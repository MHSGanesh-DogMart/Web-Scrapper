"""
Daily selector health monitor — run once a day to verify all platforms
are still working. Set it up as a Windows Scheduled Task (see bottom of file).

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

import scraper_base
import dmart_scraper
import zepto_scraper
import blinkit_scraper

DATA_DIR   = Path(__file__).parent / "data"
REPORT_CSV = DATA_DIR / "health_history.csv"

# Quick test config per platform — just enough to detect a selector break
CHECKS = {
    "DMart": {
        "url":        "https://www.dmart.in/category/dairy-aesc-dairy",
        "strategies": dmart_scraper.SELECTOR_STRATEGIES,
    },
    "Zepto": {
        "url":        "https://www.zepto.com/search?query=milk",
        "strategies": zepto_scraper.SELECTOR_STRATEGIES,
    },
    "Blinkit": {
        "url":        "https://blinkit.com/s/?q=milk",
        "strategies": blinkit_scraper.SELECTOR_STRATEGIES,
    },
}


def run_checks() -> dict[str, dict]:
    results: dict[str, dict] = {}
    now = datetime.now().isoformat(timespec="seconds")

    print("=" * 60)
    print(f"Selector Health Check  —  {now}")
    print("=" * 60)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        for platform, cfg in CHECKS.items():
            print(f"\n[{platform}] checking {cfg['url']} …")
            ctx = browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            status = "broken"
            working_sel = None
            count = 0

            try:
                page.goto(cfg["url"], wait_until="domcontentloaded", timeout=40000)
                time.sleep(3)
                working_sel = scraper_base.detect_working_selector(
                    page, platform, cfg["strategies"]
                )
                if working_sel:
                    count = page.locator(working_sel).count()
                    status = "ok"
            except Exception as e:
                print(f"  ERROR: {e}")

            ctx.close()

            results[platform] = {
                "status":     status,
                "selector":   working_sel,
                "count":      count,
                "checked_at": now,
            }

            icon = "✓" if status == "ok" else "✗ BROKEN"
            print(f"  {icon}  selector={working_sel!r}  count={count}")

        browser.close()

    # Save health.json (for dashboard)
    scraper_base.DATA_DIR.mkdir(parents=True, exist_ok=True)
    scraper_base.HEALTH_FILE.write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )

    # Append to health_history.csv
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    header = not REPORT_CSV.exists()
    with REPORT_CSV.open("a", encoding="utf-8", newline="") as f:
        if header:
            f.write("checked_at,platform,status,selector,count\n")
        for pf, r in results.items():
            f.write(
                f"{r['checked_at']},{pf},{r['status']},"
                f"\"{r['selector'] or ''}\",{r['count']}\n"
            )

    print("\n" + "=" * 60)
    broken = [p for p, r in results.items() if r["status"] != "ok"]
    if broken:
        print(f"ALERT: {len(broken)} platform(s) BROKEN: {', '.join(broken)}")
        print("Open data/health.json for details.")
    else:
        print("All platforms OK.")
    print("=" * 60)

    return results


def setup_windows_task() -> None:
    """Register a Windows Task Scheduler job to run this script every day at 6 AM."""
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
