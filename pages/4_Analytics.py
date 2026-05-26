"""
pages/4_Analytics.py — Analytics Dashboard
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

st.set_page_config(page_title="Analytics", page_icon="📊", layout="wide")
st.title("📊 Analytics Dashboard")

try:
    from src.storage.database import get_session
    from src.analytics.analytics import AnalyticsEngine
    from src.storage.models import Transaction
    from sqlalchemy import extract, func
except Exception as e:
    st.error(f"Import error: {e}")
    st.stop()

# ── Sidebar filters ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📅 Filters")
    current_year  = datetime.now().year
    current_month = datetime.now().month

    with get_session() as db:
        year_rows = db.query(
            extract("year", Transaction.transaction_date)
        ).distinct().all()
        years = sorted([int(r[0]) for r in year_rows if r[0]])

    # ── FIX: default to most recent year WITH DATA, not current calendar year
    if not years:
        st.warning("No data yet. Upload statements first.")
        st.stop()

    default_year_idx = len(years) - 1   # most recent year that has data
    selected_year = st.selectbox("Year", options=years, index=default_year_idx)

    selected_month = st.selectbox(
        "Month",
        options=[0] + list(range(1, 13)),
        format_func=lambda m: "Full Year" if m == 0 else datetime(2000, m, 1).strftime("%B"),
        index=0,
    )
    st.markdown("---")
    chart_theme = st.selectbox("Chart Theme", ["plotly", "plotly_dark", "ggplot2", "seaborn"])

month_arg = selected_month if selected_month != 0 else None
period_label = (
    datetime(2000, selected_month, 1).strftime("%B ") + str(selected_year)
    if selected_month else str(selected_year)
)
st.markdown(f"Showing data for: **{period_label}**")

# ── Load all data ──────────────────────────────────────────────────────────────
with get_session() as db:
    engine = AnalyticsEngine(db)

    # month_arg is None when "Full Year" is selected — kpi_summary handles both cases
    kpis         = engine.kpi_summary(selected_year, month_arg)

    monthly_df   = engine.monthly_spending_summary(year=selected_year)
    category_df  = engine.spending_by_category(year=selected_year, month=month_arg)
    nws_df       = engine.needs_wants_savings(year=selected_year, month=month_arg)
    daily_df     = engine.daily_spending(year=selected_year, month=month_arg)
    top_merch_df = engine.top_merchants(limit=15, year=selected_year, month=month_arg)
    txn_df       = engine.get_transactions(year=selected_year, month=month_arg, limit=500)

# ── KPI Cards ──────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("💸 Total Spend",         f"${kpis['total_spend']:,.2f}")
c2.metric("🔢 Transactions",        kpis["transaction_count"])
c3.metric("💡 Avg per Transaction", f"${kpis['avg_per_transaction']:,.2f}")
c4.metric("⚠️ Needs Review",         kpis["needs_review"])
st.markdown("---")

# ── Monthly trend + Category donut ────────────────────────────────────────────
col1, col2 = st.columns([3, 2])

with col1:
    st.subheader("📈 Monthly Spending Trend")
    if monthly_df.empty:
        st.info("No monthly data yet.")
    else:
        fig = px.bar(
            monthly_df, x="period", y="total_spend",
            labels={"period": "Month", "total_spend": "Total Spend ($)"},
            color="total_spend", color_continuous_scale="Blues",
            template=chart_theme,
        )
        fig.update_layout(showlegend=False, coloraxis_showscale=False,
                          xaxis_tickangle=-45, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("🍩 Spending by Category")
    if category_df.empty:
        st.info("No category data yet.")
    else:
        fig_pie = px.pie(
            category_df, values="total_spend", names="category_name",
            color_discrete_sequence=px.colors.qualitative.Set3,
            hole=0.45, template=chart_theme,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie.update_layout(showlegend=False, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig_pie, use_container_width=True)

# ── Needs/Wants/Savings + Daily timeline ──────────────────────────────────────
col3, col4 = st.columns([1, 2])

with col3:
    st.subheader("⚖️ Needs vs Wants")
    if nws_df.empty:
        st.info("No data.")
    else:
        COLOR_MAP = {"need":"#4CAF50","want":"#FF7043","saving":"#FFD700","income":"#69F0AE"}
        for _, row in nws_df.sort_values("total", ascending=False).iterrows():
            label = str(row["spending_type"]).replace("SpendingType.","").title()
            st.markdown(f"**{label}** — ${row['total']:,.2f} ({row['pct']:.1f}%)")
            st.progress(row["pct"] / 100)
        st.markdown("---")
        st.caption("🎯 50/30/20 Rule: 50% needs, 30% wants, 20% savings")

with col4:
    st.subheader("📅 Daily Spending Timeline")
    if daily_df.empty:
        st.info("No daily data.")
    else:
        fig_area = px.area(
            daily_df, x="transaction_date", y="total_spend",
            labels={"transaction_date":"Date","total_spend":"Spend ($)"},
            template=chart_theme, color_discrete_sequence=["#2196F3"],
        )
        fig_area.update_layout(margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig_area, use_container_width=True)

# ── Category bar + Top merchants ──────────────────────────────────────────────
col5, col6 = st.columns(2)

with col5:
    st.subheader("📊 Category Breakdown")
    if category_df.empty:
        st.info("No data.")
    else:
        fig_bar = px.bar(
            category_df.sort_values("total_spend"),
            x="total_spend", y="category_name", orientation="h",
            labels={"total_spend":"Total Spend ($)","category_name":"Category"},
            color="total_spend", color_continuous_scale="Teal",
            template=chart_theme, text="pct_of_total",
        )
        fig_bar.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        fig_bar.update_layout(showlegend=False, coloraxis_showscale=False,
                              margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig_bar, use_container_width=True)

with col6:
    st.subheader("🏆 Top Merchants")
    if top_merch_df.empty:
        st.info("No merchant data.")
    else:
        fig_merch = px.bar(
            top_merch_df.sort_values("total_spend"),
            x="total_spend", y="canonical_name", orientation="h",
            color="category_name",
            labels={"total_spend":"Total Spend ($)","canonical_name":"Merchant"},
            template=chart_theme, text="transaction_count",
        )
        fig_merch.update_traces(texttemplate="%{text} txns", textposition="outside")
        fig_merch.update_layout(margin=dict(l=0,r=0,t=30,b=0), showlegend=True)
        st.plotly_chart(fig_merch, use_container_width=True)

# ── Transaction Table ──────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 Transaction Detail")

if not txn_df.empty:
    search = st.text_input("🔍 Search transactions", placeholder="Filter by merchant name...")
    if search:
        txn_df = txn_df[
            txn_df["canonical_name"].str.contains(search, case=False, na=False) |
            txn_df["raw_merchant"].str.contains(search, case=False, na=False)
        ]
    display_df = txn_df.copy()
    display_df["amount"] = display_df["amount"].apply(lambda x: f"${x:,.2f}")
    display_df["date"]   = pd.to_datetime(display_df["date"]).dt.strftime("%Y-%m-%d")
    display_df["needs_review"]  = display_df["needs_review"].apply(lambda x: "⚠️" if x else "")
    display_df["is_recurring"]  = display_df["is_recurring"].apply(lambda x: "🔁" if x else "")
    st.dataframe(
        display_df[["date","canonical_name","category","amount","type","needs_review","is_recurring"]],
        use_container_width=True, hide_index=True,
    )
    st.caption(f"{len(txn_df)} transactions shown.")
    csv = txn_df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Export to CSV", data=csv,
                       file_name=f"transactions_{period_label.replace(' ','_')}.csv",
                       mime="text/csv")
else:
    st.info("No transactions found for the selected filters.")