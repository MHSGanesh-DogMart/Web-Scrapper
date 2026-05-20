"""Cloud dashboard — reads CSV uploaded by the user (no scraping here).

Deploy on Streamlit Community Cloud:
    1. Push this repo to GitHub
    2. Go to share.streamlit.io → New app → pick this repo → app.py
    3. Done — no server setup needed.

How to use:
    - Run the scraper locally:  python run.py scrape
    - Download data/history.csv from your machine
    - Upload it in the dashboard sidebar
    - All charts and tables update instantly
"""
from __future__ import annotations

from pathlib import Path
import io

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Dairy Price Monitor",
    page_icon="🥛",
    layout="wide",
)

COLUMNS = [
    "scraped_at", "platform", "category", "brand",
    "product_name", "size", "mrp", "sale_price", "discount_pct",
]

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🥛 Dairy Competitor Price Monitor")
st.caption("Track competitor dairy prices across DMart · Zepto · Blinkit · BigBasket")
st.divider()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Load Data")
    uploaded = st.file_uploader(
        "Upload history.csv",
        type="csv",
        help="Run the scraper locally (python run.py scrape), then upload data/history.csv here.",
    )

    st.markdown("---")
    st.subheader("Filters")

    # Placeholders — filled after data loads
    brand_filter    = []
    category_filter = []
    platform_filter = []

    st.markdown("---")
    st.info(
        "**To collect data:**\n\n"
        "1. Clone the repo\n"
        "2. `pip install -r requirements_scraper.txt`\n"
        "3. `python -m playwright install chromium`\n"
        "4. `python run.py scrape`\n"
        "5. Upload `data/history.csv` here"
    )

# ── Load data ─────────────────────────────────────────────────────────────────
if uploaded is None:
    st.info("Upload your `history.csv` file in the sidebar to see the dashboard.")
    st.stop()

try:
    df = pd.read_csv(uploaded, skipinitialspace=True, on_bad_lines="skip")
    df.columns = [c.strip() for c in df.columns]
    if "scraped_at" in df.columns:
        df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    for col in ["mrp", "sale_price", "discount_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
except Exception as e:
    st.error(f"Could not read CSV: {e}")
    st.stop()

if df.empty:
    st.warning("The CSV file is empty.")
    st.stop()

# ── Sidebar filters (now we have data) ───────────────────────────────────────
with st.sidebar:
    brand_opts    = sorted(df["brand"].dropna().unique()) if "brand" in df.columns else []
    cat_opts      = sorted(df["category"].dropna().unique()) if "category" in df.columns else []
    platform_opts = sorted(df["platform"].dropna().unique()) if "platform" in df.columns else []

    brand_filter    = st.multiselect("Brand",    options=brand_opts,    default=[])
    category_filter = st.multiselect("Category", options=cat_opts,      default=[])
    platform_filter = st.multiselect("Platform", options=platform_opts, default=[])

# ── Apply filters ─────────────────────────────────────────────────────────────
latest_ts = df["scraped_at"].max() if "scraped_at" in df.columns else None
cutoff    = (latest_ts - pd.Timedelta(hours=2)) if pd.notna(latest_ts) else None
latest    = df[df["scraped_at"] >= cutoff].copy() if cutoff is not None else df.copy()

if brand_filter:
    latest = latest[latest["brand"].isin(brand_filter)]
if category_filter:
    latest = latest[latest["category"].isin(category_filter)]
if platform_filter:
    latest = latest[latest["platform"].isin(platform_filter)]

view = df.copy()
if brand_filter:    view = view[view["brand"].isin(brand_filter)]
if category_filter: view = view[view["category"].isin(category_filter)]
if platform_filter: view = view[view["platform"].isin(platform_filter)]

# ── KPI cards ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Products",   len(latest))
c2.metric("Brands",     latest["brand"].nunique()    if not latest.empty else 0)
c3.metric("Categories", latest["category"].nunique() if not latest.empty else 0)
c4.metric("Platforms",  latest["platform"].nunique() if not latest.empty else 0)
c5.metric("Last Run",   str(latest_ts)[:16] if pd.notna(latest_ts) else "—")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(
    ["📋 Latest Prices", "📊 Price Chart", "⚖️ Compare Brands", "📈 Price History"]
)

with tab1:
    st.subheader("Latest Product Prices")
    if latest.empty:
        st.info("No products match the current filters.")
    else:
        cols = [c for c in ["platform","brand","category","product_name","size","mrp","sale_price","discount_pct"] if c in latest.columns]
        tbl  = latest[cols].sort_values(["platform","category","brand"]).reset_index(drop=True)
        tbl  = tbl.rename(columns={
            "platform":"Platform","brand":"Brand","category":"Category",
            "product_name":"Product","size":"Size",
            "mrp":"MRP (₹)","sale_price":"Sale Price (₹)","discount_pct":"Discount %",
        })
        st.dataframe(tbl, use_container_width=True, hide_index=True,
            column_config={
                "MRP (₹)":        st.column_config.NumberColumn(format="₹%.2f"),
                "Sale Price (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Discount %":     st.column_config.NumberColumn(format="%.1f%%"),
            })
        st.download_button("⬇ Download CSV",
            data=tbl.to_csv(index=False).encode("utf-8"),
            file_name="dairy_prices.csv", mime="text/csv")

with tab2:
    st.subheader("Sale Price by Product")
    cdf = latest.dropna(subset=["sale_price"]).copy() if not latest.empty else pd.DataFrame()
    if cdf.empty:
        st.info("No price data.")
    else:
        cdf["label"] = cdf["product_name"].str[:45]
        chart = (
            alt.Chart(cdf).mark_bar()
            .encode(
                x=alt.X("sale_price:Q", title="Sale Price (₹)"),
                y=alt.Y("label:N", sort="-x", title="Product"),
                color=alt.Color("platform:N", title="Platform"),
                tooltip=["platform","brand","product_name","size","sale_price","mrp","discount_pct"],
            )
            .properties(height=max(300, len(cdf) * 20))
        )
        st.altair_chart(chart, use_container_width=True)

with tab3:
    st.subheader("Brand Price Comparison")
    cmp = latest.dropna(subset=["sale_price"]).copy() if not latest.empty else pd.DataFrame()
    if cmp.empty:
        st.info("No data to compare.")
    else:
        sel_cat = st.selectbox("Category", ["(all)"] + sorted(cmp["category"].unique()))
        if sel_cat != "(all)":
            cmp = cmp[cmp["category"] == sel_cat]
        chart2 = (
            alt.Chart(cmp).mark_bar()
            .encode(
                x=alt.X("brand:N", title="Brand"),
                y=alt.Y("mean(sale_price):Q", title="Avg Sale Price (₹)"),
                color=alt.Color("platform:N"),
                tooltip=["brand","platform","mean(sale_price):Q","count():Q"],
            )
        )
        st.altair_chart(chart2, use_container_width=True)

with tab4:
    st.subheader("Price Trend Over Time")
    trend = view.dropna(subset=["sale_price"]) if not view.empty else pd.DataFrame()
    if trend.empty or ("scraped_at" not in trend.columns) or trend["scraped_at"].nunique() < 2:
        st.info("Need multiple scrape runs to show trends. Run the scraper again tomorrow.")
    else:
        col1, col2 = st.columns(2)
        sel_brand = col1.selectbox("Brand",    sorted(trend["brand"].unique()))
        sel_plat  = col2.selectbox("Platform", ["(all)"] + sorted(trend["platform"].unique()))
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
                tooltip=["scraped_at","platform","product_name","size","sale_price"],
            )
        )
        st.altair_chart(line, use_container_width=True)

st.divider()
st.caption("Dodla Dairy · Competitor Price Monitor · DMart + Zepto + Blinkit + BigBasket")
