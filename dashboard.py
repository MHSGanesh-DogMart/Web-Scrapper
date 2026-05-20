"""Streamlit dashboard for the dairy competitor price scraper.

Reads data/history.csv (and the latest snapshot) for charts. The Run button
drives core.run_platform across the platforms you tick on the sidebar.
"""
from __future__ import annotations

import importlib
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

# ── Page ──────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Dairy Price Monitor",
    page_icon="🥛",
    layout="wide",
)

CFG_PATH = Path(__file__).parent / "config.yaml"
with CFG_PATH.open("r", encoding="utf-8") as fh:
    config = yaml.safe_load(fh)

HISTORY_CSV = Path(config["output"]["history_csv"])
SNAPSHOT_DIR = Path(config["output"]["snapshot_dir"])
ALL_BRANDS = config["brands"]
ALL_PLATFORMS = config.get("platforms", ["dmart", "bigbasket", "zepto"])


# ── Robust loader ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=15)
def load_history() -> pd.DataFrame:
    """Read history.csv defensively — strip whitespace from column names,
    parse dates manually so a missing column doesn't crash the dashboard."""
    if not HISTORY_CSV.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(
            HISTORY_CSV,
            on_bad_lines="skip",
            engine="python",
            skipinitialspace=True,
        )
    except Exception as e:
        st.warning(f"Could not parse history.csv ({e}). Use the 'Reset data' button.")
        return pd.DataFrame()
    # Normalise column names: strip whitespace.
    df.columns = [str(c).strip() for c in df.columns]
    # Convert scraped_at to datetime if present.
    if "scraped_at" in df.columns:
        df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    return df


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🥛 Dairy Competitor Price Monitor")
st.caption("Track competitor dairy prices across DMart · BigBasket · Zepto")
st.divider()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    pincode_val = st.text_input(
        "Pincode",
        value=str(config.get("pincode", "500032")),
        max_chars=6,
        help="Delivery pincode — DMart, BigBasket, and Zepto serve different prices per area.",
    )

    st.subheader("Platforms")
    platform_picks = {
        name: st.checkbox(name.title(), value=True)
        for name in ALL_PLATFORMS
    }

    run_btn = st.button("▶ Run Scraper Now", type="primary", use_container_width=True)

    st.markdown("---")
    st.subheader("Filters")

    df_existing = load_history()
    brand_opts = (
        sorted(df_existing["brand"].dropna().unique().tolist())
        if not df_existing.empty else ALL_BRANDS
    )
    cat_opts = (
        sorted(df_existing["category"].dropna().unique().tolist())
        if not df_existing.empty else []
    )
    plat_opts = (
        sorted(df_existing["platform"].dropna().unique().tolist())
        if not df_existing.empty else [p.title() for p in ALL_PLATFORMS]
    )

    brand_filter = st.multiselect("Brand", options=brand_opts, default=[])
    category_filter = st.multiselect("Category", options=cat_opts, default=[])
    platform_filter = st.multiselect("Platform", options=plat_opts, default=[])

    st.caption("Filters apply to the table and trend chart.")

    st.markdown("---")
    with st.expander("🧹 Maintenance"):
        if st.button("Reset data (delete history.csv)", use_container_width=True):
            try:
                if HISTORY_CSV.exists():
                    HISTORY_CSV.unlink()
                st.cache_data.clear()
                st.success("history.csv deleted. Run the scraper to repopulate.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not delete: {e}")

# ── Run scraper ───────────────────────────────────────────────────────────────
if run_btn:
    selected = [name for name, picked in platform_picks.items() if picked]
    if not selected:
        st.warning("Pick at least one platform.")
    else:
        status_box = st.empty()
        bar = st.progress(0, text="Starting…")
        try:
            from core import run_platform
            import storage
        except Exception as e:
            st.error(f"Import error: {e}")
            st.stop()

        queries = config["queries"]
        brands = config["brands"]
        pincode = pincode_val
        headless = bool(config.get("headless", False))
        slow_mo = int(config.get("slow_mo_ms", 0))
        debug = bool(config.get("debug", False))
        profile_dir = config.get("profile_dir", "data/profiles")

        # Live UI panels that update as products stream in.
        live_count_box = st.empty()
        live_tail_box  = st.empty()
        running_total  = {"n": 0}
        recent_rows: list[dict] = []  # last 10 products, newest first

        def on_query_done(platform_name, category, query, new_products):
            """Called after each query inside run_platform. Append rows to
            history.csv immediately + refresh the live UI."""
            if not new_products:
                return
            try:
                storage.append_rows(new_products, str(HISTORY_CSV))
            except Exception:
                pass
            running_total["n"] += len(new_products)
            # Keep a rolling tail of newest products.
            for p in reversed(new_products):
                recent_rows.insert(0, {
                    "Platform": p.platform, "Brand": p.brand,
                    "Product": p.product_name, "Size": p.size,
                    "MRP ₹": p.mrp, "Sale ₹": p.sale_price,
                    "-%": p.discount_pct,
                })
            del recent_rows[10:]
            live_count_box.metric(
                "🟢 Live · products captured so far",
                running_total["n"],
                delta=f"+{len(new_products)} from {platform_name} / {query}",
            )
            live_tail_box.dataframe(
                pd.DataFrame(recent_rows),
                use_container_width=True, hide_index=True,
            )
            # Invalidate read cache so the rest of the dashboard could see
            # the new rows on the next rerun.
            try:
                st.cache_data.clear()
            except Exception:
                pass

        all_products = []
        total = len(selected)
        for i, name in enumerate(selected):
            status_box.info(f"Scraping **{name.upper()}**… ({i+1}/{total})")
            bar.progress(int((i / total) * 90), text=f"Scraping {name}…")
            try:
                mod = importlib.import_module(f"platforms.{name}")
            except ModuleNotFoundError:
                status_box.error(f"No platform module: platforms/{name}.py")
                continue
            try:
                rows = run_platform(
                    mod,
                    queries_by_category=queries,
                    brands=brands,
                    pincode=pincode,
                    headless=headless,
                    slow_mo_ms=slow_mo,
                    debug=debug,
                    profile_dir=profile_dir,
                    on_query_done=on_query_done,
                )
                all_products.extend(rows)
                status_box.success(f"{name.upper()}: {len(rows)} products")
            except Exception as e:
                status_box.error(f"{name.upper()} failed: {e}")

        bar.progress(95, text="Saving snapshot…")
        if all_products:
            # Write the Excel snapshot + comparison once at the end (history
            # CSV was already appended live per query).
            from core import COLUMNS
            df_final = pd.DataFrame(
                [p.as_row() for p in all_products], columns=COLUMNS
            )
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            snap_path = SNAPSHOT_DIR / f"prices_{stamp}.xlsx"
            df_final.to_excel(snap_path, index=False)
            bar.progress(100, text="Done!")
            status_box.success(
                f"Collected **{len(all_products)} products** across {total} platform(s). "
                f"Snapshot → `{snap_path.name}`"
            )
        else:
            bar.progress(100, text="Done (0 products)")
            status_box.warning(
                "No products captured. If a platform shows 0, run "
                "`python run.py setup <platform>` once in a terminal, "
                "set the address in the popup, and try again."
            )
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()

# ── Load data ─────────────────────────────────────────────────────────────────
df = load_history()

if df.empty:
    st.info("No data yet. Tick platforms in the sidebar and click **▶ Run Scraper Now**.")
    st.stop()

# Ensure expected columns exist (defensive — schemas have changed over time).
for col in ("scraped_at", "platform", "category", "brand", "product_name",
            "size", "mrp", "sale_price", "discount_pct"):
    if col not in df.columns:
        df[col] = pd.NA

# ── Apply filters ─────────────────────────────────────────────────────────────
# "Latest run" = all rows within 2 hours of the newest timestamp.
# This groups an entire scrape session (DMart + Zepto + Blinkit finish at
# slightly different times) instead of showing only the last-saved platform.
latest_ts  = df["scraped_at"].max()
cutoff     = latest_ts - pd.Timedelta(hours=2)
latest = df[df["scraped_at"] >= cutoff].copy()
view = df.copy()

if brand_filter:
    latest = latest[latest["brand"].isin(brand_filter)]
    view = view[view["brand"].isin(brand_filter)]
if category_filter:
    latest = latest[latest["category"].isin(category_filter)]
    view = view[view["category"].isin(category_filter)]
if platform_filter:
    latest = latest[latest["platform"].isin(platform_filter)]
    view = view[view["platform"].isin(platform_filter)]

# ── KPI cards ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Products", len(latest))
c2.metric("Brands", latest["brand"].nunique() if not latest.empty else 0)
c3.metric("Categories", latest["category"].nunique() if not latest.empty else 0)
c4.metric("Platforms", latest["platform"].nunique() if not latest.empty else 0)
c5.metric("Last Run", str(latest_ts)[:16] if pd.notna(latest_ts) else "—")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_card, tab1, tab2, tab3, tab4 = st.tabs(
    ["🃏 Browse One-by-One", "📋 Full Table", "📊 Price Chart",
     "⚖️ Compare Brands", "📈 Price History"]
)

# ── Tab: One-product-at-a-time browser ────────────────────────────────────────
with tab_card:
    st.subheader("Browse products one by one")
    if latest.empty:
        st.info("No products to browse.")
    else:
        browse_df = (
            latest.sort_values(["category", "brand", "sale_price"])
            .reset_index(drop=True)
        )
        total = len(browse_df)

        # Persist the index across reruns
        if "card_idx" not in st.session_state:
            st.session_state.card_idx = 0
        # Clamp if filters shrank the list
        if st.session_state.card_idx >= total:
            st.session_state.card_idx = 0

        # Navigation row
        nav1, nav2, nav3, nav4, nav5 = st.columns([1, 1, 2, 1, 1])
        if nav1.button("⏮ First", use_container_width=True):
            st.session_state.card_idx = 0
        if nav2.button("◀ Prev", use_container_width=True):
            st.session_state.card_idx = max(0, st.session_state.card_idx - 1)
        nav3.markdown(
            f"<div style='text-align:center;font-size:18px;padding-top:6px;'>"
            f"<b>{st.session_state.card_idx + 1}</b> / {total}"
            f"</div>",
            unsafe_allow_html=True,
        )
        if nav4.button("Next ▶", use_container_width=True):
            st.session_state.card_idx = min(total - 1, st.session_state.card_idx + 1)
        if nav5.button("Last ⏭", use_container_width=True):
            st.session_state.card_idx = total - 1

        # Jump-to slider for big lists
        new_idx = st.slider(
            "Jump to product",
            min_value=1, max_value=total,
            value=st.session_state.card_idx + 1,
            key="card_slider",
        ) - 1
        if new_idx != st.session_state.card_idx:
            st.session_state.card_idx = new_idx

        # Current product card
        r = browse_df.iloc[st.session_state.card_idx]
        st.divider()

        c_left, c_right = st.columns([2, 1])
        with c_left:
            st.markdown(f"### {r['product_name']}")
            st.caption(
                f"**{r['brand']}** · {r['category'].title()} · "
                f"{r['platform']} · pincode {r.get('pincode', '—')}"
            )
            if pd.notna(r.get("size")) and str(r["size"]).strip().lower() not in ("", "nan"):
                st.markdown(f"📦 **Size:** {r['size']}")
            if pd.notna(r.get("url")) and str(r["url"]).startswith("http"):
                st.markdown(f"🔗 [Open on {r['platform']}]({r['url']})")
            if pd.notna(r.get("scraped_at")):
                st.caption(f"Last scraped: {r['scraped_at']}")
        with c_right:
            mrp = r.get("mrp")
            sale = r.get("sale_price")
            disc = r.get("discount_pct", 0) or 0
            st.metric(
                label="Sale Price",
                value=f"₹{sale:.0f}" if pd.notna(sale) else "—",
                delta=(f"-₹{(mrp - sale):.0f}  ({disc:.1f}% off)"
                       if pd.notna(mrp) and pd.notna(sale) and mrp > sale else None),
                delta_color="inverse",
            )
            if pd.notna(mrp):
                st.caption(f"MRP: ₹{mrp:.0f}")

        st.divider()
        st.caption(
            "Use ◀ / ▶ buttons or the slider to step through products. "
            "Filters in the sidebar apply here too."
        )

with tab1:
    st.subheader("Latest Product Prices")
    if latest.empty:
        st.info("No products match the current filters.")
    else:
        cols = ["platform", "brand", "category", "product_name", "size",
                "mrp", "sale_price", "discount_pct"]
        tbl = (latest[cols]
               .sort_values(["platform", "category", "brand", "sale_price"])
               .rename(columns={
                   "platform": "Platform", "brand": "Brand", "category": "Category",
                   "product_name": "Product", "size": "Size",
                   "mrp": "MRP (₹)", "sale_price": "Sale Price (₹)",
                   "discount_pct": "Discount %",
               }))
        st.dataframe(
            tbl.reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
            column_config={
                "MRP (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Sale Price (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Discount %": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        st.download_button(
            "⬇ Download CSV",
            data=tbl.to_csv(index=False).encode("utf-8"),
            file_name="dairy_prices.csv",
            mime="text/csv",
        )

with tab2:
    st.subheader("Sale Price by Product")
    cdf = latest.dropna(subset=["sale_price"]).copy()
    if cdf.empty:
        st.info("No price data for chart.")
    else:
        import altair as alt
        cdf["label"] = cdf["product_name"].str[:45]
        chart = (
            alt.Chart(cdf).mark_bar()
            .encode(
                x=alt.X("sale_price:Q", title="Sale Price (₹)"),
                y=alt.Y("label:N", sort="-x", title="Product"),
                color=alt.Color("platform:N", title="Platform"),
                tooltip=["platform", "brand", "product_name", "size",
                         "sale_price", "mrp", "discount_pct"],
            )
            .properties(height=max(300, len(cdf) * 20))
        )
        st.altair_chart(chart, use_container_width=True)

with tab3:
    st.subheader("Brand Price Comparison")
    cmp = latest.dropna(subset=["sale_price"]).copy()
    if cmp.empty:
        st.info("No data to compare.")
    else:
        import altair as alt
        cats = sorted(cmp["category"].dropna().unique().tolist())
        sel_cat = st.selectbox("Category", ["(all)"] + cats)
        if sel_cat != "(all)":
            cmp = cmp[cmp["category"] == sel_cat]
        chart = (
            alt.Chart(cmp).mark_bar()
            .encode(
                x=alt.X("brand:N", title="Brand"),
                y=alt.Y("mean(sale_price):Q", title="Avg Sale Price (₹)"),
                color=alt.Color("platform:N"),
                tooltip=["brand", "platform", "mean(sale_price):Q", "count():Q"],
            )
        )
        st.altair_chart(chart, use_container_width=True)

with tab4:
    st.subheader("Price Trend Over Time")
    trend = view.dropna(subset=["sale_price"])
    if trend.empty or trend["scraped_at"].nunique() < 2:
        st.info("Run the scraper multiple times to see trends.")
    else:
        import altair as alt
        col1, col2 = st.columns(2)
        brands_in = sorted(trend["brand"].dropna().unique().tolist())
        sel_brand = col1.selectbox("Brand", brands_in)
        sel_plat = col2.selectbox(
            "Platform",
            ["(all)"] + sorted(trend["platform"].dropna().unique().tolist()),
        )
        tdata = trend[trend["brand"] == sel_brand]
        if sel_plat != "(all)":
            tdata = tdata[tdata["platform"] == sel_plat]
        line = (
            alt.Chart(tdata).mark_line(point=True)
            .encode(
                x=alt.X("scraped_at:T", title="Date / Time"),
                y=alt.Y("sale_price:Q", title="Sale Price (₹)"),
                color=alt.Color("product_name:N", title="Product"),
                strokeDash=alt.StrokeDash("platform:N"),
                tooltip=["scraped_at", "platform", "product_name", "size", "sale_price"],
            )
        )
        st.altair_chart(line, use_container_width=True)

st.divider()
st.caption("Dodla Dairy · Competitor Price Monitor · DMart + BigBasket + Zepto")
