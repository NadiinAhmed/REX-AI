"""Admin: leads captured with consent."""
from __future__ import annotations

import streamlit as st

from src.ui_context import get_context, require_admin

st.set_page_config(page_title="REX AI · Leads", page_icon="📇")
st.title("📇 Leads (with consent)")
ctx = get_context()
if not require_admin(ctx):
    st.stop()
leads = ctx.leads.list()
st.caption(f"{len(leads)} lead(s). Each was saved only after the person agreed to consent text version shown.")
st.dataframe([{
    "created": l.created_at.strftime("%Y-%m-%d %H:%M"), "name": l.name, "email": l.email, "phone": l.phone,
    "courses": ", ".join(l.interested_course_ids), "language": l.language,
    "consent": f"{l.consent_version} @ {l.consent_given_at:%Y-%m-%d %H:%M}", "status": l.status,
} for l in leads], use_container_width=True)
