"""Core runner: launches a Chromium browser, lets a platform module drive
navigation, and captures every JSON response. Each captured JSON is fed
through the platform's parser. Result: structured product records with
real MRP/sale prices straight from the platform's own API."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Protocol

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Response,
    TimeoutError as PWTimeout,
    sync_playwright,
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class Product:
    platform: str
    category: str
    query: str
    brand: str
    product_name: str
    size: str
    mrp: float | None
    sale_price: float | None
    discount_pct: float
    in_stock: bool | None
    sku_id: str
    url: str
    pincode: str
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def as_row(self) -> dict:
        d = asdict(self)
        return d


COLUMNS = [
    "scraped_at", "platform", "category", "query", "brand",
    "product_name", "size", "mrp", "sale_price", "discount_pct",
    "in_stock", "sku_id", "url", "pincode",
]


# ---------------------------------------------------------------------------
# Platform interface (duck typing — just implement these on your module)
# ---------------------------------------------------------------------------

class Platform(Protocol):
    NAME: str
    BASE_URL: str

    def setup(self, page: Page, pincode: str, debug: bool) -> bool: ...

    def search_urls(self, query: str) -> Iterable[str]: ...

    def is_product_response(self, url: str) -> bool: ...

    def parse(
        self,
        body: dict | list,
        url: str,
        category: str,
        query: str,
        brands: list[str],
        pincode: str,
    ) -> list[Product]: ...


# ---------------------------------------------------------------------------
# Generic helpers used by all platforms
# ---------------------------------------------------------------------------

def match_brand(text: str, brands: list[str]) -> str | None:
    low = text.lower()
    for b in brands:
        if b.lower() in low:
            return b
    return None


def safe_float(x) -> float | None:
    if x is None:
        return None
    try:
        if isinstance(x, str):
            x = x.replace("₹", "").replace(",", "").strip()
        return float(x)
    except (ValueError, TypeError):
        return None


def discount_pct(mrp: float | None, sale: float | None) -> float:
    if mrp and sale and mrp > sale:
        return round((mrp - sale) / mrp * 100, 2)
    return 0.0


# ---------------------------------------------------------------------------
# Generic JSON walker: recursively yield every dict in a nested JSON body.
# Platform parsers use this to find product records without caring about
# the exact wrapper structure.
# ---------------------------------------------------------------------------

def walk_dicts(obj) -> Iterable[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_dicts(v)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class ResponseRecorder:
    """Collects JSON response bodies that match a platform's predicate."""

    def __init__(self, predicate: Callable[[str], bool], debug_dir: Path | None):
        self.predicate = predicate
        self.debug_dir = debug_dir
        self.captured: list[tuple[str, dict | list]] = []
        self._seq = 0

    def __call__(self, resp: Response) -> None:
        url = resp.url
        if not self.predicate(url):
            return
        try:
            ct = resp.headers.get("content-type", "")
            if "json" not in ct.lower():
                return
            body = resp.json()
        except Exception:
            return
        self.captured.append((url, body))
        if self.debug_dir is not None:
            self._seq += 1
            fname = self.debug_dir / f"resp_{self._seq:04d}.json"
            try:
                fname.write_text(json.dumps(body, indent=2)[:200_000], encoding="utf-8")
            except Exception:
                pass

    def drain(self) -> list[tuple[str, dict | list]]:
        out = list(self.captured)
        self.captured.clear()
        return out


def run_platform(
    platform: Platform,
    queries_by_category: dict[str, list[str]],
    brands: list[str],
    pincode: str,
    headless: bool = True,
    slow_mo_ms: int = 0,
    debug: bool = False,
    per_query_scrolls: int = 8,
    per_query_wait_s: float = 2.5,
    profile_dir: str | None = None,
    setup_only: bool = False,
    on_query_done: "Callable[[str, str, str, list[Product]], None] | None" = None,
) -> list[Product]:
    """Drive one platform end-to-end and return parsed products.

    profile_dir: when set, use a persistent Chromium profile so cookies
        and location selections survive across runs. Strongly recommended
        for platforms with location modals (DMart / BigBasket / Zepto).
    setup_only: open the platform, run setup, then pause for manual UI
        interaction (you click the location, dismiss modals). When you close
        the browser the profile saves. Use this once per platform.
    """
    debug_dir: Path | None = None
    if debug:
        debug_dir = Path("data/debug") / platform.NAME.lower()
        debug_dir.mkdir(parents=True, exist_ok=True)

    products: list[Product] = []

    with sync_playwright() as pw:
        ctx: BrowserContext
        if profile_dir:
            pdir = Path(profile_dir) / platform.NAME.lower()
            pdir.mkdir(parents=True, exist_ok=True)
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(pdir),
                headless=headless,
                slow_mo=slow_mo_ms,
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            browser = None  # persistent context owns its own browser
        else:
            browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
            ctx = browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
        page: Page = ctx.pages[0] if ctx.pages else ctx.new_page()

        recorder = ResponseRecorder(platform.is_product_response, debug_dir)
        page.on("response", recorder)

        applied = platform.setup(page, pincode, debug)
        print(f"  [{platform.NAME}] pincode applied: {applied}", flush=True)

        if setup_only:
            print(f"  [{platform.NAME}] SETUP MODE — set the location in the browser,")
            print(f"  then close the browser window. Profile will be saved.")
            try:
                page.wait_for_event("close", timeout=600_000)
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass
            if browser is not None:
                browser.close()
            return []

        for category, queries in queries_by_category.items():
            for q in queries:
                print(f"  [{platform.NAME}] [{category}] {q!r} ...", flush=True)
                recorder.drain()
                count_before = len(products)
                for url in platform.search_urls(q):
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    except PWTimeout:
                        print(f"    nav timeout: {url}", flush=True)
                        continue
                    # Wait for JSON responses to arrive + scroll for lazy load.
                    time.sleep(per_query_wait_s)
                    for _ in range(per_query_scrolls):
                        try:
                            page.mouse.wheel(0, 2200)
                        except Exception:
                            pass
                        time.sleep(0.5)
                # ALSO grab Next.js / Nuxt / Apollo initial state from the
                # page itself — many SPAs (Zepto, BigBasket) render products
                # into the HTML via SSR rather than a separate XHR call.
                next_data = None
                try:
                    next_data = page.evaluate(
                        "() => window.__NEXT_DATA__ || window.__NUXT__ "
                        "|| window.__APOLLO_STATE__ || window.__INITIAL_STATE__ || null"
                    )
                except Exception:
                    pass
                if next_data and debug_dir is not None:
                    try:
                        import json as _json
                        (debug_dir / f"nextdata_{len(list(debug_dir.glob('nextdata_*.json')))+1:04d}.json").write_text(
                            _json.dumps(next_data, indent=2)[:400_000], encoding="utf-8"
                        )
                    except Exception:
                        pass
                captured = recorder.drain()
                if next_data is not None:
                    captured.append((page.url, next_data))
                hit = 0
                for u, body in captured:
                    try:
                        parsed = platform.parse(body, u, category, q, brands, pincode)
                    except Exception as e:
                        if debug:
                            print(f"    parse error on {u}: {e}", flush=True)
                        continue
                    hit += len(parsed)
                    products.extend(parsed)

                # DOM extraction fallback: if the platform defines extract_dom,
                # let it scrape products directly from the rendered page.
                dom_hit = 0
                if hasattr(platform, "extract_dom"):
                    try:
                        dom_products = platform.extract_dom(
                            page, category, q, brands, pincode
                        )
                        dom_hit = len(dom_products)
                        products.extend(dom_products)
                    except Exception as e:
                        if debug:
                            print(f"    dom extract error: {e}", flush=True)
                print(
                    f"    captured {len(captured)} sources, +{hit} from JSON, "
                    f"+{dom_hit} from DOM",
                    flush=True,
                )
                # Live callback: hand the freshly-scraped products to the
                # caller (dashboard appends them to history.csv immediately).
                if on_query_done is not None:
                    new = products[count_before:]
                    try:
                        on_query_done(platform.NAME, category, q, new)
                    except Exception as e:
                        if debug:
                            print(f"    on_query_done error: {e}", flush=True)

        try:
            ctx.close()
        except Exception:
            pass
        if browser is not None:
            browser.close()

    # Dedupe on (platform, sku_id || url)
    out: dict[tuple, Product] = {}
    for p in products:
        key = (p.platform, p.sku_id or p.url)
        # If we already have one, prefer the cheaper / fresher record.
        prev = out.get(key)
        if prev is None or (p.sale_price and (prev.sale_price is None or p.sale_price < prev.sale_price)):
            out[key] = p
    return list(out.values())
