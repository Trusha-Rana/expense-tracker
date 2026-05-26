"""
pages/6_Settings.py — Settings & Configuration
================================================
Sections:
  1. Database connection test + credentials guide
  2. Categorization mode switcher
  3. Category management (view/add/toggle active)
  4. Data management (delete statements, reset DB)
  5. System info
"""

import os
import streamlit as st
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv, set_key

load_dotenv()

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings")

ENV_PATH = Path(".env")

tab_db, tab_cat, tab_categories, tab_data, tab_info = st.tabs([
    "🗄️ Database", "🤖 Categorization", "🏷️ Categories", "🗑️ Data Management", "ℹ️ System Info"
])


# ── Tab 1: Database ───────────────────────────────────────────────────────────────
with tab_db:
    st.markdown("### MySQL Connection Settings")
    st.info(
        "💡 **How to set up MySQL:**\n"
        "1. Install MySQL and create a database: `CREATE DATABASE expense_tracker;`\n"
        "2. Create a user: `CREATE USER 'tracker'@'localhost' IDENTIFIED BY 'yourpassword';`\n"
        "3. Grant access: `GRANT ALL ON expense_tracker.* TO 'tracker'@'localhost';`\n"
        "4. Fill in the fields below and click **Test Connection**."
    )

    col1, col2 = st.columns(2)
    db_host = col1.text_input("Host",     value=os.getenv("DB_HOST", "localhost"))
    db_port = col2.text_input("Port",     value=os.getenv("DB_PORT", "3306"))
    db_name = col1.text_input("Database", value=os.getenv("DB_NAME", "expense_tracker"))
    db_user = col2.text_input("User",     value=os.getenv("DB_USER", "root"))
    db_pass = st.text_input("Password",   value=os.getenv("DB_PASSWORD", ""), type="password")

    c1, c2 = st.columns(2)

    if c1.button("🔌 Test Connection"):
        # Temporarily override env for the test
        os.environ["DB_HOST"]     = db_host
        os.environ["DB_PORT"]     = db_port
        os.environ["DB_NAME"]     = db_name
        os.environ["DB_USER"]     = db_user
        os.environ["DB_PASSWORD"] = db_pass

        # Force recreation of engine with new settings
        try:
            import importlib
            import src.storage.database as db_module
            importlib.reload(db_module)
            ok, msg = db_module.check_connection()
            if ok:
                st.success(f"✅ {msg}")
            else:
                st.error(f"❌ {msg}")
        except Exception as e:
            st.error(f"❌ {e}")

    if c2.button("💾 Save to .env"):
        if not ENV_PATH.exists():
            ENV_PATH.write_text("")
        set_key(str(ENV_PATH), "DB_HOST",     db_host)
        set_key(str(ENV_PATH), "DB_PORT",     db_port)
        set_key(str(ENV_PATH), "DB_NAME",     db_name)
        set_key(str(ENV_PATH), "DB_USER",     db_user)
        set_key(str(ENV_PATH), "DB_PASSWORD", db_pass)
        st.success("✅ Saved to .env. Restart the app to apply changes.")


# ── Tab 2: Categorization ─────────────────────────────────────────────────────────
with tab_cat:
    st.markdown("### Categorization Mode")

    current_mode = os.getenv("CATEGORIZATION_MODE", "rule_based")

    mode = st.radio(
        "Select mode:",
        options=["rule_based", "hybrid", "ai"],
        index=["rule_based", "hybrid", "ai"].index(current_mode),
        format_func=lambda m: {
            "rule_based": "📋 Rule-Based Only (fast, no API cost)",
            "hybrid":     "🔀 Hybrid (rules first, AI for unknowns)",
            "ai":         "🤖 AI Only (most accurate, uses Claude API)",
        }[m],
    )

    if mode in ("hybrid", "ai"):
        st.markdown("#### Claude API Key")
        api_key = st.text_input(
            "ANTHROPIC_API_KEY",
            value=os.getenv("ANTHROPIC_API_KEY", ""),
            type="password",
            help="Get your key at https://console.anthropic.com",
        )
        threshold = st.slider(
            "AI Fallback Threshold (hybrid mode)",
            min_value=0.0, max_value=1.0,
            value=float(os.getenv("AI_FALLBACK_THRESHOLD", "0.6")),
            step=0.05,
            help="If rule-based confidence < this value, AI is called. Lower = more AI usage.",
        )
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        threshold = float(os.getenv("AI_FALLBACK_THRESHOLD", "0.6"))

    if st.button("💾 Save Categorization Settings"):
        if not ENV_PATH.exists():
            ENV_PATH.write_text("")
        set_key(str(ENV_PATH), "CATEGORIZATION_MODE",   mode)
        set_key(str(ENV_PATH), "AI_FALLBACK_THRESHOLD", str(threshold))
        if api_key:
            set_key(str(ENV_PATH), "ANTHROPIC_API_KEY", api_key)
        st.success("✅ Saved. Restart the app to apply changes.")

    st.markdown("---")
    st.markdown("#### Mode Comparison")
    compare = pd.DataFrame([
        {"Mode": "Rule-Based",  "Speed": "⚡⚡⚡", "Accuracy": "⭐⭐",   "API Cost": "Free"},
        {"Mode": "Hybrid",      "Speed": "⚡⚡",   "Accuracy": "⭐⭐⭐",  "API Cost": "Low"},
        {"Mode": "AI Only",     "Speed": "⚡",     "Accuracy": "⭐⭐⭐⭐", "API Cost": "Higher"},
    ])
    st.table(compare)


# ── Tab 3: Category Management ────────────────────────────────────────────────────
with tab_categories:
    st.markdown("### Category Management")
    st.markdown(
        "Categories are defined in `config/categories.yaml`. "
        "Here you can view DB-synced categories and toggle them active/inactive."
    )

    try:
        from src.storage.database import get_session
        from src.storage.models import Category
        from sqlalchemy import func

        with get_session() as db:
            cats = db.query(Category).order_by(Category.spending_type, Category.name).all()

        if not cats:
            st.info("No categories in DB yet. Restart the app to seed from categories.yaml.")
        else:
            cat_data = [
                {
                    "ID": c.id,
                    "Icon": c.icon or "",
                    "Name": c.name,
                    "Type": str(c.spending_type).replace("SpendingType.", ""),
                    "Color": c.color_hex or "",
                    "Active": c.is_active,
                }
                for c in cats
            ]
            df = pd.DataFrame(cat_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("#### Toggle Category Active/Inactive")
            toggle_cat = st.selectbox("Category", [c.name for c in cats])
            if st.button("Toggle Active Status"):
                with get_session() as db:
                    cat = db.query(Category).filter(Category.name == toggle_cat).first()
                    if cat:
                        cat.is_active = not cat.is_active
                        status = "active" if cat.is_active else "inactive"
                st.success(f"**{toggle_cat}** is now **{status}**.")
                st.rerun()

            st.markdown("---")
            st.markdown("#### Re-seed Categories from YAML")
            if st.button("🌱 Re-seed from categories.yaml"):
                with get_session() as db:
                    from src.categorize.categorizer import CategoryRules
                    rules = CategoryRules()
                    rules.seed_db(db)
                st.success("✅ Categories re-seeded from config/categories.yaml.")
                st.rerun()

    except Exception as e:
        st.error(f"Error loading categories: {e}")


# ── Tab 4: Data Management ────────────────────────────────────────────────────────
with tab_data:
    st.markdown("### Data Management")
    st.warning("⚠️ All destructive actions below are irreversible.")

    try:
        from src.storage.database import get_session
        from src.storage.models import Statement, Transaction
        from sqlalchemy import func

        with get_session() as db:
            statements = db.query(Statement).order_by(Statement.upload_date.desc()).all()

        if statements:
            st.markdown("#### Delete a Statement")
            stmt_options = {
                f"{s.card_name or 'Unknown'} — {s.statement_month}/{s.statement_year} ({s.filename})": s.id
                for s in statements
            }
            selected_stmt = st.selectbox("Select Statement to Delete", list(stmt_options.keys()))

            if st.button("🗑️ Delete Statement + Its Transactions", type="secondary"):
                stmt_id = stmt_options[selected_stmt]
                with get_session() as db:
                    db.query(Transaction).filter(Transaction.statement_id == stmt_id).delete()
                    db.query(Statement).filter(Statement.id == stmt_id).delete()
                st.success(f"✅ Deleted: {selected_stmt}")
                st.rerun()

        st.markdown("---")
        st.markdown("#### ☢️ Full Reset")
        confirm_reset = st.text_input(
            "Type RESET to confirm deleting ALL data",
            placeholder="RESET",
        )
        if st.button("🔴 Delete All Data", disabled=confirm_reset != "RESET"):
            with get_session() as db:
                db.query(Transaction).delete()
                db.query(Statement).delete()
                from src.storage.models import MerchantRawName, Merchant
                db.query(MerchantRawName).delete()
                db.query(Merchant).delete()
            st.success("✅ All data deleted. The app is reset to a clean state.")
            st.rerun()

    except Exception as e:
        st.error(f"Error: {e}")


# ── Tab 5: System Info ────────────────────────────────────────────────────────────
with tab_info:
    st.markdown("### System Information")

    import sys
    import platform

    info = {
        "Python Version":    sys.version.split(" ")[0],
        "Platform":          platform.system(),
        "Streamlit Version": st.__version__,
        "DB Host":           os.getenv("DB_HOST", "not set"),
        "DB Name":           os.getenv("DB_NAME", "not set"),
        "Cat. Mode":         os.getenv("CATEGORIZATION_MODE", "rule_based"),
        "AI Key Set":        "Yes" if os.getenv("ANTHROPIC_API_KEY") else "No",
        "Config Path":       str(Path("config/categories.yaml").resolve()),
    }

    for key, val in info.items():
        st.text(f"{key:<25} {val}")

    st.markdown("---")
    st.markdown("#### Installed Packages")
    try:
        result = __import__("subprocess").run(
            ["pip", "list", "--format=columns"], capture_output=True, text=True
        )
        with st.expander("View installed packages"):
            st.code(result.stdout)
    except Exception:
        st.info("Could not list packages.")
