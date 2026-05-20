# Dairy Competitor Price Monitor — Full Documentation

## What This System Does

Automatically scrapes competitor dairy product prices from **DMart**, **Zepto**, and **Blinkit**,
stores them in CSV + Excel, and shows them in a live web dashboard with filters, charts, and alerts.

Built for Dodla Dairy to track: Amul, Heritage, Nandini, Mother Dairy, Milky Mist, and others.

---

## Project File Structure

```
D:\Web-Scrapper\
│
├── dashboard.py          ← Web dashboard (Streamlit)
├── run.py                ← Command-line runner for all platforms
├── config.yaml           ← All settings: pincode, brands, queries
│
├── dmart_scraper.py      ← DMart scraper
├── zepto_scraper.py      ← Zepto scraper
├── blinkit_scraper.py    ← Blinkit scraper
│
├── scraper_base.py       ← Shared selector auto-detection system
├── selector_monitor.py   ← Daily health check scheduler
├── storage.py            ← Saves data to CSV + Excel
│
├── data/
│   ├── history.csv           ← All scraped data ever (appended each run)
│   ├── health.json           ← Platform health status (shown in dashboard)
│   ├── selector_state.json   ← Last-known-good selector per platform
│   ├── health_history.csv    ← Daily health check log
│   └── snapshots/
│       └── dmart_dairy_YYYYMMDD_HHMMSS.xlsx   ← One Excel file per run
│
├── requirements.txt
└── .venv/                ← Python virtual environment
```

---

## How to Run

### Start the Dashboard (recommended)
```bash
.venv\Scripts\streamlit.exe run dashboard.py
```
Then open: http://localhost:8501

### Run scraper from command line
```bash
# All platforms
.venv\Scripts\python.exe run.py

# Specific platforms only
.venv\Scripts\python.exe run.py dmart
.venv\Scripts\python.exe run.py zepto blinkit
```

---

## Configuration (config.yaml)

```yaml
pincode: "500032"       # Your Hyderabad delivery pincode (for DMart)

brands:                 # Which brands to track
  - Amul
  - Heritage
  - Nandini
  - Dodla
  - Mother Dairy
  - Milky Mist
  - ...

output:
  snapshot_dir: data/snapshots     # Excel output folder
  history_csv:  data/history.csv   # Master CSV history

headless: true          # false = shows browser window (useful for debugging)
slow_mo_ms: 0           # Add delay between browser actions (ms)
nav_timeout_ms: 45000   # Max wait time per page load (ms)
```

---

## Platform Details

### DMart
- **URL**: `https://www.dmart.in/category/dairy-aesc-dairy`
- **How it works**: Opens the full Dairy category page, scrolls down to load all product tiles, finds product links via `a[href*='/product/']`
- **Pincode**: Changes which store's stock and prices you see. Set it in `config.yaml` or in the dashboard sidebar.
- **Products found**: ~56 dairy products per run

### Zepto
- **URL**: `https://www.zepto.com/search?query=<query>`
- **How it works**: Searches each dairy category (milk, curd, paneer, butter, ghee, cheese). Zepto renders a grid of 6 products per row. Product name comes from the `<img alt="">` attribute; price comes from the container text block parsed by splitting on "ADD" button text.
- **Pincode**: Zepto uses your browser's saved location, not a pincode. It shows Hyderabad prices by default.
- **Products found**: ~220 per full run

### Blinkit
- **URL**: `https://blinkit.com/s/?q=<query>`
- **How it works**: Blinkit uses Tailwind CSS classes (no descriptive class names). The scraper locates price `<div>` elements by their CSS class, then walks 3 ancestor levels up to get the full product card. Card text format: `Name → Size → ₹Price → ADD`.
- **Note**: Blinkit is a pure JavaScript SPA — product cards have no `<a href>` links. Product URL is generated from the product name.
- **Products found**: ~88 per full run

---

## The Self-Healing Selector System

This is the most important part — it solves the problem of websites changing their HTML.

### The Problem
Every few weeks/months, websites redesign their pages. When they change the CSS classes or HTML structure, selectors like `a[href*='/p/']` stop working and the scraper gets 0 results.

**Example**: DMart changed their product URL format from `/p/product-name` to `/product/product-name` in 2024. The scraper returned 0 results until the selector was updated.

### How the Auto-Healing Works

Each platform defines a **list of selectors in priority order**:

```python
# DMart (dmart_scraper.py)
SELECTOR_STRATEGIES = [
    "a[href*='/product/']",      # current format (2025)
    "a[href*='/p/']",            # old format (2024)
    "[data-testid*='product'] a",
    "div[class*='product'] a",
    "div[class*='Product'] a",
    "article a",
]
```

Before every scrape, `scraper_base.detect_working_selector()` runs this check:

```
For each selector in the list:
    Count how many elements it finds on the page
    If count >= 3:
        → USE this selector, save it to selector_state.json
        → Update health.json status = "ok"
        → STOP checking further selectors
If all fail:
    → Write to health.json status = "broken"
    → Show red alert in dashboard sidebar
    → Return 0 products (but don't crash)
```

### What Gets Saved

**`data/selector_state.json`** — remembers the last working selector:
```json
{
  "DMart":   { "selector": "a[href*='/product/']", "count": 184, "updated_at": "2025-05-20T04:12:01" },
  "Zepto":   { "selector": "a[href*='/pn/']",      "count": 57,  "updated_at": "2025-05-20T04:12:01" },
  "Blinkit": { "selector": "div.tw-text-200.tw-font-semibold", "count": 60, "updated_at": "2025-05-20T04:12:01" }
}
```

**`data/health.json`** — shown as green/red badges in the dashboard:
```json
{
  "DMart":   { "status": "ok",     "selector": "a[href*='/product/']", "count": 184 },
  "Zepto":   { "status": "ok",     "selector": "a[href*='/pn/']",      "count": 57 },
  "Blinkit": { "status": "broken", "selector": null, "count": 0,
               "note": "All selectors returned 0 results. Selector update needed." }
}
```

### Daily Automated Health Check

Run once to set up a Windows Scheduled Task (runs every day at 6 AM):
```bash
.venv\Scripts\python.exe selector_monitor.py --setup
```

Or run the health check manually anytime:
```bash
.venv\Scripts\python.exe selector_monitor.py
```

Output:
```
============================================================
Selector Health Check  —  2025-05-20T06:00:00
============================================================
[DMart]   checking https://www.dmart.in/category/dairy-aesc-dairy ...
  ✓  selector="a[href*='/product/']"  count=184
[Zepto]   checking https://www.zepto.com/search?query=milk ...
  ✓  selector="a[href*='/pn/']"  count=57
[Blinkit] checking https://blinkit.com/s/?q=milk ...
  ✓  selector="div.tw-text-200.tw-font-semibold"  count=60
All platforms OK.
============================================================
```

---

## How Prices Are Parsed

### DMart
Card text (raw):
```
Amul Taaza Toned Milk : 1 Litre x 12 Units
MRP
₹ 924
DMart
₹ 894
(Inclusive of all taxes)
₹ 30
OFF
1 L x 12 U
ADD TO CART
```
Parsing: extract all ₹ values → `max = MRP (924)`, `min = Sale Price (894)`, `discount = (924-894)/924 * 100 = 3.25%`

### Zepto
The search results page is a grid of 6 products per row. Container text (split by "ADD"):
```
Block 0: ADD  ₹77  Amul Taaza Toned Milk (1L)  1 pack (1 L)  4.8  (12k)
Block 1: ₹18  ₹20  ₹2 OFF  Smoodh Drink  150ml  4.6
Block 2: ₹83  ₹90  ₹7 OFF  Amul Moti Milk  1L
...
```
- Product name = `<img alt="">` attribute (inside the product anchor)
- Price = first ₹ value in that block
- MRP = largest ₹ value (if different from sale price)
- Discount amount lines (₹2, ₹7) filtered out as they're < ₹5

### Blinkit
Card text (3 ancestor levels from price div):
```
Amul Gold Full Cream Milk
1 ltr
₹72
ADD
```
Parsing: line 1 = name, line 2 = size, line 3 = price. MRP is only shown when there's a discount (strikethrough styling, separate line).

---

## Dashboard Guide

Open http://localhost:8501

### Sidebar
| Control | What it does |
|---------|-------------|
| Pincode | Change your delivery location for DMart |
| Platform checkboxes | Choose which platforms to scrape |
| Run Scraper Now | Scrapes selected platforms, saves data, refreshes dashboard |
| Brand filter | Show only selected brands in table and charts |
| Category filter | Show only milk / curd / paneer / butter / ghee |
| Platform filter | Show only DMart / Zepto / Blinkit |
| Platform Health | Green = working, Red = selector broken |
| Run Health Check | Test all platforms right now |

### Tabs
| Tab | What you see |
|-----|-------------|
| Latest Prices | Full table: Brand, Product, Size, MRP, Sale Price, Discount %, Platform. Download as CSV. |
| Price Chart | Horizontal bar chart showing sale prices colored by platform |
| Compare Brands | Grouped bar chart — compare average prices across brands and platforms side by side |
| Price History | Line chart showing price changes over time (need 2+ scrape runs) |

---

## How to Add a New Platform (e.g. Swiggy Instamart, Amazon)

1. Create `swiggy_scraper.py` using this template:

```python
from __future__ import annotations
import re, time
from datetime import datetime
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright
import scraper_base

PLATFORM = "Swiggy"
BASE     = "https://www.swiggy.com/instamart"

# Add selectors from most specific to most general
SELECTOR_STRATEGIES = [
    "div[class*='product-card']",
    "a[href*='/product']",
    # add more fallbacks
]

DAIRY_QUERIES = ["milk", "curd", "paneer", "butter", "ghee"]

def scrape(config: dict) -> list[dict]:
    brands   = config["brands"]
    headless = bool(config.get("headless", True))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(...)
        page = ctx.new_page()

        # Detect selector
        page.goto(f"{BASE}/search?query=milk", ...)
        sel = scraper_base.detect_working_selector(page, PLATFORM, SELECTOR_STRATEGIES)
        if not sel:
            browser.close()
            return []

        # Scrape each query ...
        browser.close()
    return rows
```

2. Add it to `run.py`:
```python
import swiggy_scraper
SCRAPERS = {
    "dmart":   dmart_scraper.scrape,
    "zepto":   zepto_scraper.scrape,
    "blinkit": blinkit_scraper.scrape,
    "swiggy":  swiggy_scraper.scrape,   # add this
}
```

3. Add it to `selector_monitor.py` CHECKS dict:
```python
"Swiggy": {
    "url":        "https://www.swiggy.com/instamart/search?query=milk",
    "strategies": swiggy_scraper.SELECTOR_STRATEGIES,
},
```

4. Add the checkbox to `dashboard.py` sidebar.

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| 0 products from a platform | Selector broke (site redesigned HTML) | Click "Run Health Check" in dashboard, or run `python selector_monitor.py` — it will find the new selector |
| "No data yet" in dashboard | Scraper never ran | Click "Run Scraper Now" in sidebar |
| Blinkit shows 0 ghee/cheese | Those products have different page structure | Blinkit may not carry those brands — not a bug |
| DMart shows wrong prices | Wrong pincode | Update pincode in sidebar before running |
| Browser window opens | headless is false | Set `headless: true` in config.yaml |
| Scraper is slow | Many queries | Normal — each page takes 5-10s. Full run takes 5-8 minutes |

---

## Technology Stack

| Tool | Purpose |
|------|---------|
| Python 3.12 | Main language |
| Playwright | Browser automation — opens real Chrome, renders JS |
| Streamlit | Web dashboard |
| Pandas | Data handling, CSV/Excel |
| Altair | Charts |
| PyYAML | Config file parsing |
| OpenPyXL | Excel file writing |

---

## Data Schema (CSV columns)

| Column | Example | Description |
|--------|---------|-------------|
| scraped_at | 2025-05-20T04:12:01 | Timestamp of when data was collected |
| platform | DMart | Which website |
| category | milk | Dairy category |
| query | dairy | Search query used |
| brand | Amul | Matched brand name |
| product_name | Amul Taaza Toned Milk | Full product title |
| size | 1 L | Size/weight extracted from name |
| mrp | 83.0 | Maximum Retail Price |
| sale_price | 79.0 | Actual price on platform |
| discount_pct | 4.82 | Discount percentage |
| url | https://www.dmart.in/product/... | Product page URL |
| pincode | 500032 | Delivery pincode used |

---

*Last updated: May 2025*
