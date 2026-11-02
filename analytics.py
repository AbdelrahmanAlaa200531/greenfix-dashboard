"""Pure pandas logic for the dashboard (no Streamlit / Plotly in here, so it is easy to test)."""
import re
import numpy as np
import pandas as pd

# field -> required?
FIELDS = {
    "Date": True,
    "Customer ID": True,
    "Area": True,
    "Service Type": True,
    "Revenue": True,
    "Cost": False,
    "Customer Segment": False,
    "Contract Type": False,
}
SYNONYMS = {
    "Date": ["date", "orderdate", "jobdate", "servicedate", "bookingdate", "createdat"],
    "Customer ID": ["customerid", "customer", "clientid", "client", "customername", "custid", "clientname"],
    "Area": ["area", "district", "location", "neighborhood", "neighbourhood", "region", "community", "zone"],
    "Service Type": ["servicetype", "service", "servicecategory", "category", "jobtype", "servicename"],
    "Revenue": ["revenue", "sales", "amount", "price", "total", "income", "totalamount", "invoiceamount"],
    "Cost": ["cost", "costs", "expense", "expenses", "cogs", "jobcost"],
    "Customer Segment": ["customersegment", "segment", "propertytype", "customertype", "clienttype"],
    "Contract Type": ["contracttype", "contract", "plan", "subscription", "plantype"],
}
SEASONS = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
           6: "Summer", 7: "Summer", 8: "Summer", 9: "Summer", 10: "Autumn", 11: "Autumn"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

STATUS_ORDER = ["Champions", "Loyal", "New / One-time", "At Risk", "Lost"]
# Assumed chance the customer is still buying over the projection horizon (editable assumption).
RETENTION = {"Champions": 0.90, "Loyal": 0.75, "New / One-time": 0.40, "At Risk": 0.25, "Lost": 0.05}


# ---------------------------------------------------------------- loading
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def guess_mapping(columns):
    norm = {_norm(c): c for c in columns}
    out = {}
    for field, syns in SYNONYMS.items():
        out[field] = next((norm[s] for s in [_norm(field)] + syns if s in norm), None)
    return out


def sheet_to_csv_url(url):
    """Turn a normal Google Sheets link into its CSV-export link."""
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        return url
    gid = re.search(r"[#&?]gid=(\d+)", url)
    out = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    return out + (f"&gid={gid.group(1)}" if gid else "")


def _to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce")


def prepare(raw, mapping):
    """Rename/clean the raw table into the standard schema. Returns (df, has_cost)."""
    df = pd.DataFrame({f: raw[c].values for f, c in mapping.items() if c})

    d = pd.to_datetime(df["Date"], errors="coerce")
    if d.isna().mean() > 0.2:                      # maybe dd-mm-yyyy
        d2 = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
        if d2.isna().mean() < d.isna().mean():
            d = d2
    df["Date"] = d
    df["Revenue"] = _to_num(df["Revenue"])

    has_cost = "Cost" in df.columns
    df["Cost"] = _to_num(df["Cost"]).fillna(0.0) if has_cost else 0.0
    df["Profit"] = df["Revenue"] - df["Cost"]

    for c in ["Customer ID", "Area", "Service Type"]:
        df[c] = df[c].astype(str).str.strip()
    for c in ["Customer Segment", "Contract Type"]:
        df[c] = "Not provided" if c not in df.columns else df[c].fillna("Not provided").astype(str).str.strip()

    df = df.dropna(subset=["Date", "Revenue"]).sort_values("Date").reset_index(drop=True)
    return df, has_cost


# ---------------------------------------------------------------- 1) growth areas
def area_growth(df, months=6):
    """Last `months` vs the `months` before them, per area."""
    end = df["Date"].max()
    cut1 = end - pd.DateOffset(months=months)
    cut0 = cut1 - pd.DateOffset(months=months)
    recent = df[df["Date"] > cut1]
    prev = df[(df["Date"] > cut0) & (df["Date"] <= cut1)]

    a = recent.groupby("Area").agg(
        Recent_Revenue=("Revenue", "sum"), Recent_Profit=("Profit", "sum"),
        Recent_Jobs=("Revenue", "size"), Recent_Customers=("Customer ID", "nunique"))
    b = prev.groupby("Area").agg(Prev_Revenue=("Revenue", "sum"))
    out = a.join(b, how="outer").fillna(0)

    prev_rev = out["Prev_Revenue"].replace(0, np.nan)
    rec_rev = out["Recent_Revenue"].replace(0, np.nan)
    out["Growth %"] = (out["Recent_Revenue"] / prev_rev - 1) * 100
    out["Margin %"] = out["Recent_Profit"] / rec_rev * 100

    g_med, r_med = out["Growth %"].median(), out["Recent_Revenue"].median()

    def quadrant(r):
        if pd.isna(r["Growth %"]):
            return "No baseline"
        hi_g, hi_r = r["Growth %"] > g_med, r["Recent_Revenue"] > r_med
        return ("Star (big & growing)" if hi_g and hi_r else
                "Rising (small but growing)" if hi_g else
                "Core (big, slower)" if hi_r else "Watch (small & slow)")

    out["Quadrant"] = out.apply(quadrant, axis=1)
    out = out.reset_index().sort_values("Growth %", ascending=False, na_position="last")
    return out, (cut0, cut1, end), (g_med, r_med)


# ---------------------------------------------------------------- 2) customer value
def customer_table(df, horizon=3, retention=None):
    retention = retention or RETENTION
    asof = df["Date"].max()
    g = df.groupby("Customer ID").agg(
        Jobs=("Revenue", "size"), Revenue=("Revenue", "sum"), Profit=("Profit", "sum"),
        First=("Date", "min"), Last=("Date", "max"),
        Area=("Area", lambda s: s.mode().iat[0]),
        Contract=("Contract Type", "last"), Segment=("Customer Segment", "last"),
    ).reset_index()

    g["Recency (days)"] = (asof - g["Last"]).dt.days
    span_years = ((asof - g["First"]).dt.days / 365).clip(lower=0.5)
    g["Annual Profit"] = g["Profit"] / span_years
    g["Avg Job Value"] = g["Revenue"] / g["Jobs"]

    g["Status"] = np.select(
        [g["Recency (days)"] > 365, g["Recency (days)"] > 180, g["Jobs"] >= 4, g["Jobs"] >= 2],
        ["Lost", "At Risk", "Champions", "Loyal"], default="New / One-time")

    g["Projected Profit"] = g["Annual Profit"] * horizon * g["Status"].map(retention)
    g["Lifetime Value"] = g["Profit"] + g["Projected Profit"]
    return g


def pareto(ct):
    s = ct.sort_values("Revenue", ascending=False)["Revenue"].reset_index(drop=True)
    out = pd.DataFrame({"Customers %": (s.index + 1) / len(s) * 100,
                        "Revenue %": s.cumsum() / s.sum() * 100})
    top20 = float(s.head(max(1, int(round(len(s) * 0.2)))).sum() / s.sum() * 100)
    return out, top20


# ---------------------------------------------------------------- 3) seasonality
def seasonal_tables(df, metric="Jobs"):
    """avg: average per calendar month (rows 1..12) per service; idx: same / its own yearly average (1.0 = normal)."""
    d = df.assign(YM=df["Date"].dt.to_period("M"))
    if metric == "Jobs":
        pt = d.groupby(["YM", "Service Type"]).size().unstack(fill_value=0)
    else:
        pt = d.groupby(["YM", "Service Type"])["Revenue"].sum().unstack(fill_value=0)
    pt = pt.reindex(pd.period_range(pt.index.min(), pt.index.max(), freq="M"), fill_value=0)
    avg = pt.groupby(pt.index.month).mean().reindex(range(1, 13), fill_value=0)
    idx = (avg / avg.mean().replace(0, np.nan)).fillna(0)
    return avg, idx


def monthly_by_year(df, metric="Jobs"):
    d = df.assign(Year=df["Date"].dt.year, Month=df["Date"].dt.month)
    g = (d.groupby(["Year", "Month"]).size() if metric == "Jobs"
         else d.groupby(["Year", "Month"])["Revenue"].sum()).rename("Value").reset_index()
    g["Month Name"] = g["Month"].map(lambda m: MONTHS[m - 1])
    g["Year"] = g["Year"].astype(str)
    return g


def seasonal_insights(idx):
    rows = []
    for svc in idx.columns:
        s = idx[svc]
        if s.sum() == 0:
            continue
        pk, lo = int(s.idxmax()), int(s.idxmin())
        before = MONTHS[(pk - 2) % 12]
        if s.max() >= 1.5:
            tip = f"Strongly seasonal: add technicians/stock before {before}; push promos or training in {MONTHS[lo - 1]}."
        elif s.max() >= 1.2:
            tip = f"Mild peak in {MONTHS[pk - 1]}: schedule extra capacity, no major hiring needed."
        else:
            tip = "Stable demand all year: good base-load service for annual contracts."
        rows.append({"Service": svc, "Peak Month": MONTHS[pk - 1], "Peak Index": round(s.max(), 2),
                     "Low Month": MONTHS[lo - 1], "Low Index": round(s.min(), 2),
                     "Peak Season": SEASONS[pk], "Recommendation": tip})
    return pd.DataFrame(rows).sort_values("Peak Index", ascending=False)
