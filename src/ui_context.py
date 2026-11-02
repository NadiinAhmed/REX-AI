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


# ------------------------------------------------------------------ entry page roles
# The role decides which pages exist in the navigation (see app.py). "staff" also sets the
# existing admin_ok flag, so every admin page's require_admin() check keeps working unchanged.
ROLE_KEY = "role"
_CONVERSATION_KEYS = ("messages", "agent_state")


def current_role() -> str | None:
    return st.session_state.get(ROLE_KEY)


def staff_password_required(ctx: AppContext) -> bool:
    return bool(ctx.settings and ctx.settings.admin_password)


def enter_as_student() -> None:
    _reset_conversation()
    st.session_state.pop("admin_ok", None)
    st.session_state[ROLE_KEY] = "student"


def enter_as_staff(ctx: AppContext, password: str) -> bool:
    """True when the password matches ADMIN_PASSWORD (or no password is configured)."""
    expected = ctx.settings.admin_password if ctx.settings else None
    if expected and not hmac.compare_digest(password or "", expected):
        return False
    _reset_conversation()
    st.session_state.admin_ok = True
    st.session_state[ROLE_KEY] = "staff"
    return True


def leave_role() -> None:
    """Back to the entry page. Clears the chat so the next person never sees it."""
    _reset_conversation()
    for key in (ROLE_KEY, "admin_ok"):
        st.session_state.pop(key, None)


def _reset_conversation() -> None:
    for key in _CONVERSATION_KEYS:
        st.session_state.pop(key, None)
