# Dodla Competitor Price Scraper

Free, multi-platform dairy price tracker for DMart, BigBasket, and Zepto.

**How it gets accurate data without paying anyone:** instead of guessing
prices from HTML, it lets Chromium browse the site normally and captures
each platform's own JSON API responses (the same ones the React app uses
to render the page). Those JSONs contain real MRP / sale_price / SKU / pack
size fields, so we read structured data, not text.

## Setup (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

If `playwright install chromium` fails with "Access denied" / DLL locked,
**fully quit Chrome and Edge** (check the system tray) and retry, or run
PowerShell as Administrator.

## Run

```powershell
python run.py
```

First run: keep `debug: true` and `headless: false` in `config.yaml` so you
can see the browser and inspect captured JSON in `data/debug/`.

## Outputs

Each run writes three files into `data/snapshots/`:
- `prices_<timestamp>.xlsx` — every product captured this run
- `comparison_<timestamp>.xlsx` — pivot table: cheapest platform per (brand, size)
- `history.csv` (in `data/`) — appended every run for trend analysis

## How to add a new platform

Create `platforms/<name>.py` with these module-level functions:

```python
NAME = "Foo"
BASE_URL = "https://www.foo.com"

def setup(page, pincode, debug) -> bool: ...
def search_urls(query) -> Iterable[str]: ...
def is_product_response(url) -> bool: ...
def parse(body, url, category, query, brands, pincode) -> list[Product]: ...
```

Then add `"name"` to the `platforms:` list in `config.yaml`. The core
runner will pick it up automatically.

## Architecture

```
run.py
 └─ core.run_platform(platform_module, ...)
     ├─ launches Chromium with realistic UA / locale
     ├─ registers page.on("response") with platform.is_product_response
     ├─ platform.setup(page, pincode)      # set delivery location
     ├─ for each query:
     │   ├─ navigate platform.search_urls(q)
     │   ├─ scroll until lazy-load settles
     │   ├─ drain captured JSON responses
     │   └─ platform.parse(body, ...)  → list[Product]
     └─ dedupe on (platform, sku_id)
storage.save() → snapshot xlsx + history csv + comparison xlsx
```

## Accuracy tactics already in place

1. **JSON capture, not HTML parsing** — structured fields, far less brittle.
2. **Multi-key field detection** — each platform module lists every known
   alias (`mrp`, `MRP`, `priceMRP`, `maxRetailPrice`) so a JSON rename on
   their side rarely breaks us.
3. **Brand whitelist filtering** — drops noise products.
4. **Dedupe by `(platform, sku_id)`** — same product showing up across
   multiple queries collapses to one row.
5. **Locale set to en-IN / Asia/Kolkata** — prices show in INR.
6. **Debug mode** — dumps every captured JSON so you can verify what was
   parsed and adjust key lists if a platform restructures.

## Realistic expectations

- DMart, BigBasket: very reliable.
- Zepto, Blinkit, Swiggy Instamart: location-gated. If the location modal
  doesn't accept a pincode and demands GPS, the run captures zero
  products. Workaround in the works (set saved address via cookie).
- Flipkart, Amazon: not included — they actively block scrapers.
  Realistic only with a paid proxy service.

## Schedule it

Run nightly via Windows Task Scheduler:

```powershell
schtasks /Create /SC DAILY /TN "DodlaPriceScrape" /TR "D:\Web-Scrapper\.venv\Scripts\python.exe D:\Web-Scrapper\run.py" /ST 03:00
```
