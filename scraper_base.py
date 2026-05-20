"""
Shared selector-detection utility used by every platform scraper.

Every scraper defines a SELECTOR_STRATEGIES list (ordered best→fallback).
Before scraping, detect_working_selector() tries each one and returns the
first that yields ≥ MIN_HITS product elements.

On failure it writes an alert to data/health.json so the dashboard can show
a warning. The scraper then carries on with whatever strategy worked last
time (stored in data/selector_state.json).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR      = Path(__file__).parent / "data"
STATE_FILE    = DATA_DIR / "selector_state.json"   # persists last-known-good selector
HEALTH_FILE   = DATA_DIR / "health.json"            # alert log for dashboard

MIN_HITS = 3   # minimum products a selector must find to be considered "working"


# ── State helpers ──────────────────────────────────────────────────────────────

def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _save_health(platform: str, status: str, selector: str | None, count: int, note: str = "") -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    health: dict[str, Any] = {}
    if HEALTH_FILE.exists():
        try:
            health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    health[platform] = {
        "status":     status,   # "ok" | "broken" | "unknown"
        "selector":   selector,
        "count":      count,
        "note":       note,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    HEALTH_FILE.write_text(json.dumps(health, indent=2), encoding="utf-8")


# ── Core detection ─────────────────────────────────────────────────────────────

def detect_working_selector(
    page,                     # Playwright Page
    platform: str,
    strategies: list[str],    # CSS selectors to try in order
    *,
    scroll_first: bool = True,
) -> str | None:
    """
    Try each CSS selector in `strategies`.
    Returns the first one that finds ≥ MIN_HITS elements.
    Saves the result to selector_state.json and health.json.
    """
    if scroll_first:
        try:
            for _ in range(3):
                page.mouse.wheel(0, 2000)
                import time; time.sleep(0.4)
        except Exception:
            pass

    for sel in strategies:
        try:
            count = page.locator(sel).count()
            if count >= MIN_HITS:
                print(f"  [{platform}] selector OK: {sel!r} → {count} elements", flush=True)
                state = _load_state()
                state[platform] = {"selector": sel, "count": count,
                                   "updated_at": datetime.now().isoformat(timespec="seconds")}
                _save_state(state)
                _save_health(platform, "ok", sel, count)
                return sel
        except Exception:
            pass

    # All strategies failed
    print(f"  [{platform}] ALL selectors failed — site may have changed HTML!", flush=True)
    _save_health(platform, "broken", None, 0,
                 note="All selectors returned 0 results. Selector update needed.")
    return None


def last_known_selector(platform: str) -> str | None:
    """Return the selector that worked last time (may be stale)."""
    state = _load_state()
    entry = state.get(platform, {})
    return entry.get("selector")


def load_health() -> dict[str, Any]:
    if HEALTH_FILE.exists():
        try:
            return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}
