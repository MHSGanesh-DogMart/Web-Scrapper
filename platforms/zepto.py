"""Zepto adapter.

Zepto's search API is at:
    https://bff-gateway.zepto.com/user-search-service/api/v3/search

Real schema (observed 2025):
    layout[*].data.resolver.data.items[*].productResponse
        product.name                 — product name
        product.brand                — brand string
        productVariant.mrp           — MRP in PAISE (₹85 = 8500)
        productVariant.formattedPacksize  — "1 pack (1 L)"
        discountedSellingPrice       — sale price in PAISE
        outOfStock                   — bool
        id                           — variant UUID

All money fields are in PAISE — divide by 100.

DOM fallback also extracts from rendered tiles when the API capture misses.
"""
from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import quote_plus

from playwright.sync_api import Page, TimeoutError as PWTimeout

from core import Product, discount_pct, match_brand, safe_float, walk_dicts


NAME     = "Zepto"
BASE_URL = "https://www.zepto.com"

_PRICE_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_SIZE_RE  = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ml|ltr|litre|liter|l\b|kg|gm|gms|g\b|pack|pcs|pieces|units?|u\b)",
    re.IGNORECASE,
)
_CAT_KWS = [
    ("milk",   ["milk", "toned", "full cream", "skimmed", "standardised"]),
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


def _paise_to_rupees(v) -> float | None:
    n = safe_float(v)
    return None if n is None else round(n / 100.0, 2)


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup(page: Page, pincode: str, debug: bool) -> bool:
    """Just open Zepto home — location is set via persistent profile."""
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
    except Exception:
        pass
    return True


# ── Navigation ────────────────────────────────────────────────────────────────

def search_urls(query: str) -> Iterable[str]:
    yield f"{BASE_URL}/search?query={quote_plus(query)}"


# ── JSON capture ──────────────────────────────────────────────────────────────

def is_product_response(url: str) -> bool:
    """Capture Zepto's BFF search API."""
    return "bff-gateway.zepto.com" in url or "user-search-service" in url


# ── JSON parse ────────────────────────────────────────────────────────────────

def parse(body, url, category, query, brands, pincode) -> list[Product]:
    out:  list[Product] = []
    seen: set[str]      = set()

    for d in walk_dicts(body):
        if not isinstance(d, dict):
            continue
        prod = d.get("product")
        var  = d.get("productVariant")
        if not (isinstance(prod, dict) and isinstance(var, dict)):
            continue

        name = str(prod.get("name") or "").strip()
        if not name:
            continue
        brand_field = str(prod.get("brand") or "").strip()
        brand = match_brand(brand_field, brands) or match_brand(name, brands)
        if not brand:
            continue

        mrp  = _paise_to_rupees(var.get("mrp"))
        sale = _paise_to_rupees(d.get("discountedSellingPrice")) or \
               _paise_to_rupees(d.get("sellingPrice"))
        if sale is None and mrp is not None:
            sale = mrp
        if mrp is None and sale is not None:
            mrp = sale
        if mrp is None and sale is None:
            continue

        sku = str(var.get("id") or d.get("id") or "").strip()
        if sku and sku in seen:
            continue
        if sku:
            seen.add(sku)

        size = str(
            var.get("formattedPacksize") or _build_size(var) or ""
        ).strip()

        out_of_stock = d.get("outOfStock")
        if isinstance(out_of_stock, bool):
            in_stock: bool | None = not out_of_stock
        else:
            in_stock = None

        # Build a real product URL: zepto.com/pn/<slug>/pvid/<variant-uuid>
        # Slug comes from the API if available, otherwise derived from product name.
        slug = (
            d.get("urlSlug") or d.get("slug") or
            prod.get("urlSlug") or prod.get("slug") or
            prod.get("urlKey") or ""
        )
        if not slug:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if sku:
            prod_url = f"{BASE_URL}/pn/{slug.lstrip('/')}/pvid/{sku}"
        elif slug:
            prod_url = f"{BASE_URL}/{slug.lstrip('/')}"
        else:
            prod_url = BASE_URL

        out.append(Product(
            platform     = NAME,
            category     = category,
            query        = query,
            brand        = brand,
            product_name = name,
            size         = size,
            mrp          = mrp,
            sale_price   = sale,
            discount_pct = discount_pct(mrp, sale),
            in_stock     = in_stock,
            sku_id       = sku,
            url          = prod_url,
            pincode      = pincode,
        ))
    return out


def _build_size(variant: dict) -> str:
    ps  = variant.get("packsize")
    uom = variant.get("unitOfMeasure")
    w   = variant.get("weightInGms")
    if ps and uom:
        return f"{ps} {uom}".strip()
    if w:
        return f"{w} g"
    return ""


# ── DOM fallback ──────────────────────────────────────────────────────────────

def extract_dom(page: Page, category: str, query: str, brands: list[str], pincode: str) -> list[Product]:
    """Fallback: auto-detect and extract product tiles from the rendered Zepto page."""
    from auto_extract import extract_cards

    time.sleep(1.5)
    results = extract_cards(page, NAME, BASE_URL, category, query, brands, pincode)

    # Fallback: if auto-detect found nothing, use known /pn/ anchor pattern
    if not results:
        links = page.locator("a[href*='/pn/']").all()
        if not links:
            return []

        containers: dict[str, tuple] = {}
        for a in links:
            try:
                parent = a.locator("xpath=ancestor::div[1]")
                key    = parent.get_attribute("class") or parent.inner_html()[:30]
                if key not in containers:
                    containers[key] = (parent, [])
                containers[key][1].append(a)
            except Exception:
                pass

        out:  list[Product] = []
        seen: set[str]      = set()

        for parent, anchors in containers.values():
            try:
                container_text = parent.inner_text() or ""
            except Exception:
                continue
            blocks = re.split(r"\nADD\n", container_text)

            for i, a in enumerate(anchors):
                try:
                    href = a.get_attribute("href") or ""
                    if href in seen:
                        continue
                    seen.add(href)

                    img  = a.locator("img").first
                    name = (img.get_attribute("alt") or "").strip() if img.count() > 0 else ""
                    if not name:
                        slug = re.search(r"/pn/([^/]+)/", href)
                        if slug:
                            name = slug.group(1).replace("-", " ").title()
                    if not name:
                        continue

                    brand = match_brand(name, brands)
                    if not brand:
                        continue

                    block  = blocks[i] if i < len(blocks) else ""
                    prices = sorted(
                        [float(m.replace(",", "")) for m in re.findall(r"₹\s*([\d,]+)", block)],
                        reverse=True,
                    )
                    if prices:
                        threshold = prices[0] * 0.15
                        prices = [p for p in prices if p >= threshold]
                    top2 = prices[:2]
                    mrp  = top2[0] if top2 else None
                    sale = top2[1] if len(top2) > 1 else mrp

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
                        sku_id=href.split("/")[-1] if href else "",
                        url=BASE_URL + href if href.startswith("/") else href,
                        pincode=pincode,
                    ))
                except Exception:
                    continue
        results = out

    return results
