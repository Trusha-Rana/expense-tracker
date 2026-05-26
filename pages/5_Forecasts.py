"""
pages/5_Forecasts.py — Predictive Insights
============================================
FEATURES:
  1. Recurring Transaction Detector — finds subscriptions & bills
  2. Next Month Forecast — predicts total spend and per-category breakdown
  3. Actual vs Predicted chart — shows how accurate the model is historically
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

st.set_page_config(page_title="Forecasts", page_icon="🔮", layout="wide")
st.title("🔮 Forecasts & Predictive Insights")
st.markdown("Detect recurring charges and predict your next month's spending based on your history.")

try:
    from src.storage.database import get_session
    from src.forecasts.forecaster import RecurringDetector, SpendForecaster
    from src.storage.models import Transaction
    from sqlalchemy import func
except Exception as e:
    st.error(f"Import error: {e}")
    st.stop()

tab_recurring, tab_forecast, tab_accuracy = st.tabs([
    "🔁 Recurring Charges", "📈 Next Month Forecast", "🎯 Model Accuracy"
])


# ── Tab 1: Recurring charges ──────────────────────────────────────────────────────
with tab_recurring:
    st.markdown(
        "Transactions from the same merchant at a similar amount each month are detected as recurring. "
        "These are typically subscriptions, memberships, and utility bills."
    )

    col_btn, col_mark = st.columns([2, 1])

    with col_btn:
        if st.button("🔍 Detect Recurring Transactions", type="primary"):
            with get_session() as db:
                detector = RecurringDetector(db)
                recurring_df = detector.detect()

            if recurring_df.empty:
                st.info("No recurring transactions detected yet. Upload at least 2 months of statements.")
            else:
                st.session_state["recurring_df"] = recurring_df
                st.success(f"Found **{len(recurring_df)}** recurring merchant(s).")

    with col_mark:
        if st.button("🔁 Mark All as Recurring in DB"):
            with get_session() as db:
                detector = RecurringDetector(db)
                updated = detector.mark_recurring_in_db()
            st.success(f"Marked **{updated}** transactions as recurring.")

    if "recurring_df" in st.session_state:
        df = st.session_state["recurring_df"]

        # Summary metrics
        total_monthly_recurring = df["avg_amount"].sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("🔁 Recurring Merchants",  len(df))
        c2.metric("💸 Est. Monthly Cost",    f"${total_monthly_recurring:,.2f}")
        c3.metric("📅 Est. Annual Cost",      f"${total_monthly_recurring * 12:,.2f}")

        # Table
        display = df.copy()
        display["avg_amount"]     = display["avg_amount"].apply(lambda x: f"${x:,.2f}")
        display["estimated_next"] = pd.to_datetime(display["estimated_next"]).dt.strftime("%B %Y")
        display = display.rename(columns={
            "canonical_name": "Merchant",
            "avg_amount": "Avg Monthly Cost",
            "months_seen": "Months Seen",
            "estimated_next": "Next Est. Date",
        })
        st.dataframe(display[["Merchant", "Avg Monthly Cost", "Months Seen", "Next Est. Date"]],
                     use_container_width=True, hide_index=True)

        # Bar chart
        fig = px.bar(
            df.sort_values("avg_amount", ascending=False),
            x="avg_amount",
            y="canonical_name",
            orientation="h",
            labels={"avg_amount": "Avg Monthly Cost ($)", "canonical_name": "Merchant"},
            color="months_seen",
            color_continuous_scale="Teal",
            title="Recurring Charges by Amount",
        )
        fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=50, b=0))
        st.plotly_chart(fig, use_container_width=True)


# ── Tab 2: Forecast ───────────────────────────────────────────────────────────────
with tab_forecast:
    st.markdown(
        "Uses **Linear Regression** (for < 8 months of data) or **ARIMA** "
        "(for 8+ months) to predict next month's spending."
    )

    if st.button("📈 Generate Forecast", type="primary"):
        try:
            with get_session() as db:
                forecaster = SpendForecaster(db)
                forecast   = forecaster.forecast_next_month()

            st.session_state["forecast"] = forecast
        except ValueError as e:
            st.warning(f"⚠️ {e}")
        except Exception as e:
            st.error(f"Forecast failed: {e}")

    if "forecast" in st.session_state:
        fc = st.session_state["forecast"]

        st.markdown(f"### Forecast for {fc['next_month_label']}")

        fc1, fc2, fc3 = st.columns(3)
        fc1.metric("📊 Predicted Total Spend", f"${fc['overall_forecast']:,.2f}")
        fc2.metric("🤖 Model Used",             fc["model_used"].upper())
        fc3.metric("📅 Months of History",      fc["periods_available"])

        st.markdown("---")
        st.markdown("#### Per-Category Forecast")

        cat_df = pd.DataFrame(
            [(cat, amt) for cat, amt in fc["by_category"].items() if amt > 0],
            columns=["Category", "Predicted Amount"],
        ).sort_values("Predicted Amount", ascending=False)

        col_a, col_b = st.columns([1, 1])

        with col_a:
            cat_df["Predicted Amount ($)"] = cat_df["Predicted Amount"].apply(lambda x: f"${x:,.2f}")
            st.dataframe(cat_df[["Category", "Predicted Amount ($)"]], use_container_width=True, hide_index=True)

        with col_b:
            fig = px.bar(
                cat_df.sort_values("Predicted Amount"),
                x="Predicted Amount",
                y="Category",
                orientation="h",
                color="Predicted Amount",
                color_continuous_scale="Blues",
                labels={"Predicted Amount": "Predicted ($)"},
            )
            fig.update_layout(
                showlegend=False,
                coloraxis_showscale=False,
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Donut of forecast
        fig_d = px.pie(
            cat_df,
            values="Predicted Amount",
            names="Category",
            hole=0.4,
            title=f"Predicted Spend Distribution — {fc['next_month_label']}",
            color_discrete_sequence=px.colors.qualitative.Pastel,
        )
        fig_d.update_traces(textposition="inside", textinfo="percent+label")
        fig_d.update_layout(showlegend=False)
        st.plotly_chart(fig_d, use_container_width=True)


# ── Tab 3: Model accuracy ─────────────────────────────────────────────────────────
with tab_accuracy:
    st.markdown(
        "Leave-one-out backtesting: for each month with data, "
        "the model is trained on all prior months and tested against the actual spend."
    )

    if st.button("📐 Run Accuracy Analysis"):
        try:
            with get_session() as db:
                forecaster = SpendForecaster(db)
                accuracy_df = forecaster.actual_vs_predicted()

            if accuracy_df.empty:
                st.warning("Need at least 3 months of data for accuracy analysis.")
            else:
                st.session_state["accuracy_df"] = accuracy_df
        except Exception as e:
            st.error(f"Error: {e}")

    if "accuracy_df" in st.session_state:
        acc_df = st.session_state["accuracy_df"]

        avg_error = acc_df["error_pct"].mean()
        st.metric("📉 Average Prediction Error", f"{avg_error:.1f}%",
                  help="Lower is better. < 15% is excellent for personal finance forecasting.")

        # Actual vs Predicted line chart
        fig_av = go.Figure()
        fig_av.add_trace(go.Scatter(
            x=acc_df["period"], y=acc_df["actual"],
            name="Actual", line=dict(color="#2196F3", width=3),
            mode="lines+markers",
        ))
        fig_av.add_trace(go.Scatter(
            x=acc_df["period"], y=acc_df["predicted"],
            name="Predicted", line=dict(color="#FF7043", dash="dash", width=2),
            mode="lines+markers",
        ))
        fig_av.update_layout(
            title="Actual vs Predicted Monthly Spend",
            xaxis_title="Month",
            yaxis_title="Spend ($)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=0, r=0, t=60, b=0),
        )
        st.plotly_chart(fig_av, use_container_width=True)

        # Error bar chart
        fig_err = px.bar(
            acc_df,
            x="period",
            y="error_pct",
            title="Prediction Error by Month (%)",
            labels={"error_pct": "Error (%)", "period": "Month"},
            color="error_pct",
            color_continuous_scale="RdYlGn_r",
        )
        fig_err.add_hline(y=15, line_dash="dot", annotation_text="15% target", line_color="green")
        fig_err.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_err, use_container_width=True)

        st.dataframe(acc_df, use_container_width=True, hide_index=True)
