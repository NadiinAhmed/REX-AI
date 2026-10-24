"""Shared Streamlit helpers: one cached AppContext for the chat page and the admin pages."""
from __future__ import annotations

import hmac

import streamlit as st

from src.bootstrap import AppContext, build_app
from src.config import load_settings
from src.utils.logging_config import configure_logging


@st.cache_resource
def get_context() -> AppContext:
    settings = load_settings()
    configure_logging(settings.log_level)
    return build_app(settings)


def require_admin(ctx: AppContext) -> bool:
    """Gate admin pages when ADMIN_PASSWORD is set. MVP-level protection, not real auth."""
    expected = ctx.settings.admin_password if ctx.settings else None
    if not expected:
        st.info("ADMIN_PASSWORD is not set, so this admin page is open. Set it in .env before any shared demo.")
        return True
    if st.session_state.get("admin_ok"):
        return True
    entered = st.text_input("Admin password", type="password")
    if entered and hmac.compare_digest(entered, expected):
        st.session_state.admin_ok = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    return False
