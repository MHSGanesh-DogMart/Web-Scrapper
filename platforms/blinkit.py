"""Blinkit adapter.

Blinkit is a pure JavaScript SPA built with Tailwind CSS.
There are no product anchor tags — product cards have no href.
Product data comes entirely from DOM extraction.

Card structure (observed 2025):
    Ancestor div[3] of the price element contains:
        <Product Name>
        <Size / Weight>
        ₹<sale_price>
        [₹<mrp>  (only when discounted)]
        ADD / OUT OF STOCK

Price elements use Tailwind class: div.tw-text-200.tw-font-semibold
"""
from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import quote_plus

from playwright.sync_api import Page, TimeoutError as PWTimeout

from core import Product, discount_pct, match_brand, safe_float


NAME     = "Blinkit"
BASE_URL = "https://blinkit.com"

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

# Price div selectors in priority order
_PRICE_SELS = [
    "div.tw-text-200.tw-font-semibold",   # current (2025)
    "[class*='tw-text'][class*='tw-font-semibold']",
    "div[class*='Product__price']",
    "div[class*='product-price']",
    "[data-testid*='price']",
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


def _find_price_sel(page) -> str | None:
    for s in _PRICE_SELS:
        if page.locator(s).count() >= 3:
            return s
    return None


def _find_card_level(page, price_sel: str) -> int:
    """Auto-detect which ancestor div level gives exactly 1 price per card."""
    try:
        first = page.locator(price_sel).first
        for lvl in range(2, 8):
            anc   = first.locator(f"xpath=ancestor::div[{lvl}]")
            inner = anc.locator(price_sel).count()
            text  = anc.inner_text()
            if inner == 1 and len(text) > 10:
                return lvl
    except Exception:
        pass
    return 3


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup(page: Page, pincode: str, debug: bool) -> bool:
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
    except Exception:
        pass
    return True


# ── Navigation ────────────────────────────────────────────────────────────────

def search_urls(query: str) -> Iterable[str]:
    yield f"{BASE_URL}/s/?q={quote_plus(query)}"


# ── JSON (not used — Blinkit has no interceptable product API) ────────────────

def is_product_response(url: str) -> bool:
    return False


def parse(body, url, category, query, brands, pincode) -> list[Product]:
    return []


# ── DOM extraction ────────────────────────────────────────────────────────────

def extract_dom(page: Page, category: str, query: str, brands: list[str], pincode: str) -> list[Product]:
    """Scrape product cards from the rendered Blinkit page."""
    from auto_extract import extract_cards

    for _ in range(7):
        try:
            page.mouse.wheel(0, 2500)
        except Exception:
            pass
        time.sleep(0.45)

    results = extract_cards(page, NAME, BASE_URL, category, query, brands, pincode)

    # Blinkit is a React SPA with no <a href> on product cards, so auto_extract
    # returns base_url for every product. Replace with a working search URL.
    for p in results:
        if not p.url or p.url == BASE_URL:
            p.url = f"{BASE_URL}/s/?q={quote_plus(p.product_name)}"

    # Fallback: if auto-detect found nothing, use known Tailwind price selector
    if not results:
        price_sel = _find_price_sel(page)
        if not price_sel:
            return []

        card_lvl   = _find_card_level(page, price_sel)
        price_divs = page.locator(price_sel).all()
        out:  list[Product] = []
        seen: set[str]      = set()

        for pd in price_divs:
            try:
                card = pd.locator(f"xpath=ancestor::div[{card_lvl}]")
                text = (card.inner_text() or "").strip()
                if len(text) < 5:
                    continue
                dedup_key = text[:60]
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                lines = [l.strip() for l in text.splitlines() if l.strip()]
                prices: list[float] = []
                name = ""

                for line in lines:
                    low = line.lower()
                    if any(s in low for s in ["add", "out of stock", "notify", "off", "save"]):
                        continue
                    if "₹" in line:
                        p = safe_float(line.replace("₹", "").replace(",", "").strip())
                        if p and p > 0:
                            prices.append(p)
                    elif not name and len(line) > 3 and not re.match(r"^[\d.%]+$", line):
                        name = line

                if not name:
                    continue
                brand = match_brand(name, brands)
                if not brand:
                    continue

                sale = min(prices) if prices else None
                mrp  = max(prices) if prices else None

                # Blinkit is a React SPA — product cards have no <a href>.
                # Use a search URL so clicking opens the right product page.
                prod_url = f"{BASE_URL}/s/?q={quote_plus(name)}"
                out.append(Product(
                    platform=NAME,
                    category=_guess_category(name),
                    query=query,
                    brand=brand,
                    product_name=name,
                    size=_extract_size(name),
                    mrp=mrp,
                    sale_price=sale,
                    discount_pct=discount_pct(mrp, sale),
                    in_stock=None,
                    sku_id="",
                    url=prod_url,
                    pincode=pincode,
                ))
            except Exception:
                continue
        results = out

    return results
