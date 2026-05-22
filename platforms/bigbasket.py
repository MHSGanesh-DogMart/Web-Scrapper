"""BigBasket adapter.

BigBasket's listing service responds to URLs like:
    https://www.bigbasket.com/listing-svc/v2/products?...
and search uses /ps/?q=... in the SPA. Their JSON includes fields
like 'desc', 'mrp', 'sp' (selling price), 'sku', 'absolute_url', and
nested 'pricing' / 'children' arrays for variants.

We accept any *.bigbasket.com JSON that looks productish and parse with
the same key-detection approach as DMart.
"""
from __future__ import annotations

import time
from typing import Iterable
from urllib.parse import quote_plus

from playwright.sync_api import Page, TimeoutError as PWTimeout

from core import (
    Product,
    discount_pct,
    match_brand,
    safe_float,
    walk_dicts,
)


NAME = "BigBasket"
BASE_URL = "https://www.bigbasket.com"


# Pincode -> BigBasket city slug. /cl/<slug>/ is a deep-link that sets
# the city cookie without any modal interaction.
_PIN_TO_CITY = {
    "500": "hyderabad",   # Hyderabad
    "560": "bangalore",   # Bangalore
    "600": "chennai",     # Chennai
    "400": "mumbai",      # Mumbai
    "411": "pune",        # Pune
    "110": "delhi",       # Delhi
    "201": "noida",       # Noida
    "122": "gurgaon",     # Gurgaon
    "700": "kolkata",     # Kolkata
    "380": "ahmedabad",   # Ahmedabad
}


def _city_for_pincode(pincode: str) -> str:
    prefix = pincode[:3]
    return _PIN_TO_CITY.get(prefix, "hyderabad")


def setup(page: Page, pincode: str, debug: bool) -> bool:
    """Skip BigBasket's location modal by visiting the city deep-link.
    /cl/<city>/ sets the city cookie server-side; subsequent /ps/ search
    calls return real product listings."""
    city = _city_for_pincode(pincode)
    page.goto(f"{BASE_URL}/cl/{city}/", wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    time.sleep(2)
    # Dismiss the address-confirmation popup if it appears.
    for sel in (
        "button:has-text('Confirm')",
        "button:has-text('Yes')",
        "button:has-text('Continue')",
        "[aria-label='Close']",
    ):
        try:
            page.locator(sel).first.click(timeout=2000)
            time.sleep(1)
            break
        except Exception:
            continue
    return True


def search_urls(query: str) -> Iterable[str]:
    yield f"{BASE_URL}/ps/?q={quote_plus(query)}"


def is_product_response(url: str) -> bool:
    """Capture BigBasket listing-svc API responses (require auth session)."""
    return "listing-svc" in url and "products" in url


_NAME_KEYS = ("desc", "p_desc", "name", "productName", "title")
_BRAND_KEYS = ("brand", "brand_name", "brandName", "p_brand")
_MRP_KEYS = ("mrp", "p_mrp", "MRP", "max_price")
_SALE_KEYS = ("sp", "p_sp", "sellingPrice", "selling_price", "price", "discounted_price")
_SKU_KEYS = ("sku", "id", "p_id", "child_sku", "skuId")
_SIZE_KEYS = ("w", "weight", "unit_desc", "uom", "pack_desc", "variant")
_URL_KEYS = ("absolute_url", "url", "slug")
_STOCK_KEYS = ("availability", "available", "in_stock", "is_available")


def _first(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _looks_like_product(d):
    return any(k in d for k in _NAME_KEYS) and any(k in d for k in _MRP_KEYS + _SALE_KEYS)


def parse(body, url, category, query, brands, pincode) -> list[Product]:
    out: list[Product] = []
    seen: set[str] = set()
    for d in walk_dicts(body):
        if not _looks_like_product(d):
            continue
        name = str(_first(d, _NAME_KEYS) or "").strip()
        if not name or len(name) < 8 or len(name.split()) < 2:
            continue
        brand_field = str(_first(d, _BRAND_KEYS) or "").strip()
        brand = match_brand(brand_field, brands) or match_brand(name, brands)
        if not brand:
            continue
        mrp = safe_float(_first(d, _MRP_KEYS))
        sale = safe_float(_first(d, _SALE_KEYS))
        if sale is None and mrp is not None:
            sale = mrp
        if mrp is None and sale is not None:
            mrp = sale
        if mrp is None and sale is None:
            continue
        sku = str(_first(d, _SKU_KEYS) or "").strip()
        if sku and sku in seen:
            continue
        if sku:
            seen.add(sku)
        size = str(_first(d, _SIZE_KEYS) or "").strip()
        slug = str(_first(d, _URL_KEYS) or "").strip()
        prod_url = (
            slug if slug.startswith("http")
            else f"{BASE_URL}{slug}" if slug.startswith("/")
            else url
        )
        stock_raw = _first(d, _STOCK_KEYS)
        if isinstance(stock_raw, bool):
            in_stock = stock_raw
        elif isinstance(stock_raw, str):
            in_stock = stock_raw.lower() in ("available", "instock", "in_stock", "true")
        elif isinstance(stock_raw, (int, float)):
            in_stock = stock_raw > 0
        else:
            in_stock = None
        out.append(Product(
            platform=NAME,
            category=category,
            query=query,
            brand=brand,
            product_name=name,
            size=size,
            mrp=mrp,
            sale_price=sale,
            discount_pct=discount_pct(mrp, sale),
            in_stock=in_stock,
            sku_id=sku,
            url=prod_url,
            pincode=pincode,
        ))
    return out


import re as _re
_SIZE_RE = _re.compile(
    r"(\d+(?:\.\d+)?)\s*(ml|ltr|litre|liter|l\b|kg|gm|gms|g\b|pack|pcs|pieces|units?|u\b)",
    _re.IGNORECASE,
)
_CAT_KWS = [
    ("milk",   ["milk", "toned", "full cream", "skimmed"]),
    ("curd",   ["curd", "dahi", "yogurt"]),
    ("paneer", ["paneer"]),
    ("cheese", ["cheese"]),
    ("butter", ["butter"]),
    ("ghee",   ["ghee"]),
]


def _guess_category(title: str) -> str:
    low = title.lower()
    for cat, kws in _CAT_KWS:
        if any(k in low for k in kws):
            return cat
    return "other"


def extract_dom(page, category: str, query: str, brands: list, pincode: str) -> list:
    """DOM fallback for BigBasket — auto-detects and reads product cards."""
    import time
    from auto_extract import extract_cards
    from core import Product, discount_pct, match_brand, safe_float

    for _ in range(8):
        try:
            page.mouse.wheel(0, 2500)
        except Exception:
            pass
        time.sleep(0.4)

    results = extract_cards(page, NAME, BASE_URL, category, query, brands, pincode)

    # Fallback: if auto-detect found nothing, use known BigBasket card selectors
    if not results:
        sel = None
        for s in (
            "li[qa]", "div[qa]",
            "div[class*='SKUDeck']", "div[class*='product-sub']",
            "div[class*='PriceBox']",
            "[class*='product-card']",
        ):
            if page.locator(s).count() >= 2:
                sel = s
                break

        if not sel:
            return []

        cards = page.locator(sel).all()
        out:  list = []
        seen: set[str] = set()

        for card in cards:
            try:
                text = (card.inner_text() or "").strip()
                if len(text) < 5:
                    continue
                key = text[:50]
                if key in seen:
                    continue
                seen.add(key)

                lines = [l.strip() for l in text.splitlines() if l.strip()]
                name  = ""
                prices: list[float] = []

                for line in lines:
                    low = line.lower()
                    if any(s in low for s in ["add", "out of stock", "notify", "off", "save", "%"]):
                        p = safe_float(line.replace("₹","").replace("Rs","").replace(",","").strip())
                        if p and p > 0 and ("₹" in line or "Rs" in line):
                            prices.append(p)
                        continue
                    if "₹" in line or "Rs" in line:
                        p = safe_float(line.replace("₹","").replace("Rs","").replace(",","").strip())
                        if p and p > 0:
                            prices.append(p)
                    elif not name and len(line) > 4 and not _re.match(r"^[\d.%₹]+$", line):
                        name = line

                if not name:
                    continue
                brand = match_brand(name, brands)
                if not brand:
                    continue

                sale = min(prices) if prices else None
                mrp  = max(prices) if prices else None

                size_m = _SIZE_RE.search(name)
                size   = size_m.group(0).strip() if size_m else ""

                try:
                    href = card.locator("a[href]").first.get_attribute("href") or ""
                except Exception:
                    href = ""
                prod_url = (BASE_URL + href) if href.startswith("/") else href or BASE_URL

                out.append(Product(
                    platform=NAME,
                    category=_guess_category(name),
                    query=query,
                    brand=brand,
                    product_name=name,
                    size=size,
                    mrp=mrp,
                    sale_price=sale,
                    discount_pct=discount_pct(mrp, sale),
                    in_stock=None,
                    sku_id=href.split("/")[-2] if "/" in href else "",
                    url=prod_url,
                    pincode=pincode,
                ))
            except Exception:
                continue
        results = out

    return results
