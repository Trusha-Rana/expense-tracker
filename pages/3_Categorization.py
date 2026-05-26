"""
pages/3_Categorization.py — Transaction Categorization Review
==============================================================
USER FLOW:
  1. Shows transactions filtered by "needs review" flag (low-confidence)
  2. User can override category via dropdown
  3. On save → category updated, source set to "manual", needs_review cleared
  4. "Re-run Categorization" button re-processes all uncategorized transactions
  5. Stats tab shows source breakdown (rule vs AI vs manual)
"""

import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="Categorization", page_icon="🏷️", layout="wide")
st.title("🏷️ Transaction Categorization")
st.markdown("Review auto-categorized transactions, correct mistakes, and re-run the categorization engine.")

try:
    from src.storage.database import get_session
    from src.storage.models import Transaction, Category, Merchant, CategorizationSource
    from sqlalchemy import func, extract
except Exception as e:
    st.error(f"Import error: {e}")
    st.stop()


# ── Filters sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Filters")
    current_year = datetime.now().year

    with get_session() as db:
        years = [r[0] for r in db.query(
            extract("year", Transaction.transaction_date).label("yr")
        ).distinct().order_by("yr").all()]
        years = [int(y) for y in years if y]

    if not years:
        years = [current_year]

    selected_year  = st.selectbox("Year",  options=years, index=len(years) - 1)
    selected_month = st.selectbox(
        "Month", options=[0] + list(range(1, 13)),
        format_func=lambda m: "All Months" if m == 0 else datetime(2000, m, 1).strftime("%B"),
    )
    show_only_review = st.checkbox("⚠️ Show only 'Needs Review'", value=True)
    show_uncategorized = st.checkbox("❓ Show uncategorized only", value=False)


# ── Tabs ──────────────────────────────────────────────────────────────────────────
tab_review, tab_bulk, tab_stats = st.tabs(["🔍 Review Transactions", "⚡ Bulk Actions", "📊 Stats"])


# ── Tab 1: Row-by-row review ──────────────────────────────────────────────────────
with tab_review:
    with get_session() as db:
        q = (
            db.query(
                Transaction.id,
                Transaction.transaction_date,
                Transaction.raw_merchant,
                Merchant.canonical_name,
                Category.name.label("category_name"),
                Category.id.label("category_id"),
                Transaction.amount,
                Transaction.transaction_type,
                Transaction.categorization_source,
                Transaction.confidence_score,
                Transaction.needs_review,
            )
            .outerjoin(Merchant,  Transaction.merchant_id  == Merchant.id)
            .outerjoin(Category,  Transaction.category_id  == Category.id)
            .filter(extract("year", Transaction.transaction_date) == selected_year)
        )
        if selected_month:
            q = q.filter(extract("month", Transaction.transaction_date) == selected_month)
        if show_only_review:
            q = q.filter(Transaction.needs_review == True)
        if show_uncategorized:
            q = q.filter(Transaction.category_id == None)

        rows = q.order_by(Transaction.confidence_score.asc()).limit(300).all()

        all_categories = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        cat_map = {c.name: c.id for c in all_categories}
        cat_names = list(cat_map.keys())

    if not rows:
        if show_only_review:
            st.success("🎉 No transactions need review! Everything is categorized.")
        else:
            st.info("No transactions found for the selected filters.")
    else:
        st.info(f"Showing **{len(rows)}** transaction(s). Click a row to edit its category.")

        # Build editable DataFrame
        data = []
        for r in rows:
            data.append({
                "id":           r.id,
                "Date":         r.transaction_date,
                "Merchant":     r.canonical_name or r.raw_merchant,
                "Amount":       f"${r.amount:,.2f}",
                "Category":     r.category_name or "Uncategorized",
                "Source":       str(r.categorization_source or "unknown").replace("CategorizationSource.", ""),
                "Confidence":   f"{(r.confidence_score or 0) * 100:.0f}%",
                "Review":       "⚠️" if r.needs_review else "✅",
            })

        df = pd.DataFrame(data)
        st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("#### ✏️ Edit a Transaction")
        txn_options = {f"{r.transaction_date} | {r.canonical_name or r.raw_merchant} | ${r.amount:.2f}": r.id for r in rows}
        selected_label = st.selectbox("Select transaction to edit", options=list(txn_options.keys()))
        selected_id    = txn_options[selected_label]

        selected_row = next(r for r in rows if r.id == selected_id)
        current_cat  = selected_row.category_name or "Uncategorized"
        default_idx  = cat_names.index(current_cat) if current_cat in cat_names else 0

        new_cat = st.selectbox("New Category", options=cat_names, index=default_idx, key="edit_cat")

        if st.button("💾 Save Category Override"):
            with get_session() as db:
                txn = db.query(Transaction).get(selected_id)
                if txn:
                    txn.category_id           = cat_map[new_cat]
                    txn.categorization_source = CategorizationSource.MANUAL
                    txn.confidence_score      = 1.0
                    txn.needs_review          = False
            st.success(f"✅ Category updated to **{new_cat}**")
            st.rerun()


# ── Tab 2: Bulk actions ───────────────────────────────────────────────────────────
with tab_bulk:
    st.markdown("#### ⚡ Re-run Categorization")
    st.markdown(
        "Re-categorizes all transactions that were set by the auto-pipeline (not manually overridden). "
        "Useful after adding new YAML rules or switching to AI mode."
    )

    col1, col2 = st.columns(2)
    overwrite_manual = col1.checkbox(
        "Also overwrite manual overrides", value=False,
        help="If checked, even manually corrected categories will be re-run."
    )
    only_uncategorized = col2.checkbox(
        "Only process uncategorized", value=False,
        help="Skip transactions that already have any category assigned."
    )

    if st.button("🔄 Re-run Categorization Engine", type="primary"):
        try:
            from src.categorize.categorizer import TransactionCategorizer

            with get_session() as db:
                categorizer = TransactionCategorizer(db)

                q = db.query(Transaction).outerjoin(Merchant, Transaction.merchant_id == Merchant.id)

                if not overwrite_manual:
                    q = q.filter(Transaction.categorization_source != CategorizationSource.MANUAL)
                if only_uncategorized:
                    q = q.filter(Transaction.category_id == None)

                txns = q.all()
                updated = 0

                progress = st.progress(0, text="Processing...")
                for i, txn in enumerate(txns):
                    merchant_name = txn.merchant.canonical_name if txn.merchant else txn.raw_merchant
                    result = categorizer.categorize_transaction(
                        merchant_name=merchant_name,
                        description=txn.description or "",
                        merchant_id=txn.merchant_id,
                    )
                    txn.category_id           = result.category_id
                    txn.categorization_source = result.source
                    txn.confidence_score      = result.confidence
                    txn.needs_review          = result.needs_review
                    updated += 1

                    if i % 20 == 0:
                        progress.progress((i + 1) / len(txns), text=f"Processing {i+1}/{len(txns)}...")

                progress.progress(1.0, text="Done!")

            st.success(f"✅ Re-categorized **{updated}** transactions.")
            st.rerun()

        except Exception as e:
            st.error(f"Error: {e}")

    st.markdown("---")
    st.markdown("#### 🏷️ Bulk Assign by Merchant")
    st.markdown("Assign a category to ALL transactions from a specific merchant at once.")

    with get_session() as db:
        merchant_names = [m[0] for m in db.query(Merchant.canonical_name).order_by(Merchant.canonical_name).all()]

    if merchant_names:
        bm_col1, bm_col2 = st.columns(2)
        bulk_merchant = bm_col1.selectbox("Merchant", merchant_names, key="bulk_merchant")
        bulk_cat      = bm_col2.selectbox("Assign Category", cat_names, key="bulk_cat")

        if st.button("Apply to All Transactions from This Merchant"):
            with get_session() as db:
                merchant = db.query(Merchant).filter(Merchant.canonical_name == bulk_merchant).first()
                if merchant:
                    # Also update the merchant's default category
                    merchant.category_id = cat_map[bulk_cat]
                    merchant.is_verified = True

                    # Update all its transactions
                    updated = (
                        db.query(Transaction)
                        .filter(Transaction.merchant_id == merchant.id)
                        .update({
                            "category_id":           cat_map[bulk_cat],
                            "categorization_source": CategorizationSource.MANUAL,
                            "confidence_score":      1.0,
                            "needs_review":          False,
                        }, synchronize_session=False)
                    )
            st.success(f"✅ Updated **{updated}** transactions for **{bulk_merchant}** → **{bulk_cat}**")
            st.rerun()


# ── Tab 3: Stats ──────────────────────────────────────────────────────────────────
with tab_stats:
    with get_session() as db:
        source_counts = (
            db.query(Transaction.categorization_source, func.count(Transaction.id))
            .group_by(Transaction.categorization_source)
            .all()
        )
        total_txns      = db.query(func.count(Transaction.id)).scalar() or 0
        categorized_cnt = db.query(func.count(Transaction.id)).filter(Transaction.category_id != None).scalar() or 0
        review_cnt      = db.query(func.count(Transaction.id)).filter(Transaction.needs_review == True).scalar() or 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Transactions",  total_txns)
    c2.metric("✅ Categorized",       categorized_cnt)
    c3.metric("⚠️ Needs Review",      review_cnt)

    if total_txns > 0:
        st.progress(categorized_cnt / total_txns, text=f"Categorization coverage: {categorized_cnt/total_txns*100:.1f}%")

    if source_counts:
        st.markdown("#### Categorization Source Breakdown")
        src_df = pd.DataFrame(
            [(str(s).replace("CategorizationSource.", ""), c) for s, c in source_counts],
            columns=["Source", "Count"]
        )
        st.bar_chart(src_df.set_index("Source"))
