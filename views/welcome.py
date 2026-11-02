"""Entry page: the visitor chooses student or academy staff. Shown until a role is chosen."""
from __future__ import annotations

import streamlit as st

from src.ui_context import enter_as_staff, enter_as_student, get_context, staff_password_required

ctx = get_context()

st.title("REX AI")
st.markdown("Andalusia Academy  \nأكاديمية الأندلس")
if ctx.dataset.is_synthetic:
    st.warning(ctx.dataset.notice)

student, staff = st.columns(2, gap="large")

with student.container(border=True):
    st.subheader("I'm a student")
    st.markdown("Find the right training track, ask about a course, and register your interest.  \n"
                "اعثر على المسار المناسب واسأل عن الكورسات وسجّل اهتمامك.")
    if st.button("Continue as student", key="enter_student", type="primary", use_container_width=True):
        enter_as_student()
        st.rerun()

with staff.container(border=True):
    st.subheader("I'm academy staff")
    st.markdown("Manage documents, leads and offers, and work with the tender, curriculum, "
                "document and marketing agents.")
    if staff_password_required(ctx):
        password = st.text_input("Staff password", type="password", key="staff_pw")
    else:
        password = ""
        st.caption("ADMIN_PASSWORD is not set, so staff access is open. Set it in .env before a shared demo.")
    if st.button("Sign in as staff", key="enter_staff", use_container_width=True):
        if enter_as_staff(ctx, password):
            st.rerun()
        st.error("Wrong password. Ask the project admin for the staff password.")

st.caption("Contact details are saved only after you agree to the consent text in the chat.")
