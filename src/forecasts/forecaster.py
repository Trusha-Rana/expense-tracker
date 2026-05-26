"""
forecaster.py — Predictive Insights Engine
===========================================
TWO MAIN FEATURES:

1. RECURRING TRANSACTION DETECTION
   Finds subscriptions and regular bills automatically.
   Algorithm:
     - Group transactions by canonical merchant
     - If a merchant appears in 2+ different months with similar amounts (within 10%)
       → flagged as recurring
   This catches Netflix, Spotify, gym memberships, etc.

2. SPEND FORECASTING
   Predicts next month's total spending by category.
   Two models (we pick based on data volume):
     - LinearRegression (sklearn): fast, works with < 6 months of data
     - ARIMA (statsmodels): better for 6+ months with seasonal patterns

WHY NOT just use ARIMA always?
   ARIMA needs at least 8-12 observations to be reliable. With 2-3 months of
   statement data, linear regression is more accurate. The forecaster
   automatically picks the right model based on available data.
"""

from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from src.storage.models import Category, Merchant, Transaction, TransactionType


# ── Constants ──────────────────────────────────────────────────────────────────

_RECURRING_AMOUNT_TOLERANCE = 0.10   # 10% amount variance still counts as recurring
_ARIMA_MIN_OBSERVATIONS     = 8      # Use ARIMA only if we have this many monthly data points


# ── Recurring detection ─────────────────────────────────────────────────────────

class RecurringDetector:
    """
    Detects recurring transactions (subscriptions, bills, memberships).

    Returns a DataFrame of merchants flagged as recurring with:
      - canonical_name
      - avg_amount
      - months_seen
      - estimated_next_date
    """

    def __init__(self, session: Session):
        self.session = session

    def detect(self) -> pd.DataFrame:
        """
        Queries all transactions grouped by merchant+month, then finds
        merchants that appear regularly with stable amounts.
        """
        q = (
            self.session.query(
                Merchant.canonical_name,
                extract("year",  Transaction.transaction_date).label("year"),
                extract("month", Transaction.transaction_date).label("month"),
                func.avg(Transaction.amount).label("avg_amount"),
                func.count(Transaction.id).label("count"),
            )
            .join(Transaction, Transaction.merchant_id == Merchant.id)
            .filter(Transaction.transaction_type == TransactionType.DEBIT)
            .group_by(
                Merchant.id, Merchant.canonical_name,
                extract("year",  Transaction.transaction_date),
                extract("month", Transaction.transaction_date),
            )
            .order_by(Merchant.canonical_name)
        )

        rows = q.all()
        df = pd.DataFrame(rows, columns=["canonical_name", "year", "month", "avg_amount", "count"])

        if df.empty:
            return pd.DataFrame()

        # Group by merchant and analyse consistency
        recurring_rows = []
        for merchant, group in df.groupby("canonical_name"):
            months_seen = len(group)
            if months_seen < 2:
                continue

            amounts = group["avg_amount"].values
            mean_amount = amounts.mean()
            # Check if all amounts are within tolerance of the mean
            all_stable = all(
                abs(a - mean_amount) / mean_amount <= _RECURRING_AMOUNT_TOLERANCE
                for a in amounts
            )
            if all_stable:
                # Estimate next billing date (same day-of-month, next month)
                latest = group.sort_values(["year", "month"]).iloc[-1]
                next_month = int(latest["month"]) % 12 + 1
                next_year  = int(latest["year"]) + (1 if next_month == 1 else 0)
                try:
                    next_date = date(next_year, next_month, 1)
                except ValueError:
                    next_date = None

                recurring_rows.append({
                    "canonical_name":  merchant,
                    "avg_amount":      round(mean_amount, 2),
                    "months_seen":     months_seen,
                    "amount_stable":   all_stable,
                    "estimated_next":  next_date,
                })

        result = pd.DataFrame(recurring_rows)
        return result.sort_values("avg_amount", ascending=False) if not result.empty else result

    def mark_recurring_in_db(self) -> int:
        """
        Sets is_recurring=True on all detected recurring transactions.
        Returns count of updated rows.
        """
        recurring_df = self.detect()
        if recurring_df.empty:
            return 0

        recurring_names = recurring_df["canonical_name"].tolist()
        updated = (
            self.session.query(Transaction)
            .join(Merchant, Transaction.merchant_id == Merchant.id)
            .filter(Merchant.canonical_name.in_(recurring_names))
            .update({"is_recurring": True}, synchronize_session=False)
        )
        return updated


# ── Spend forecasting ────────────────────────────────────────────────────────────

class SpendForecaster:
    """
    Forecasts next month's total spending overall and per category.

    Uses month index (1, 2, 3...) as the X feature for LinearRegression.
    For categories with enough history, a per-category model is trained.
    Overall forecast is the sum of per-category forecasts + a global fallback.
    """

    def __init__(self, session: Session):
        self.session = session

    def _get_monthly_data(self) -> pd.DataFrame:
        """
        Builds a month-indexed DataFrame of spending per category.
        Returns: columns = category names, index = period (YYYY-MM string)
        """
        q = (
            self.session.query(
                extract("year",  Transaction.transaction_date).label("year"),
                extract("month", Transaction.transaction_date).label("month"),
                Category.name.label("category"),
                func.sum(Transaction.amount).label("total"),
            )
            .join(Category, Transaction.category_id == Category.id)
            .filter(Transaction.transaction_type == TransactionType.DEBIT)
            .group_by("year", "month", Category.name)
            .order_by("year", "month")
        )
        rows = q.all()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["year", "month", "category", "total"])
        df["year"]  = df["year"].astype(int)
        df["month"] = df["month"].astype(int)
        df["period"] = pd.to_datetime(
            df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2)
        )
        pivoted = df.pivot_table(
            index="period", columns="category", values="total", aggfunc="sum"
        ).fillna(0)
        return pivoted

    def _linear_forecast(self, series: pd.Series) -> float:
        """
        Fits LinearRegression on (month_index, spend) and predicts the next value.
        """
        X = np.arange(len(series)).reshape(-1, 1)
        y = series.values
        model = LinearRegression()
        model.fit(X, y)
        next_x = np.array([[len(series)]])
        prediction = model.predict(next_x)[0]
        return max(0.0, round(float(prediction), 2))   # Clamp negatives to 0

    def _arima_forecast(self, series: pd.Series) -> Optional[float]:
        """
        Uses ARIMA(1,1,1) for time-series forecasting with enough data.
        Returns None if statsmodels is unavailable or fit fails.
        """
        try:
            from statsmodels.tsa.arima.model import ARIMA
            model = ARIMA(series.values, order=(1, 1, 1))
            fit   = model.fit()
            forecast = fit.forecast(steps=1)
            return max(0.0, round(float(forecast[0]), 2))
        except Exception:
            return None

    def forecast_next_month(self) -> dict:
        """
        Returns a dict with:
          - overall_forecast: total predicted spend for next month
          - by_category: {category_name: predicted_amount}
          - model_used: "linear" | "arima"
          - periods_available: int (how many months of data we used)
          - next_month_label: "February 2025" etc.

        Raises ValueError if not enough data (< 2 months).
        """
        pivoted = self._get_monthly_data()

        if pivoted.empty or len(pivoted) < 2:
            raise ValueError(
                "Not enough data to forecast. Upload at least 2 months of statements."
            )

        periods_available = len(pivoted)
        use_arima = periods_available >= _ARIMA_MIN_OBSERVATIONS

        # Determine next month label
        last_period = pivoted.index[-1]
        next_period = last_period + pd.DateOffset(months=1)
        next_month_label = next_period.strftime("%B %Y")

        # Forecast per category
        by_category: dict[str, float] = {}
        model_used = "arima" if use_arima else "linear"

        for category in pivoted.columns:
            series = pivoted[category].dropna()
            if len(series) < 2:
                by_category[category] = round(float(series.mean()), 2)
                continue

            if use_arima:
                prediction = self._arima_forecast(series)
                if prediction is None:
                    prediction = self._linear_forecast(series)
                    model_used = "linear"  # Fell back
            else:
                prediction = self._linear_forecast(series)

            by_category[category] = prediction

        overall_forecast = round(sum(by_category.values()), 2)

        return {
            "overall_forecast":   overall_forecast,
            "by_category":        by_category,
            "model_used":         model_used,
            "periods_available":  periods_available,
            "next_month_label":   next_month_label,
        }

    def actual_vs_predicted(self) -> pd.DataFrame:
        """
        Compares actual monthly spending to what a leave-one-out forecast would have predicted.
        Used for the model accuracy chart.
        Columns: period, actual, predicted, error_pct
        """
        pivoted = self._get_monthly_data()
        if len(pivoted) < 3:
            return pd.DataFrame()

        totals = pivoted.sum(axis=1)   # Total per month
        records = []

        for i in range(2, len(totals)):
            # Train on everything up to month i, predict month i
            train = totals.iloc[:i]
            actual = totals.iloc[i]
            predicted = self._linear_forecast(train)
            error_pct = abs(actual - predicted) / actual * 100 if actual > 0 else 0
            records.append({
                "period":    totals.index[i].strftime("%b %Y"),
                "actual":    round(float(actual), 2),
                "predicted": predicted,
                "error_pct": round(error_pct, 1),
            })

        return pd.DataFrame(records)
