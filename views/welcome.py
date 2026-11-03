"""Entry page: the visitor chooses student or academy staff. Shown until a role is chosen."""
from __future__ import annotations

import streamlit as st

from src.ui_context import enter_as_staff, enter_as_student, get_context, staff_password_required
from src.ui_theme import cover, footer

ctx = get_context()

cover("REX AI", "Your guide to Andalusia Academy's training programs")
if ctx.dataset.is_synthetic:
    st.warning(ctx.dataset.notice)

student, staff = st.columns(2, gap="medium")

with student.container(key="card_student"):
    st.subheader("For students")
    st.markdown("Find the right training track, ask about a course, and register your interest.")
    st.markdown("اعثر على المسار المناسب واسأل عن الكورسات وسجّل اهتمامك.")
    if st.button("Continue as student", key="enter_student", type="primary", width="stretch"):
        enter_as_student()
        st.rerun()

with staff.container(key="card_staff"):
    st.subheader("For academy staff")
    st.markdown("Manage documents, leads and offers, and work with the tender, curriculum, "
                "document and marketing agents.")
    if staff_password_required(ctx):
        password = st.text_input("Staff password", type="password", key="staff_pw")
    else:
        password = ""
        st.caption("ADMIN_PASSWORD is not set, so staff access is open. Set it in .env before a shared demo.")
    if st.button("Sign in as staff", key="enter_staff", width="stretch"):
        if enter_as_staff(ctx, password):
            st.rerun()
        st.error("Wrong password. Ask the project admin for the staff password.")

st.caption("Contact details are saved only after you agree to the consent text in the chat.")
footer()
