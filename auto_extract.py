"""auto_extract.py — Easy Scraper-style universal product card detector.

Automatically finds product cards on any page by:
1. Locating price-bearing leaf elements (₹ symbol) via DOM tree walk in JS
2. Walking DOM ancestors to find the smallest repeating container (≥3 siblings)
3. Extracting text / href / img-alt from every matching container
4. Parsing name / price / size / brand from raw card text

No hardcoded CSS classes — adapts automatically when sites redesign.
"""
from __future__ import annotations

import re
from playwright.sync_api import Page

from core import Product, discount_pct, match_brand, safe_float


_PRICE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")
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

# Runs inside the browser: walks the DOM to find repeating product card
# containers, then returns raw data (text, href, img alt) for each card.
_JS_EXTRACT_CARDS = r"""
() => {
    const PRICE_PAT = /₹[\s]*[\d,]+/;
    const MIN_SIBLINGS = 3;
    const MAX_PRICE_TEXT = 60;

    function cardKey(node) {
        if (!node) return null;
        const tag = node.tagName;
        // Prefer data-qa / data-testid as a stable key (e.g. BigBasket li[qa])
        const qa = node.getAttribute('qa') || node.getAttribute('data-qa');
        if (qa) return tag + '[qa]';
        const testid = node.getAttribute('data-testid');
        if (testid) return tag + '[data-testid]';
        // Use first two Tailwind/BEM classes (handles Blinkit utility classes)
        if (node.className && typeof node.className === 'string') {
            const first = node.className.trim().split(/\s+/).slice(0, 2).join('.');
            if (first) return tag + '.' + first;
        }
        return tag;
    }

    function countSiblings(node) {
        if (!node || !node.parentElement) return 0;
        const key = cardKey(node);
        return Array.from(node.parentElement.children)
            .filter(c => cardKey(c) === key).length;
    }

    // Collect near-leaf elements that contain a ₹ price
    const priceNodes = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
        const el = walker.currentNode;
        if (el.children.length <= 3) {
            const t = el.textContent.trim();
            if (PRICE_PAT.test(t) && t.length < MAX_PRICE_TEXT) {
                priceNodes.push(el);
            }
        }
    }
    if (priceNodes.length < MIN_SIBLINGS) return [];

    // Walk up from each price node to find the first repeating ancestor
    const candidateMap = new Map();  // key -> { siblings[], score }

    for (const pe of priceNodes.slice(0, 60)) {
        let node = pe.parentElement;
        for (let lvl = 0; lvl < 12 && node && node.tagName !== 'BODY'; lvl++) {
            const n = countSiblings(node);
            if (n >= MIN_SIBLINGS) {
                const key = cardKey(node);
                const txt = node.innerText || node.textContent || '';
                const score = n * (txt.length > 20 && txt.length < 5000 ? 2 : 1);
                const prev = candidateMap.get(key);
                if (!prev || score > prev.score) {
                    const sibs = Array.from(node.parentElement.children)
                        .filter(c => cardKey(c) === key);
                    candidateMap.set(key, { siblings: sibs, score });
                }
                break;
            }
            node = node.parentElement;
        }
    }

    if (!candidateMap.size) return [];

    // Best = most cards with reasonable text length
    const best = [...candidateMap.entries()]
        .sort((a, b) => b[1].score - a[1].score)[0];
    const cards = best[1].siblings;

    return cards.slice(0, 100).map(card => {
        const anchor = card.querySelector('a[href]');
        const img    = card.querySelector('img[alt]');
        return {
            text:    card.innerText || '',
            href:    anchor ? anchor.href : '',
            img_alt: img    ? (img.alt || '') : '',
            sku:     card.dataset.sku || card.dataset.id || card.dataset.productId || '',
        };
    });
}
"""


def _guess_category(title: str) -> str:
    low = title.lower()
    for cat, kws in _CAT_KWS:
        if any(k in low for k in kws):
            return cat
    return "other"


def _parse_prices(text: str) -> list[float]:
    vals = []
    for m in _PRICE_RE.finditer(text):
        v = safe_float(m.group(1).replace(",", ""))
        if v and v > 0:
            vals.append(v)
    return vals


def extract_cards(
    page: Page,
    platform_name: str,
    base_url: str,
    category: str,
    query: str,
    brands: list[str],
    pincode: str,
) -> list[Product]:
    """Auto-detect and extract product cards from any e-commerce page.

    Uses a JavaScript DOM tree walker to find repeating container elements
    that hold prices, then parses name/price/size from each card's text.
    No platform-specific CSS selectors required.
    """
    try:
        raw: list[dict] = page.evaluate(_JS_EXTRACT_CARDS) or []
    except Exception:
        return []

    out:  list[Product] = []
    seen: set[str]      = set()

    for card in raw:
        try:
            text = (card.get("text") or "").strip()
            href = card.get("href") or ""
            alt  = (card.get("img_alt") or "").strip()
            sku  = card.get("sku") or ""

            if not text or len(text) < 5:
                continue
            key = text[:60]
            if key in seen:
                continue
            seen.add(key)

            # Product name: img alt is cleanest (no price noise), else first
            # non-trivial text line that isn't a price or action label
            name = alt if alt and len(alt) > 4 else ""
            if not name:
                for line in text.splitlines():
                    line = line.strip()
                    low  = line.lower()
                    if (len(line) > 4
                            and not re.match(r"^[₹\d\s%,.+-]+$", line)
                            and "add" not in low
                            and "out of stock" not in low
                            and "notify" not in low):
                        name = line
                        break
            if not name:
                continue

            brand = match_brand(name, brands)
            if not brand:
                continue

            prices = _parse_prices(text)
            if prices:
                max_p = max(prices)
                # Drop values below 10% of max (discount labels like "₹12 OFF")
                prices = sorted([p for p in prices if p >= max_p * 0.10], reverse=True)

            mrp  = prices[0] if prices else None
            sale = prices[1] if len(prices) > 1 else mrp

            if not sku and href:
                parts = [p for p in href.rstrip("/").split("/") if p]
                sku = parts[-1] if parts else ""

            prod_url = (
                href if href.startswith("http")
                else (base_url + href if href.startswith("/") else base_url)
            )

            # Size: check product name first, then full card text
            size_m = _SIZE_RE.search(name) or _SIZE_RE.search(text)
            size = size_m.group(0).strip() if size_m else ""

            text_low = text.lower()
            if "out of stock" in text_low or "sold out" in text_low:
                in_stock: bool | None = False
            elif "add" in text_low:
                in_stock = True
            else:
                in_stock = None

            out.append(Product(
                platform=platform_name,
                category=_guess_category(name),
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
        except Exception:
            continue

    return out
