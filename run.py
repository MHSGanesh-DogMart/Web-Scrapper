from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
import yaml

from core import run_platform
import storage


def load_config() -> dict:
    with (Path(__file__).parent / "config.yaml").open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def cmd_setup(args, config: dict) -> int:
    """Open one platform interactively so the user can set the location,
    log in, etc. The browser stays open until the user closes it. All
    cookies/localStorage are saved into the persistent profile and reused
    on every subsequent scrape run."""
    name = args.platform
    try:
        mod = importlib.import_module(f"platforms.{name}")
    except ModuleNotFoundError:
        print(f"No platform module: platforms/{name}.py")
        return 2
    print(f"Opening {mod.NAME} in headed mode. Set the delivery location,")
    print(f"dismiss any modals/login prompts, then close the browser window.")
    run_platform(
        mod,
        queries_by_category={},
        brands=config["brands"],
        pincode=str(config["pincode"]),
        headless=False,
        slow_mo_ms=int(config.get("slow_mo_ms", 0)),
        debug=False,
        profile_dir=config.get("profile_dir", "data/profiles"),
        setup_only=True,
    )
    print("Profile saved. You can now run: python run.py scrape")
    return 0


def cmd_scrape(args, config: dict) -> int:
    all_products = []
    platforms = config.get("platforms", ["dmart"])
    queries = config["queries"]
    brands = config["brands"]
    pincode = str(config["pincode"])
    headless = bool(config.get("headless", True))
    slow_mo = int(config.get("slow_mo_ms", 0))
    debug = bool(config.get("debug", False))
    profile_dir = config.get("profile_dir", "data/profiles")

    for name in platforms:
        try:
            mod = importlib.import_module(f"platforms.{name}")
        except ModuleNotFoundError:
            print(f"[skip] no platform module: platforms/{name}.py")
            continue
        print(f"\n=== {mod.NAME} ===")
        try:
            products = run_platform(
                mod,
                queries_by_category=queries,
                brands=brands,
                pincode=pincode,
                headless=headless,
                slow_mo_ms=slow_mo,
                debug=debug,
                profile_dir=profile_dir,
            )
        except Exception as e:
            print(f"  [{mod.NAME}] crashed: {e}")
            continue
        print(f"  [{mod.NAME}] total products: {len(products)}")
        all_products.extend(products)

    print(f"\nTotal across platforms: {len(all_products)}")
    if not all_products:
        print("No products captured. Run `python run.py setup <platform>` first,")
        print("set the location interactively, then retry.")
        return 1

    snap, hist, comp = storage.save(
        all_products,
        snapshot_dir=config["output"]["snapshot_dir"],
        history_csv=config["output"]["history_csv"],
    )
    print(f"Snapshot   : {snap}")
    print(f"History    : {hist}")
    print(f"Comparison : {comp}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="run.py")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("setup", help="Open a platform interactively to set location/login")
    sp.add_argument("platform", help="dmart | bigbasket | zepto")

    sub.add_parser("scrape", help="Scrape all configured platforms (default)")

    args = ap.parse_args()
    config = load_config()

    if args.cmd == "setup":
        return cmd_setup(args, config)
    return cmd_scrape(args, config)


if __name__ == "__main__":
    sys.exit(main())
