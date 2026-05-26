"""
pages/2_Merchant_Resolution.py — Merchant Review & Mapping
"""

import streamlit as st
import pandas as pd

st.set_page_config(page_title="Merchant Resolution", page_icon="🏪", layout="wide")
st.title("🏪 Merchant Resolution")
st.markdown(
    "Review unresolved merchant names. Clean names improve categorization accuracy "
    "for every future statement you upload."
)

try:
    from src.storage.database import get_session
    from src.storage.models import Merchant, MerchantRawName, Transaction, Category
    from sqlalchemy import func
except Exception as e:
    st.error(f"Import error: {e}")
    st.stop()

tab_unresolved, tab_all, tab_stats = st.tabs([
    "⚠️ Needs Review", "✅ All Merchants", "📊 Stats"
])


# ── Tab 1: Unresolved merchants ───────────────────────────────────────────────
with tab_unresolved:
    st.markdown("These merchant names couldn't be automatically resolved. Assign the correct canonical name.")

    # Load everything needed while session is open — avoid DetachedInstanceError
    with get_session() as db:
        rows = (
            db.query(
                Merchant.id,
                Merchant.canonical_name,
                func.count(Transaction.id).label("txn_count"),
                func.sum(Transaction.amount).label("total_spend"),
            )
            .outerjoin(Transaction, Transaction.merchant_id == Merchant.id)
            .filter(Merchant.is_verified == False)
            .group_by(Merchant.id, Merchant.canonical_name)
            .order_by(func.count(Transaction.id).desc())
            .all()
        )
        # Convert to plain dicts immediately — no ORM objects leave the session
        unverified = [
            {
                "id":          r.id,
                "name":        r.canonical_name,
                "txn_count":   r.txn_count or 0,
                "total_spend": float(r.total_spend or 0),
            }
            for r in rows
        ]

        # Load all categories as plain dicts too
        all_cats = db.query(Category.id, Category.name).filter(Category.is_active == True).all()
        cat_options = {c.name: c.id for c in all_cats}

    if not unverified:
        st.success("🎉 All merchants have been reviewed! No unresolved items.")
    else:
        st.info(f"**{len(unverified)}** merchants need review.")

        for merchant in unverified:
            label = (
                f"🔴 `{merchant['name']}` — "
                f"{merchant['txn_count']} transaction(s) — "
                f"${merchant['total_spend']:.2f}"
            )
            with st.expander(label, expanded=False):
                # Load raw name variants for this merchant
                with get_session() as db:
                    raw_names = (
                        db.query(
                            MerchantRawName.raw_name,
                            MerchantRawName.match_score,
                            MerchantRawName.source,
                        )
                        .filter(MerchantRawName.merchant_id == merchant["id"])
                        .all()
                    )
                    raw_list = [
                        {"raw_name": r.raw_name, "score": r.match_score, "source": r.source}
                        for r in raw_names
                    ]

                if raw_list:
                    st.markdown("**Raw PDF names seen:**")
                    st.dataframe(pd.DataFrame(raw_list), use_container_width=True, hide_index=True)

                col_a, col_b = st.columns([3, 1])
                new_name = col_a.text_input(
                    "Canonical Name",
                    value=merchant["name"],
                    key=f"name_{merchant['id']}",
                    placeholder="e.g. Tim Hortons",
                )
                cat_names  = ["(None)"] + list(cat_options.keys())
                cat_choice = col_b.selectbox("Category", cat_names, key=f"cat_{merchant['id']}")

                if st.button("✅ Confirm & Verify", key=f"confirm_{merchant['id']}"):
                    with get_session() as db:
                        m = db.query(Merchant).filter(Merchant.id == merchant["id"]).first()
                        if m:
                            m.canonical_name = new_name.strip()
                            m.is_verified    = True
                            if cat_choice != "(None)":
                                m.category_id = cat_options[cat_choice]
                    st.success(f"✅ Verified: **{new_name}**")
                    st.rerun()


# ── Tab 2: All merchants ──────────────────────────────────────────────────────
with tab_all:
    search = st.text_input("🔍 Search merchants", placeholder="Type to filter...")

    with get_session() as db:
        merchant_rows = (
            db.query(
                Merchant.id,
                Merchant.canonical_name,
                Merchant.is_verified,
                Category.name.label("category"),
                func.count(Transaction.id).label("txn_count"),
                func.sum(Transaction.amount).label("total_spend"),
            )
            .outerjoin(Category,     Merchant.category_id   == Category.id)
            .outerjoin(Transaction,  Transaction.merchant_id == Merchant.id)
            .group_by(Merchant.id, Merchant.canonical_name, Merchant.is_verified, Category.name)
            .order_by(func.sum(Transaction.amount).desc())
            .all()
        )
        # Convert to dicts inside session
        all_merchants = [
            {
                "Merchant":     r.canonical_name,
                "Verified":     "✅" if r.is_verified else "⚠️",
                "Category":     r.category or "—",
                "Transactions": r.txn_count or 0,
                "Total Spend":  f"${float(r.total_spend or 0):,.2f}",
            }
            for r in merchant_rows
        ]

    df = pd.DataFrame(all_merchants)
    if search and not df.empty:
        df = df[df["Merchant"].str.contains(search, case=False, na=False)]

    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(df)} merchants total")
    else:
        st.info("No merchants found. Upload a statement first.")


# ── Tab 3: Stats ──────────────────────────────────────────────────────────────
with tab_stats:
    with get_session() as db:
        total_merchants  = db.query(func.count(Merchant.id)).scalar() or 0
        verified_count   = db.query(func.count(Merchant.id)).filter(Merchant.is_verified == True).scalar() or 0
        total_raw_names  = db.query(func.count(MerchantRawName.id)).scalar() or 0

        method_rows = (
            db.query(MerchantRawName.source, func.count(MerchantRawName.id))
            .group_by(MerchantRawName.source)
            .all()
        )
        method_data = [(r[0] or "unknown", r[1]) for r in method_rows]

    unverified_count = total_merchants - verified_count

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Merchants",   total_merchants)
    c2.metric("✅ Verified",        verified_count)
    c3.metric("⚠️ Unverified",      unverified_count)
    c4.metric("Raw Name Variants", total_raw_names)

    if total_merchants > 0:
        pct = int(verified_count / total_merchants * 100)
        st.progress(pct / 100, text=f"Resolution progress: {pct}%")

    if method_data:
        st.markdown("---")
        st.markdown("#### Resolution Method Breakdown")
        method_df = pd.DataFrame(method_data, columns=["Method", "Count"])
        st.bar_chart(method_df.set_index("Method"))