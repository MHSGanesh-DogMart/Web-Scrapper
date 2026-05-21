"""DMart adapter.

DMart renders product listings entirely in the browser HTML — there is no
separate product-listing JSON API call. We use the extract_dom() fallback
to scrape product cards directly from the rendered page.

Card structure (observed 2025):
    <a href="/product/<slug>?selectedProd=<id>">
        <img alt="<product name>">
    </a>
    … ancestor div[2] contains full card text:
        <Product Name>
        MRP
        ₹ <mrp>
        DMart
        ₹ <sale>
        ...
"""
from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import quote_plus

from playwright.sync_api import Page, TimeoutError as PWTimeout

from core import Product, discount_pct, match_brand, safe_float


NAME     = "DMart"
BASE_URL = "https://www.dmart.in"

_PRICE_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_SIZE_RE  = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ml|ltr|litre|liter|l\b|kg|gm|gms|g\b|pack|pcs|pieces|units?|u\b)",
    re.IGNORECASE,
)
_CAT_KWS = [
    ("milk",   ["milk", "toned", "full cream", "skimmed", "standardised", "homogenised"]),
    ("curd",   ["curd", "dahi", "yogurt", "yoghurt"]),
    ("paneer", ["paneer", "cottage cheese"]),
    ("cheese", ["cheese", "mozzarella", "cheddar"]),
    ("butter", ["butter"]),
    ("ghee",   ["ghee"]),
    ("cream",  ["cream", "malai"]),
    ("lassi",  ["lassi", "chaas", "buttermilk"]),
]


def _guess_category(title: str) -> str:
    low = title.lower()
    for cat, kws in _CAT_KWS:
        if any(k in low for k in kws):
            return cat
    return "other"


def _extract_size(title: str) -> str:
    m = _SIZE_RE.search(title)
    return m.group(0).strip() if m else ""


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup(page: Page, pincode: str, debug: bool) -> bool:
    """Go to DMart home; try to set pincode if the modal appears."""
    page.goto(BASE_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except PWTimeout:
        pass

    for sel in (
        "input[placeholder*='Pincode' i]",
        "input[name='pincode']",
        "input[aria-label*='pincode' i]",
        "input[type='tel'][maxlength='6']",
    ):
        try:
            page.wait_for_selector(sel, timeout=3000)
        except PWTimeout:
            continue
        box = page.locator(sel).first
        try:
            box.click()
            box.fill("")
            box.type(pincode, delay=80)
            time.sleep(1.2)
        except Exception:
            continue
        for sug in ("ul[role='listbox'] li", "ul li[role='option']", "ul li"):
            try:
                page.locator(sug).first.click(timeout=2000)
                time.sleep(2)
                return True
            except PWTimeout:
                continue
        try:
            box.press("Enter")
        except Exception:
            pass
        time.sleep(2)
        return True
    return False


# ── Navigation ────────────────────────────────────────────────────────────────

def search_urls(query: str) -> Iterable[str]:
    # DMart category page for dairy gives far more results than search
    yield f"{BASE_URL}/category/dairy-aesc-dairy"


# ── JSON capture (not used — DMart has no product JSON API) ───────────────────

def is_product_response(url: str) -> bool:
    return False  # all product data comes from DOM, not JSON


def parse(body, url, category, query, brands, pincode) -> list[Product]:
    return []


# ── DOM extraction ────────────────────────────────────────────────────────────

def extract_dom(page: Page, category: str, query: str, brands: list[str], pincode: str) -> list[Product]:
    """Scroll to load all tiles then auto-detect and scrape product cards."""
    from auto_extract import extract_cards

    for _ in range(14):
        try:
            page.mouse.wheel(0, 3000)
        except Exception:
            pass
        time.sleep(0.45)

    results = extract_cards(page, NAME, BASE_URL, category, query, brands, pincode)

    # Fallback: if auto-detect found nothing, try the known anchor selector
    if not results:
        sel = None
        for s in ("a[href*='/product/']", "a[href*='/p/']",
                  "[data-testid*='product'] a", "div[class*='product'] a"):
            if page.locator(s).count() >= 3:
                sel = s
                break

        if not sel:
            return []

        prod_links = page.locator(sel).all()
        seen: set[str] = set()

        for a in prod_links:
            try:
                href = a.get_attribute("href") or ""
                if not href or href in seen:
                    continue
                seen.add(href)

                parent = a.locator("xpath=ancestor::div[2]")
                text   = (parent.inner_text() or "").strip()
                if not text:
                    continue

                lines = [l.strip() for l in text.splitlines() if l.strip()]
                title = lines[0] if lines else ""
                brand = match_brand(title, brands)
                if not brand:
                    continue

                rupee_vals = sorted(
                    [float(m.replace(",", "")) for m in re.findall(r"₹\s*([\d,]+(?:\.\d+)?)", text)],
                    reverse=True,
                )
                top2 = rupee_vals[:2]
                mrp  = top2[0] if top2 else None
                sale = top2[1] if len(top2) > 1 else mrp
                if sale == mrp:
                    mrp = None

                full_url = (BASE_URL + href) if href.startswith("/") else href
                results.append(Product(
                    platform=NAME,
                    category=_guess_category(title),
                    query=query,
                    brand=brand,
                    product_name=title,
                    size=_extract_size(title),
                    mrp=mrp,
                    sale_price=sale,
                    discount_pct=discount_pct(mrp, sale),
                    in_stock=None,
                    sku_id=href.split("selectedProd=")[-1] if "selectedProd=" in href else "",
                    url=full_url,
                    pincode=pincode,
                ))
            except Exception:
                continue

    return results
