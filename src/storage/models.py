"""
models.py — SQLAlchemy ORM Table Definitions
=============================================
WHY ORM instead of raw SQL?
  - Tables are Python classes → type hints, IDE autocomplete, refactoring
  - Database-agnostic: swap MySQL → SQLite by changing one env var
  - Relationships are explicit and easy to query (no manual JOINs for simple cases)
  - Alembic can auto-generate migrations by diffing these models vs live DB

TABLE RELATIONSHIPS:
  Statement ──< Transaction >── Merchant ──> Category
  (one statement has many transactions; each transaction maps to one merchant;
   each merchant belongs to one category)
"""

from __future__ import annotations

import enum
from datetime import datetime, date
from typing import List, Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Enum, Float,
    ForeignKey, Integer, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── Base class all models inherit from ─────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Enums ──────────────────────────────────────────────────────────────────────

class TransactionType(str, enum.Enum):
    """Debit = money out (purchase). Credit = money back (refund/payment)."""
    DEBIT  = "debit"
    CREDIT = "credit"


class SpendingType(str, enum.Enum):
    """Broad classification used in analytics: Need vs Want vs Saving."""
    NEED   = "need"
    WANT   = "want"
    SAVING = "saving"
    INCOME = "income"


class CategorizationSource(str, enum.Enum):
    """Tracks HOW a transaction was categorized — useful for auditing accuracy."""
    RULE     = "rule"       # Matched a keyword/pattern rule
    FUZZY    = "fuzzy"      # Matched via fuzzy merchant name lookup
    AI       = "ai"         # Categorized by Claude API
    MANUAL   = "manual"     # User overrode the category in the UI
    UNKNOWN  = "unknown"    # Nothing matched; left for manual review


# ── Table 1: statements ────────────────────────────────────────────────────────
class Statement(Base):
    """
    Represents one uploaded PDF statement file.
    Keeping statements as a separate table lets us:
      - Detect duplicate uploads (same filename + month)
      - Show per-statement vs cross-statement analytics
      - Link all transactions back to their source file
    """
    __tablename__ = "statements"

    id          : Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename    : Mapped[str]           = mapped_column(String(255), nullable=False)
    card_name   : Mapped[Optional[str]] = mapped_column(String(100))          # e.g. "TD Visa"
    statement_month: Mapped[Optional[int]] = mapped_column(Integer)           # 1-12
    statement_year : Mapped[Optional[int]] = mapped_column(Integer)           # e.g. 2024
    upload_date : Mapped[datetime]      = mapped_column(DateTime, default=func.now())
    raw_text    : Mapped[Optional[str]] = mapped_column(Text)                 # Full extracted PDF text (for debugging)
    parse_status: Mapped[str]           = mapped_column(String(20), default="pending")  # pending|success|failed

    # Relationship: one statement → many transactions
    transactions: Mapped[List["Transaction"]] = relationship(
        "Transaction", back_populates="statement", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("filename", "statement_month", "statement_year", name="uq_statement"),
    )

    def __repr__(self) -> str:
        return f"<Statement {self.filename} {self.statement_month}/{self.statement_year}>"


# ── Table 2: categories ────────────────────────────────────────────────────────
class Category(Base):
    """
    Spending categories (Groceries, Dining, Transport, etc.).
    Seeded from config/categories.yaml so non-devs can add categories via YAML
    without touching Python.
    """
    __tablename__ = "categories"

    id           : Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    name         : Mapped[str]           = mapped_column(String(100), nullable=False, unique=True)
    spending_type: Mapped[SpendingType]  = mapped_column(Enum(SpendingType), nullable=False)
    color_hex    : Mapped[Optional[str]] = mapped_column(String(7))   # e.g. "#FF6B6B" for charts
    icon         : Mapped[Optional[str]] = mapped_column(String(10))  # emoji icon for UI
    is_active    : Mapped[bool]          = mapped_column(Boolean, default=True)

    merchants   : Mapped[List["Merchant"]]   = relationship("Merchant", back_populates="category")
    transactions: Mapped[List["Transaction"]] = relationship("Transaction", back_populates="category")

    def __repr__(self) -> str:
        return f"<Category {self.name} ({self.spending_type})>"


# ── Table 3: merchants ─────────────────────────────────────────────────────────
class Merchant(Base):
    """
    Canonical (clean) merchant names.
    WHY a separate table?
      - Raw PDF names are messy: "TIM HORTONS #1234", "TIMHRTNS", "Tim Horton's"
        all resolve to one canonical Merchant row: "Tim Hortons"
      - Once a merchant is resolved + categorized, every future transaction from
        that merchant is auto-categorized instantly (no re-processing needed)
      - You can build a personal merchant knowledge base over time
    """
    __tablename__ = "merchants"

    id              : Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name  : Mapped[str]           = mapped_column(String(255), nullable=False, unique=True)
    category_id     : Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"))
    is_verified     : Mapped[bool]          = mapped_column(Boolean, default=False)  # User confirmed this mapping
    created_at      : Mapped[datetime]      = mapped_column(DateTime, default=func.now())

    category    : Mapped[Optional["Category"]]     = relationship("Category", back_populates="merchants")
    raw_names   : Mapped[List["MerchantRawName"]]  = relationship(
        "MerchantRawName", back_populates="merchant", cascade="all, delete-orphan"
    )
    transactions: Mapped[List["Transaction"]]       = relationship("Transaction", back_populates="merchant")

    def __repr__(self) -> str:
        return f"<Merchant {self.canonical_name}>"


# ── Table 4: merchant_raw_names ────────────────────────────────────────────────
class MerchantRawName(Base):
    """
    Stores every raw PDF name that mapped to a canonical merchant.
    This is a lookup table used by the fuzzy matcher.

    Example:
      Merchant(canonical_name="Tim Hortons")
        └── MerchantRawName("TIM HORTONS #1234")
        └── MerchantRawName("TIMHRTNS BRAMPTON")
        └── MerchantRawName("Tim Horton's")

    Next time "TIM HORTONS #9999" appears, exact or fuzzy match finds it quickly.
    """
    __tablename__ = "merchant_raw_names"

    id          : Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id : Mapped[int] = mapped_column(ForeignKey("merchants.id"), nullable=False)
    raw_name    : Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    match_score : Mapped[Optional[float]] = mapped_column(Float)   # Fuzzy score that created this link
    source      : Mapped[str]             = mapped_column(String(20), default="manual")  # manual|fuzzy|ai

    merchant: Mapped["Merchant"] = relationship("Merchant", back_populates="raw_names")

    def __repr__(self) -> str:
        return f"<RawName '{self.raw_name}' → {self.merchant_id}>"


# ── Table 5: transactions ──────────────────────────────────────────────────────
class Transaction(Base):
    """
    Core table — one row per credit card transaction.

    Design decisions:
      - raw_merchant stored alongside merchant_id so we never lose original data
      - categorization_source tracked for analytics (how accurate is each method?)
      - confidence_score stored to prioritize low-confidence rows for manual review
      - is_recurring flag set by the forecasting module
    """
    __tablename__ = "transactions"

    id                    : Mapped[int]                    = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    statement_id          : Mapped[int]                    = mapped_column(ForeignKey("statements.id"), nullable=False)
    transaction_date      : Mapped[date]                   = mapped_column(Date, nullable=False)
    raw_merchant          : Mapped[str]                    = mapped_column(String(255), nullable=False)
    merchant_id           : Mapped[Optional[int]]          = mapped_column(ForeignKey("merchants.id"))
    category_id           : Mapped[Optional[int]]          = mapped_column(ForeignKey("categories.id"))
    amount                : Mapped[float]                  = mapped_column(Float, nullable=False)
    transaction_type      : Mapped[TransactionType]        = mapped_column(Enum(TransactionType), nullable=False)
    description           : Mapped[Optional[str]]          = mapped_column(Text)
    categorization_source : Mapped[CategorizationSource]   = mapped_column(
        Enum(CategorizationSource), default=CategorizationSource.UNKNOWN
    )
    confidence_score      : Mapped[Optional[float]]        = mapped_column(Float)   # 0.0 – 1.0
    is_recurring          : Mapped[bool]                   = mapped_column(Boolean, default=False)
    needs_review          : Mapped[bool]                   = mapped_column(Boolean, default=False)
    created_at            : Mapped[datetime]               = mapped_column(DateTime, default=func.now())
    updated_at            : Mapped[datetime]               = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    statement : Mapped["Statement"]          = relationship("Statement", back_populates="transactions")
    merchant  : Mapped[Optional["Merchant"]] = relationship("Merchant", back_populates="transactions")
    category  : Mapped[Optional["Category"]] = relationship("Category", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction {self.transaction_date} {self.raw_merchant} ${self.amount:.2f}>"
