"""Admin: approved course offers. SHARED data: REX Assistant and the Marketing Agent read the same offers."""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from src.services.offer_service import OfferError
from src.ui_context import get_context, require_admin

st.set_page_config(page_title="REX AI · Offers", page_icon="🏷️")
st.title("🏷️ Course offers")
ctx = get_context()
if not require_admin(ctx):
    st.stop()
st.caption("An offer is used by the agents only when a named person approved it AND today is inside its dates.")

courses = {c.course_id: c for c in ctx.courses.list_approved()}
with st.form("new_offer"):
    course_id = st.selectbox("Course", list(courses), format_func=lambda cid: f"{cid} — {courses[cid].title}")
    title_en = st.text_input("Offer title (English)", placeholder="Early-bird discount")
    title_ar = st.text_input("Offer title (Arabic, optional)")
    kind = st.radio("Offer type", ["Discount %", "Fixed price"], horizontal=True)
    c1, c2 = st.columns(2)
    value = c1.number_input("Percent or price", min_value=0.0, step=1.0)
    currency = c2.text_input("Currency (fixed price only)", placeholder="EGP")
    d1, d2 = st.columns(2)
    valid_from = d1.date_input("Valid from", date.today())
    valid_until = d2.date_input("Valid until", date.today() + timedelta(days=14))
    proposed_by = st.text_input("Proposed by")
    if st.form_submit_button("Propose offer (pending approval)"):
        try:
            ctx.offers.propose(course_id=course_id, title_en=title_en, title_ar=title_ar or None,
                               discount_percent=value if kind == "Discount %" else None,
                               price_amount=value if kind == "Fixed price" else None,
                               currency=currency or None, valid_from=valid_from, valid_until=valid_until,
                               proposed_by=proposed_by)
            st.success("Saved as pending. It is not used until someone approves it.")
        except OfferError as exc:
            st.error(str(exc))

approver = st.text_input("Your name (to approve or reject)", key="approver")
today = date.today()
for o in ctx.offers.list_offers():
    state = "ACTIVE" if o.is_active(today) else o.approval_status.upper() + (
        " (outside dates)" if o.approval_status == "approved" else "")
    cols = st.columns([5, 1, 1])
    cols[0].markdown(f"**{o.course_id}** · {o.describe('en')} · {state}"
                     + (f" · approved by {o.approved_by}" if o.approved_by else ""))
    if o.approval_status == "pending":
        try:
            if cols[1].button("Approve", key=f"oa{o.id}"):
                ctx.offers.approve(o.id, approver)
                st.rerun()
            if cols[2].button("Reject", key=f"or{o.id}"):
                ctx.offers.reject(o.id, approver)
                st.rerun()
        except OfferError as exc:
            st.error(str(exc))
