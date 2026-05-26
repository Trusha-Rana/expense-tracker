"""
app.py — Main Streamlit Entry Point
=====================================
This is the home page of the multi-page app.
Streamlit auto-discovers pages/ folder and builds the sidebar nav.

Responsibilities:
  - Initialize the DB (create tables if they don't exist)
  - Validate the DB connection and show a warning if it fails
  - Render the home dashboard (quick stats + navigation cards)
  - Store shared state in st.session_state
"""

import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Page config (must be FIRST Streamlit call) ──────────────────────────────────
st.set_page_config(
    page_title="Expense Tracker",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── DB init (runs once per session) ─────────────────────────────────────────────
@st.cache_resource
def initialize_database():
    """
    Called once per process. Creates all tables if they don't exist.
    cache_resource means this runs once — not on every page reload.
    """
    try:
        from src.storage.database import init_db, check_connection
        ok, msg = check_connection()
        if ok:
            init_db()
            return True, "Database connected and initialized."
        return False, f"Database connection failed: {msg}"
    except Exception as e:
        return False, str(e)

db_ok, db_msg = initialize_database()

# ── Sidebar ──────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 💳 Expense Tracker")
    st.markdown("---")

    if db_ok:
        st.success("🟢 Database connected")
    else:
        st.error(f"🔴 DB Error\n\n{db_msg}")
        st.info("Go to **Settings** to configure your database credentials.")

    st.markdown("---")
    st.markdown(
        """
        **Workflow:**
        1. 📤 Upload & Parse
        2. 🏪 Merchant Resolution
        3. 🏷️ Categorization
        4. 📊 Analytics
        5. 🔮 Forecasts
        6. ⚙️ Settings
        """,
    )
    st.markdown("---")
    mode = os.getenv("CATEGORIZATION_MODE", "rule_based")
    st.caption(f"Categorization: `{mode}`")
    st.caption(f"v1.0 | {datetime.now().strftime('%B %Y')}")


# ── Main content ──────────────────────────────────────────────────────────────────
st.title("💳 Privacy-First Expense Tracker")
st.markdown(
    "Track your spending **without connecting your bank**. "
    "Upload your credit card PDF statements and get instant analytics."
)

if not db_ok:
    st.warning(
        "⚠️ **Database not connected.** Configure your MySQL credentials in "
        "**Settings** before uploading statements.",
        icon="⚠️",
    )
    st.stop()

# ── Quick stats from DB ───────────────────────────────────────────────────────────
try:
    from src.storage.database import get_session
    from src.storage.models import Statement, Transaction
    from sqlalchemy import func

    with get_session() as db:
        total_statements  = db.query(func.count(Statement.id)).scalar() or 0
        total_transactions = db.query(func.count(Transaction.id)).scalar() or 0
        needs_review      = db.query(func.count(Transaction.id)).filter(
            Transaction.needs_review == True
        ).scalar() or 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📄 Statements Uploaded", total_statements)
    col2.metric("💸 Total Transactions",  total_transactions)
    col3.metric("🔍 Needs Review",        needs_review)
    col4.metric("🤖 Categorization Mode", os.getenv("CATEGORIZATION_MODE", "rule_based").replace("_", " ").title())

except Exception as e:
    st.error(f"Could not load stats: {e}")

st.markdown("---")

# ── Navigation cards ──────────────────────────────────────────────────────────────
st.subheader("Where would you like to go?")

c1, c2, c3 = st.columns(3)
with c1:
    st.info(
        "### 📤 Upload & Parse\n"
        "Upload a PDF credit card statement. "
        "We extract transactions automatically.",
    )
with c2:
    st.info(
        "### 🏪 Merchant Resolution\n"
        "Clean up messy merchant names. "
        "Review fuzzy matches and confirm mappings.",
    )
with c3:
    st.info(
        "### 🏷️ Categorization\n"
        "Review and correct transaction categories. "
        "Override AI/rule-based assignments.",
    )

c4, c5, c6 = st.columns(3)
with c4:
    st.success(
        "### 📊 Analytics\n"
        "Explore spending by category, merchant, and time period.",
    )
with c5:
    st.success(
        "### 🔮 Forecasts\n"
        "Detect recurring charges. "
        "Predict next month's spending.",
    )
with c6:
    st.warning(
        "### ⚙️ Settings\n"
        "Configure DB credentials, categorization mode, and API keys.",
    )

st.markdown("---")
st.caption(
    "🔒 **Your data never leaves your machine.** "
    "All processing is local. PDFs are parsed in memory and transactions stored in your own MySQL database."
)
