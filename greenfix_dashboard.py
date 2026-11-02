import io
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import analytics as an

st.set_page_config(page_title="GreenFix – Growth, Customer Value & Seasonality", page_icon="🌿", layout="wide")

# Streamlit >= 1.50 uses width="stretch"; older versions use use_container_width
try:
    _NEW = tuple(int(x) for x in st.__version__.split(".")[:2]) >= (1, 50)
except Exception:
    _NEW = False


def show(fig):
    fig.update_layout(margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig, **({"width": "stretch"} if _NEW else {"use_container_width": True}))


def show_df(d):
    st.dataframe(d, hide_index=True, **({"width": "stretch"} if _NEW else {"use_container_width": True}))


aed = lambda x: f"AED {x:,.0f}"
HERE = Path(__file__).parent

# ------------------------------------------------------------------ data source
st.sidebar.title("🌿 GreenFix")
src = st.sidebar.radio("Data source", ["Sample data", "Upload file", "Google Sheet link"])


@st.cache_data(show_spinner=False)
def read_upload(name: str, content: bytes) -> pd.DataFrame:
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    return pd.read_csv(io.BytesIO(content))


@st.cache_data(ttl=300, show_spinner="Loading Google Sheet…")
def read_sheet(url: str) -> pd.DataFrame:
    return pd.read_csv(an.sheet_to_csv_url(url))


try:
    if src == "Sample data":
        raw = pd.read_csv(HERE / "greenfix_data.csv")
    elif src == "Upload file":
        up = st.sidebar.file_uploader("CSV or Excel", type=["csv", "xlsx", "xls"])
        if up is None:
            st.info("Upload your CSV/Excel file from the sidebar, or switch to *Sample data*.")
            st.stop()
        raw = read_upload(up.name, up.getvalue())
    else:
        url = st.sidebar.text_input("Paste the Google Sheet link", placeholder="https://docs.google.com/spreadsheets/d/…")
        st.sidebar.caption("The sheet must be shared as “Anyone with the link can view”.")
        if not url:
            st.info("Paste the Google Sheet link in the sidebar, or switch to *Sample data*.")
            st.stop()
        raw = read_sheet(url)
except Exception as e:
    st.error(f"Could not load the data: {e}")
    st.stop()

# ------------------------------------------------------------------ column mapping
guess = an.guess_mapping(raw.columns)
missing = [f for f, req in an.FIELDS.items() if req and guess[f] is None]
with st.sidebar.expander("🔗 Column mapping", expanded=bool(missing)):
    st.caption("Match your sheet's columns to what the dashboard needs.")
    mapping = {}
    for f, required in an.FIELDS.items():
        opts = list(raw.columns) if required else ["— none —"] + list(raw.columns)
        idx = opts.index(guess[f]) if guess[f] in opts else 0
        sel = st.selectbox(f + ("" if required else " (optional)"), opts, index=idx, key=f"map_{src}_{f}")
        mapping[f] = None if sel == "— none —" else sel

try:
    df, has_cost = an.prepare(raw, mapping)
except Exception as e:
    st.error(f"Check the column mapping in the sidebar — {e}")
    st.stop()
if df.empty:
    st.error("No valid rows after cleaning. Check the Date and Revenue column mapping.")
    st.stop()

# ------------------------------------------------------------------ filters
st.sidebar.header("🎛️ Filters")
d0, d1 = df["Date"].min().date(), df["Date"].max().date()
dr = st.sidebar.date_input("Date range", (d0, d1), min_value=d0, max_value=d1)
start, end = dr if isinstance(dr, tuple) and len(dr) == 2 else (d0, d1)


def multi(label, col):
    opts = sorted(df[col].unique())
    return st.sidebar.multiselect(label, opts, default=opts)


areas = multi("Area", "Area")
services = multi("Service type", "Service Type")
segments = multi("Customer segment", "Customer Segment") if mapping["Customer Segment"] else sorted(df["Customer Segment"].unique())
contracts = multi("Contract type", "Contract Type") if mapping["Contract Type"] else sorted(df["Contract Type"].unique())

fdf = df[
    df["Date"].between(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1))
    & df["Area"].isin(areas) & df["Service Type"].isin(services)
    & df["Customer Segment"].isin(segments) & df["Contract Type"].isin(contracts)
]

# ------------------------------------------------------------------ header + KPIs
st.title("🌿 GreenFix Solutions — Business Intelligence Dashboard")
st.caption("Where to grow · Which customers are worth the most over time · How to plan around the seasons")
if fdf.empty:
    st.warning("No data matches the filters.")
    st.stop()
if not has_cost:
    st.info("No Cost column mapped → profit is shown as equal to revenue.")

n_cust = fdf["Customer ID"].nunique()
repeat = (fdf.groupby("Customer ID").size() >= 2).mean() * 100
k = st.columns(6)
k[0].metric("💰 Revenue", aed(fdf["Revenue"].sum()))
k[1].metric("📈 Gross profit", aed(fdf["Profit"].sum()),
            f"{fdf['Profit'].sum() / fdf['Revenue'].sum():.0%} margin" if has_cost else None, delta_color="off")
k[2].metric("🛠️ Jobs", f"{len(fdf):,}")
k[3].metric("👥 Customers", f"{n_cust:,}")
k[4].metric("💵 Revenue / customer", aed(fdf["Revenue"].sum() / n_cust))
k[5].metric("🔁 Repeat customers", f"{repeat:.0f}%")

tab1, tab2, tab3 = st.tabs(["🚀 Growth areas", "💎 Customer value", "🗓️ Seasonality"])

# ================================================================== TAB 1: GROWTH
with tab1:
    months = st.select_slider("Compare the last N months with the N months before", [3, 6, 9, 12], value=6)
    g, (c0, c1, c2), (g_med, r_med) = an.area_growth(fdf, months)
    st.caption(f"Recent: {c1:%d %b %Y} → {c2:%d %b %Y}  |  Previous: {c0:%d %b %Y} → {c1:%d %b %Y}")

    valid = g.dropna(subset=["Growth %"])
    if valid.empty:
        st.warning("Not enough history for this comparison. Widen the date range or shorten the window.")
    else:
        top = valid[valid["Recent_Jobs"] > 0].head(3)
        st.success("**Top growth areas:** " + " · ".join(f"{r.Area} ({r['Growth %']:+.0f}%)" for _, r in top.iterrows()))

        a, b = st.columns(2)
        with a:
            fig = px.bar(valid.sort_values("Growth %"), x="Growth %", y="Area", orientation="h",
                         color="Growth %", color_continuous_scale="RdYlGn", color_continuous_midpoint=0,
                         title="Revenue growth by area (%)")
            fig.update_layout(coloraxis_showscale=False, yaxis_title=None)
            show(fig)
        with b:
            sc = valid[valid["Recent_Jobs"] > 0]
            fig = px.scatter(sc, x="Recent_Revenue", y="Growth %", size="Recent_Jobs", color="Quadrant", text="Area",
                             title="Size vs growth (bubble = number of jobs)",
                             labels={"Recent_Revenue": "Recent revenue (AED)"})
            fig.update_traces(textposition="top center")
            fig.add_hline(y=g_med, line_dash="dot", opacity=0.4)
            fig.add_vline(x=r_med, line_dash="dot", opacity=0.4)
            show(fig)

    a, b = st.columns(2)
    with a:
        fig = px.treemap(fdf, path=["Area", "Service Type"], values="Revenue", color="Profit",
                         color_continuous_scale="Greens", title="What sells where (revenue)")
        show(fig)
    with b:
        trend = fdf.assign(Month=fdf["Date"].dt.to_period("M").dt.to_timestamp()).groupby(["Month", "Area"], as_index=False)["Revenue"].sum()
        fig = px.line(trend, x="Month", y="Revenue", color="Area", title="Monthly revenue by area")
        show(fig)

    cols = ["Area", "Recent_Revenue", "Prev_Revenue", "Growth %", "Recent_Jobs", "Recent_Customers", "Quadrant"]
    if has_cost:
        cols.insert(4, "Margin %")
    show_df(g[cols].round(1))

# ================================================================== TAB 2: CUSTOMER VALUE
with tab2:
    horizon = st.slider("Projection horizon (years)", 1, 5, 3)
    ct = an.customer_table(fdf, horizon)
    par, top20 = an.pareto(ct)

    k = st.columns(4)
    k[0].metric("Avg lifetime value", aed(ct["Lifetime Value"].mean()))
    k[1].metric("Top 20% customers = revenue share", f"{top20:.0f}%")
    k[2].metric("Champions + Loyal", f"{ct['Status'].isin(['Champions', 'Loyal']).mean():.0%} of customers")
    k[3].metric("Annual profit at risk", aed(ct.loc[ct["Status"] == "At Risk", "Annual Profit"].sum()))

    a, b = st.columns(2)
    with a:
        st_agg = ct.groupby("Status", as_index=False).agg(Customers=("Jobs", "size"), Value=("Lifetime Value", "sum"))
        fig = px.bar(st_agg, x="Status", y="Value", color="Status", text="Customers",
                     category_orders={"Status": an.STATUS_ORDER}, title="Lifetime value by customer status (label = # customers)")
        fig.update_layout(showlegend=False, yaxis_title="Lifetime value (AED)")
        show(fig)
    with b:
        fig = px.area(par, x="Customers %", y="Revenue %", title="Pareto: how concentrated is revenue?")
        fig.add_vline(x=20, line_dash="dot", opacity=0.5)
        show(fig)

    a, b, c = st.columns(3)
    for col, field, title in [(a, "Contract", "Avg lifetime value by contract"),
                              (b, "Segment", "…by customer segment"),
                              (c, "Area", "…by area (where do the best customers live?)")]:
        with col:
            d = ct.groupby(field, as_index=False)["Lifetime Value"].mean().sort_values("Lifetime Value")
            fig = px.bar(d, x="Lifetime Value", y=field, orientation="h", title=title, color="Lifetime Value",
                         color_continuous_scale="Greens")
            fig.update_layout(coloraxis_showscale=False, yaxis_title=None)
            show(fig)

    show_cols = ["Customer ID", "Area", "Contract", "Segment", "Jobs", "Revenue", "Profit", "Recency (days)", "Status", "Lifetime Value"]
    a, b = st.columns(2)
    with a:
        st.subheader("🏆 Top 15 customers")
        show_df(ct.sort_values("Lifetime Value", ascending=False).head(15)[show_cols].round(0))
    with b:
        st.subheader("⚠️ Win-back list (valuable but going quiet)")
        show_df(ct[ct["Status"] == "At Risk"].sort_values("Annual Profit", ascending=False).head(15)[show_cols].round(0))

    with st.expander("How is customer value calculated?"):
        st.markdown(
            "- **Status**: *Lost* = no job for 365+ days · *At Risk* = 181–365 days · *Champions* = 4+ jobs · "
            "*Loyal* = 2–3 jobs · *New / One-time* = 1 job.\n"
            "- **Lifetime value** = profit so far + (annual profit rate × horizon × retention assumption).\n"
            "- **Retention assumptions** (editable in `analytics.py → RETENTION`): "
            + ", ".join(f"{k} {v:.0%}" for k, v in an.RETENTION.items()))

# ================================================================== TAB 3: SEASONALITY
with tab3:
    metric = st.radio("Measure", ["Jobs", "Revenue"], horizontal=True)
    avg, idx = an.seasonal_tables(fdf, metric)

    hm = idx.copy()
    hm.index = an.MONTHS
    fig = px.imshow(hm.T, color_continuous_scale="RdYlBu_r", color_continuous_midpoint=1, aspect="auto",
                    text_auto=".1f", title="Seasonal index by service (1.0 = average month, 2.0 = twice the normal demand)")
    fig.update_layout(coloraxis_showscale=False)
    show(fig)

    a, b = st.columns(2)
    with a:
        load = avg.copy()
        load.index = an.MONTHS
        fig = px.bar(load, barmode="stack", title=f"Typical monthly workload ({metric.lower()}) by service",
                     labels={"value": metric, "index": "", "variable": "Service"})
        show(fig)
    with b:
        yoy = an.monthly_by_year(fdf, metric)
        fig = px.line(yoy, x="Month Name", y="Value", color="Year", markers=True, title=f"{metric} by month, year over year",
                      category_orders={"Month Name": an.MONTHS})
        show(fig)

    st.subheader("📋 Capacity planning guide")
    show_df(an.seasonal_insights(idx))
