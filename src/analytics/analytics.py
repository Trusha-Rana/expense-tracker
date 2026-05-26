"""
analytics.py — Analytics Query Engine
=======================================
All analytics queries are centralized here, NOT in Streamlit pages.
This separation means:
  - Pages stay clean (just display logic)
  - Queries are testable in isolation
  - SQL can be optimized without touching UI code

Every method returns a pandas DataFrame or a scalar.
Streamlit pages consume these DataFrames directly with Plotly/Altair.
"""

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from src.storage.models import Category, Merchant, Statement, Transaction, TransactionType


class AnalyticsEngine:
    """
    All analytics queries. Instantiate with a session, call methods to get DataFrames.
    """

    def __init__(self, session: Session):
        self.session = session

    # ── Monthly Summary ──────────────────────────────────────────────────────────

    def monthly_spending_summary(
        self,
        year: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Returns total spending per month.
        Columns: year, month, month_name, total_spend, transaction_count

        Only counts DEBITS (actual purchases, not refunds/payments).
        """
        q = (
            self.session.query(
                extract("year",  Transaction.transaction_date).label("year"),
                extract("month", Transaction.transaction_date).label("month"),
                func.sum(Transaction.amount).label("total_spend"),
                func.count(Transaction.id).label("transaction_count"),
            )
            .filter(Transaction.transaction_type == TransactionType.DEBIT)
        )
        if year:
            q = q.filter(extract("year", Transaction.transaction_date) == year)

        q = q.group_by("year", "month").order_by("year", "month")
        rows = q.all()

        df = pd.DataFrame(rows, columns=["year", "month", "total_spend", "transaction_count"])
        if not df.empty:
            df["year"]  = df["year"].astype(int)
            df["month"] = df["month"].astype(int)
            df["month_name"] = pd.to_datetime(df["month"].astype(str), format="%m").dt.strftime("%B")
            df["period"] = df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2)
        return df

    # ── Category Breakdown ───────────────────────────────────────────────────────

    def spending_by_category(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Returns total spending per category for a given period.
        Columns: category_name, spending_type, color_hex, icon, total_spend, pct_of_total
        """
        q = (
            self.session.query(
                Category.name.label("category_name"),
                Category.spending_type.label("spending_type"),
                Category.color_hex.label("color_hex"),
                Category.icon.label("icon"),
                func.sum(Transaction.amount).label("total_spend"),
            )
            .join(Transaction, Transaction.category_id == Category.id)
            .filter(Transaction.transaction_type == TransactionType.DEBIT)
        )
        if year:
            q = q.filter(extract("year", Transaction.transaction_date) == year)
        if month:
            q = q.filter(extract("month", Transaction.transaction_date) == month)

        q = q.group_by(
            Category.id, Category.name, Category.spending_type,
            Category.color_hex, Category.icon,
        ).order_by(func.sum(Transaction.amount).desc())

        rows = q.all()
        df = pd.DataFrame(
            rows,
            columns=["category_name", "spending_type", "color_hex", "icon", "total_spend"],
        )

        if not df.empty:
            total = df["total_spend"].sum()
            df["pct_of_total"] = (df["total_spend"] / total * 100).round(1)

        return df

    # ── Top Merchants ────────────────────────────────────────────────────────────

    def top_merchants(
        self,
        limit: int = 15,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Returns the top N merchants by spend.
        Columns: canonical_name, category_name, total_spend, transaction_count, avg_transaction
        """
        q = (
            self.session.query(
                Merchant.canonical_name,
                Category.name.label("category_name"),
                func.sum(Transaction.amount).label("total_spend"),
                func.count(Transaction.id).label("transaction_count"),
                func.avg(Transaction.amount).label("avg_transaction"),
            )
            .join(Transaction, Transaction.merchant_id == Merchant.id)
            .outerjoin(Category, Category.id == Transaction.category_id)
            .filter(Transaction.transaction_type == TransactionType.DEBIT)
        )
        if year:
            q = q.filter(extract("year", Transaction.transaction_date) == year)
        if month:
            q = q.filter(extract("month", Transaction.transaction_date) == month)

        q = (
            q.group_by(Merchant.id, Merchant.canonical_name, Category.name)
            .order_by(func.sum(Transaction.amount).desc())
            .limit(limit)
        )
        rows = q.all()
        return pd.DataFrame(
            rows,
            columns=["canonical_name", "category_name", "total_spend",
                     "transaction_count", "avg_transaction"],
        )

    # ── Need vs Want vs Saving Breakdown ─────────────────────────────────────────

    def needs_wants_savings(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Returns spending split by spending_type (need/want/saving/income).
        Useful for the "50/30/20 rule" dashboard widget.
        """
        q = (
            self.session.query(
                Category.spending_type.label("spending_type"),
                func.sum(Transaction.amount).label("total"),
            )
            .join(Transaction, Transaction.category_id == Category.id)
            .filter(Transaction.transaction_type == TransactionType.DEBIT)
        )
        if year:
            q = q.filter(extract("year", Transaction.transaction_date) == year)
        if month:
            q = q.filter(extract("month", Transaction.transaction_date) == month)

        q = q.group_by(Category.spending_type)
        rows = q.all()
        df = pd.DataFrame(rows, columns=["spending_type", "total"])

        if not df.empty:
            total = df["total"].sum()
            df["pct"] = (df["total"] / total * 100).round(1)

        return df

    # ── Daily Spending Timeline ───────────────────────────────────────────────────

    def daily_spending(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Returns spend per day — used for the timeline/area chart.
        Columns: transaction_date, total_spend, cumulative_spend
        """
        q = (
            self.session.query(
                Transaction.transaction_date,
                func.sum(Transaction.amount).label("total_spend"),
            )
            .filter(Transaction.transaction_type == TransactionType.DEBIT)
        )
        if year:
            q = q.filter(extract("year", Transaction.transaction_date) == year)
        if month:
            q = q.filter(extract("month", Transaction.transaction_date) == month)

        q = q.group_by(Transaction.transaction_date).order_by(Transaction.transaction_date)
        rows = q.all()

        df = pd.DataFrame(rows, columns=["transaction_date", "total_spend"])
        if not df.empty:
            df["transaction_date"] = pd.to_datetime(df["transaction_date"])
            df["cumulative_spend"] = df["total_spend"].cumsum()
        return df

    # ── Transactions table (for the review UI) ────────────────────────────────────

    def get_transactions(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
        category_id: Optional[int] = None,
        needs_review: Optional[bool] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        Returns a filtered list of transactions for the UI table.
        """
        q = (
            self.session.query(
                Transaction.id,
                Transaction.transaction_date,
                Transaction.raw_merchant,
                Merchant.canonical_name,
                Category.name.label("category"),
                Category.icon,
                Transaction.amount,
                Transaction.transaction_type,
                Transaction.categorization_source,
                Transaction.confidence_score,
                Transaction.needs_review,
                Transaction.is_recurring,
            )
            .outerjoin(Merchant, Transaction.merchant_id == Merchant.id)
            .outerjoin(Category, Transaction.category_id == Category.id)
        )
        if year:
            q = q.filter(extract("year", Transaction.transaction_date) == year)
        if month:
            q = q.filter(extract("month", Transaction.transaction_date) == month)
        if category_id:
            q = q.filter(Transaction.category_id == category_id)
        if needs_review is not None:
            q = q.filter(Transaction.needs_review == needs_review)

        q = q.order_by(Transaction.transaction_date.desc()).limit(limit)
        rows = q.all()

        return pd.DataFrame(rows, columns=[
            "id", "date", "raw_merchant", "canonical_name", "category", "icon",
            "amount", "type", "cat_source", "confidence", "needs_review", "is_recurring",
        ])

    # ── KPI Scalars ───────────────────────────────────────────────────────────────

    def kpi_summary(self, year: int, month: Optional[int] = None) -> dict:
        """
        Returns key metrics for the dashboard header cards.
        Pass month=None for a full-year summary.
        """
        q = (
            self.session.query(
                func.sum(Transaction.amount),
                func.count(Transaction.id),
            )
            .filter(
                Transaction.transaction_type == TransactionType.DEBIT,
                extract("year", Transaction.transaction_date) == year,
            )
        )
        if month:
            q = q.filter(extract("month", Transaction.transaction_date) == month)

        total_spend, txn_count = q.one()

        avg_per_txn = (total_spend / txn_count) if txn_count else 0

        review_q = (
            self.session.query(func.count(Transaction.id))
            .filter(
                Transaction.needs_review == True,
                extract("year", Transaction.transaction_date) == year,
            )
        )
        if month:
            review_q = review_q.filter(
                extract("month", Transaction.transaction_date) == month
            )
        needs_review_count = review_q.scalar()

        return {
            "total_spend":         round(total_spend or 0, 2),
            "transaction_count":   txn_count or 0,
            "avg_per_transaction": round(avg_per_txn, 2),
            "needs_review":        needs_review_count or 0,
        }