"""
pages/1_Upload_Parse.py — Upload & Parse PDF Statements
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Upload & Parse", page_icon="📤", layout="wide")
st.title("📤 Upload & Parse Statements")
st.markdown("Upload your PDF credit card statements. Supports bulk upload — select all files at once.")


# ── Pipeline helper ────────────────────────────────────────────────────────────
def run_pipeline(statement_id: int, db) -> dict:
    from src.merchants.resolver import MerchantResolver
    from src.categorize.categorizer import TransactionCategorizer
    from src.storage.models import Transaction, CategorizationSource

    resolver    = MerchantResolver(db)
    categorizer = TransactionCategorizer(db)

    transactions = db.query(Transaction).filter(
        Transaction.statement_id == statement_id
    ).all()

    resolved = categorized = 0
    for txn in transactions:
        result = resolver.resolve(txn.raw_merchant)
        txn.merchant_id = result.merchant_id

        cat_result = categorizer.categorize_transaction(
            merchant_name=result.canonical_name,
            description=txn.description or "",
            merchant_id=result.merchant_id,
        )
        txn.category_id           = cat_result.category_id
        txn.categorization_source = cat_result.source
        txn.confidence_score      = cat_result.confidence
        txn.needs_review          = cat_result.needs_review

        if result.method != "unresolved":
            resolved += 1
        if cat_result.category_id:
            categorized += 1

    return {"resolved": resolved, "categorized": categorized, "total": len(transactions)}


def save_statement(uploaded_file, card_name, stmt_month, stmt_year, parse_result) -> dict:
    """Save one parsed statement + run pipeline. Returns stats dict."""
    from src.storage.database import get_session
    from src.storage.models import Statement, Transaction, TransactionType

    with get_session() as db:
        # Check for duplicate
        existing = db.query(Statement).filter(
            Statement.filename        == uploaded_file.name,
            Statement.statement_month == stmt_month,
            Statement.statement_year  == stmt_year,
        ).first()
        if existing:
            return {"skipped": True, "reason": "Already uploaded"}

        stmt = Statement(
            filename=uploaded_file.name,
            card_name=card_name,
            statement_month=stmt_month,
            statement_year=stmt_year,
            # encode/decode strips any non-UTF8 bytes that cause Windows charmap errors
            raw_text=parse_result.raw_text[:10000].encode("utf-8", errors="ignore").decode("utf-8"),
            parse_status="success",
        )
        db.add(stmt)
        db.flush()

        for _, row in parse_result.transactions.iterrows():
            txn_date = row["transaction_date"]
            if hasattr(txn_date, "date"):
                txn_date = txn_date.date()
            txn = Transaction(
                statement_id     = stmt.id,
                transaction_date = txn_date,
                raw_merchant     = row["raw_merchant"],
                amount           = float(row["amount"]),
                transaction_type = TransactionType(row["transaction_type"]),
                description      = str(row.get("description", "")),
            )
            db.add(txn)
        db.flush()

        stats = run_pipeline(stmt.id, db)
        stats["skipped"] = False
        stats["filename"] = uploaded_file.name
        return stats


# ── Parse helper ──────────────────────────────────────────────────────────────
def parse_file(uploaded_file):
    from src.parsing.pdf_parser import parse_statement
    temp_path = Path(tempfile.gettempdir()) / uploaded_file.name
    temp_path.write_bytes(uploaded_file.getvalue())

    year_guess = datetime.now().year
    for token in uploaded_file.name.replace("_", "-").split("-"):
        if token.isdigit() and 2020 <= int(token) <= 2030:
            year_guess = int(token)
            break

    return parse_statement(temp_path, statement_year=year_guess), year_guess


# ── File uploader ──────────────────────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "Choose PDF statement(s) — you can select all 36 at once",
    type=["pdf"],
    accept_multiple_files=True,
)

if not uploaded_files:
    st.info("👆 Upload one or more PDF statements. Select multiple files at once with Ctrl+A or Shift+Click.")
    st.stop()

st.success(f"✅ {len(uploaded_files)} file(s) selected.")

# ─────────────────────────────────────────────────────────────────────────────
# ── SAVE ALL BUTTON (batch mode — no clicking 36 times) ──────────────────────
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("⚡ Save All Statements at Once")
st.markdown(
    "Click **Save All** to parse and save every uploaded file in one go. "
    "Card name defaults to `TD Bank` — change it below if needed."
)

batch_card = st.text_input("Card Name (applied to all)", value="TD Bank")

if st.button("💾 Save All Statements", type="primary"):
    results_log = []
    progress = st.progress(0, text="Starting...")
    errors   = []

    for i, uf in enumerate(uploaded_files):
        progress.progress((i) / len(uploaded_files), text=f"Processing {uf.name}...")
        try:
            parse_result, year_guess = parse_file(uf)

            if parse_result.transactions.empty:
                results_log.append({"File": uf.name, "Status": "⚠️ No txns found",
                                    "Transactions": 0, "Error": str(parse_result.errors)})
                continue

            # Auto-detect month/year from filename
            month_guess = datetime.now().month
            parts = uf.name.replace("_", "-").split("-")
            for j, p in enumerate(parts):
                if p.isdigit() and 2020 <= int(p) <= 2030:
                    year_guess = int(p)
                if p.isdigit() and 1 <= int(p) <= 12:
                    month_guess = int(p)

            stats = save_statement(uf, batch_card, month_guess, year_guess, parse_result)

            if stats.get("skipped"):
                results_log.append({"File": uf.name, "Status": "⏭️ Skipped (duplicate)",
                                    "Transactions": 0, "Error": stats["reason"]})
            else:
                results_log.append({
                    "File": uf.name,
                    "Status": "✅ Saved",
                    "Transactions": stats["total"],
                    "Resolved": stats["resolved"],
                    "Categorized": stats["categorized"],
                    "Error": "",
                })

        except Exception as e:
            errors.append(f"{uf.name}: {e}")
            results_log.append({"File": uf.name, "Status": "❌ Error",
                                "Transactions": 0, "Error": str(e)})

    progress.progress(1.0, text="Done!")

    # Summary
    total_saved = sum(1 for r in results_log if r["Status"] == "✅ Saved")
    total_txns  = sum(r.get("Transactions", 0) for r in results_log)
    st.success(f"✅ Saved **{total_saved}** statements with **{total_txns}** total transactions.")

    if errors:
        st.error(f"❌ {len(errors)} errors:")
        for e in errors:
            st.text(e)

    log_df = pd.DataFrame(results_log)
    st.dataframe(log_df, use_container_width=True, hide_index=True)
    st.balloons()

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# ── INDIVIDUAL FILE PREVIEW (optional) ───────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("🔍 Preview & save individual files"):
    file_names = [f.name for f in uploaded_files]
    selected_name = st.selectbox("Select a file to preview", file_names)
    selected_uf   = next(f for f in uploaded_files if f.name == selected_name)

    if st.button("Parse selected file"):
        with st.spinner("Parsing..."):
            try:
                parse_result, year_guess = parse_file(selected_uf)
                st.session_state["preview_result"]   = parse_result
                st.session_state["preview_year"]     = year_guess
                st.session_state["preview_filename"] = selected_name
            except Exception as e:
                st.error(f"Parse failed: {e}")

    if st.session_state.get("preview_filename") == selected_name and \
       "preview_result" in st.session_state:
        r = st.session_state["preview_result"]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🏦 Bank",         r.detected_bank)
        c2.metric("📃 Pages",        r.page_count)
        c3.metric("💳 Transactions", len(r.transactions))
        c4.metric("🔧 Method",       r.parse_method)

        if r.errors:
            for err in r.errors:
                st.warning(err)

        if not r.transactions.empty:
            preview = r.transactions.head(10).copy()
            preview["transaction_date"] = preview["transaction_date"].dt.strftime("%Y-%m-%d")
            preview["amount"] = preview["amount"].apply(lambda x: f"${x:,.2f}")
            st.dataframe(preview[["transaction_date","raw_merchant","amount","transaction_type"]],
                         use_container_width=True, hide_index=True)

            mc1, mc2, mc3 = st.columns(3)
            card_name  = mc1.text_input("Card Name", value=r.detected_bank, key="ind_card")
            stmt_month = mc2.selectbox("Month", range(1,13),
                                       format_func=lambda m: datetime(2000,m,1).strftime("%B"),
                                       index=datetime.now().month-1, key="ind_month")
            stmt_year  = mc3.number_input("Year", 2015, 2030,
                                          value=st.session_state["preview_year"], key="ind_year")

            save_key = f"saved_{selected_name}"
            if st.session_state.get(save_key):
                st.success("✅ Already saved.")
            elif st.button("💾 Save This File"):
                try:
                    stats = save_statement(selected_uf, card_name, stmt_month, stmt_year, r)
                    if stats.get("skipped"):
                        st.warning(f"⏭️ Skipped: {stats['reason']}")
                    else:
                        st.session_state[save_key] = True
                        st.success(f"✅ Saved {stats['total']} transactions.")
                except Exception as e:
                    st.error(f"Save failed: {e}")

        with st.expander("🔍 Raw extracted text (debug)"):
            st.text_area("Raw Text", value=r.raw_text[:4000], height=250)