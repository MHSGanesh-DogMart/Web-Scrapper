"""
Daily selector health monitor — verifies all platforms are reachable
and returning product data. Uses the auto_extract pattern detector, so
it works even after a site redesigns its CSS.

Usage:
    python selector_monitor.py          # run check now
    python selector_monitor.py --setup  # create Windows scheduled task
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
import yaml

import auto_extract

DATA_DIR   = Path(__file__).parent / "data"
HEALTH_FILE = DATA_DIR / "health.json"
REPORT_CSV  = DATA_DIR / "health_history.csv"

with (Path(__file__).parent / "config.yaml").open("r", encoding="utf-8") as _fh:
    _cfg = yaml.safe_load(_fh)

BRANDS = _cfg["brands"]

CHECKS = {
    "DMart":      "https://www.dmart.in/category/dairy-aesc-dairy",
    "Zepto":      "https://www.zepto.com/search?query=milk",
    "Blinkit":    "https://blinkit.com/s/?q=milk",
    "BigBasket":  "https://www.bigbasket.com/ps/?q=milk",
}


def run_checks() -> dict[str, dict]:
    results: dict[str, dict] = {}
    now = datetime.now().isoformat(timespec="seconds")

    print("=" * 60)
    print(f"Selector Health Check  —  {now}")
    print("=" * 60)

    _CLOUD_ARGS = [
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-extensions",
    ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=_CLOUD_ARGS)

        for platform, url in CHECKS.items():
            print(f"\n[{platform}] checking {url} …")
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            page = ctx.new_page()
            status = "broken"
            count  = 0

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40000)
                time.sleep(3)
                for _ in range(4):
                    page.mouse.wheel(0, 2000)
                    time.sleep(0.4)
                # Use auto_extract to count detected cards
                raw = page.evaluate(auto_extract._JS_EXTRACT_CARDS) or []
                count = len(raw)
                if count >= 3:
                    status = "ok"
                    print(f"  ✓  auto-detected {count} product cards")
                else:
                    print(f"  ✗ BROKEN  only {count} cards detected")
            except Exception as e:
                print(f"  ERROR: {e}")

            ctx.close()
            results[platform] = {
                "status":     status,
                "selector":   "auto_extract",
                "count":      count,
                "note":       "",
                "checked_at": now,
            }

        browser.close()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")

    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    header = not REPORT_CSV.exists()
    with REPORT_CSV.open("a", encoding="utf-8", newline="") as f:
        if header:
            f.write("checked_at,platform,status,selector,count\n")
        for pf, r in results.items():
            f.write(
                f"{r['checked_at']},{pf},{r['status']},"
                f"\"{r.get('selector','')}\",{r['count']}\n"
            )

    print("\n" + "=" * 60)
    broken = [p for p, r in results.items() if r["status"] != "ok"]
    if broken:
        print(f"ALERT: {len(broken)} platform(s) BROKEN: {', '.join(broken)}")
    else:
        print("All platforms OK.")
    print("=" * 60)

    return results


def setup_windows_task() -> None:
    python_exe = sys.executable
    script     = Path(__file__).resolve()
    task_name  = "DairyPriceHealthCheck"
    cmd = (
        f'schtasks /create /tn "{task_name}" /tr '
        f'"{python_exe} {script}" /sc DAILY /st 06:00 /f'
    )
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Scheduled task '{task_name}' created — runs daily at 06:00.")
    else:
        print("Failed to create task:")
        print(result.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true",
                        help="Register as a Windows daily scheduled task")
    args = parser.parse_args()
    if args.setup:
        setup_windows_task()
    else:
        run_checks()
