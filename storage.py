from __future__ import annotations

from pathlib import Path
from datetime import datetime
import pandas as pd

from core import COLUMNS, Product


def append_rows(products: list[Product], history_csv: str) -> Path:
    """Append products to history.csv immediately (no snapshot/comparison).
    Used for live streaming during a scrape run so the dashboard can show
    products as soon as they're found."""
    if not products:
        return Path(history_csv)
    df = pd.DataFrame([p.as_row() for p in products], columns=COLUMNS)
    hist_path = Path(history_csv)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(
        hist_path, mode="a", index=False, header=not hist_path.exists()
    )
    return hist_path


def _to_df(products: list[Product]) -> pd.DataFrame:
    rows = [p.as_row() for p in products]
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df


def save(
    products: list[Product],
    snapshot_dir: str,
    history_csv: str,
) -> tuple[Path, Path, Path]:
    """Write three artefacts:
    - snapshot Excel for this run
    - growing history CSV (appended)
    - comparison Excel: cheapest sale_price per (brand, size) across platforms

    Returns (snapshot_path, history_path, comparison_path).
    """
    df = _to_df(products)
    snap_dir = Path(snapshot_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = snap_dir / f"prices_{stamp}.xlsx"
    df.to_excel(snap_path, index=False)

    hist_path = Path(history_csv)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(
        hist_path, mode="a", index=False, header=not hist_path.exists()
    )

    # Comparison: pivot of sale_price by (brand, size) × platform
    comp_path = snap_dir / f"comparison_{stamp}.xlsx"
    try:
        if not df.empty:
            comp = (
                df.dropna(subset=["sale_price"])
                  .assign(size=lambda x: x["size"].fillna("").astype(str))
                  .pivot_table(
                      index=["brand", "size"],
                      columns="platform",
                      values="sale_price",
                      aggfunc="min",
                  )
            )
            comp["cheapest_platform"] = comp.idxmin(axis=1)
            comp["cheapest_price"] = comp.min(axis=1, numeric_only=True)
            comp.to_excel(comp_path)
        else:
            comp_path.write_text("")  # empty placeholder
    except Exception:
        # If pivot fails (e.g. only one platform), skip silently.
        comp_path = snap_path

    return snap_path, hist_path, comp_path
