"""REX AI Streamlit entry point. Run with:  streamlit run app.py

This file is now only the router. It decides which pages exist for the current visitor:

    no role yet  ->  Welcome (choose "student" or "academy staff")
    student      ->  REX Assistant chat only
    staff        ->  chat with all five agents + the admin pages (knowledge base, leads, offers,
                     RFP, curriculum, documents, marketing)

Because a page that is not passed to st.navigation does not exist for that visitor, students can
neither see nor open the admin pages. The folder is called admin_pages/ (not pages/) on purpose:
Streamlit auto-lists a folder named pages/ in the sidebar before this script runs. Each admin page still calls require_admin() as a second check.
"""
from __future__ import annotations

import streamlit as st

from src.agents.base import AgentStatus
from src.config import ConfigError
from src.db.database import DatabaseError
from src.knowledge_base.loader import KnowledgeBaseError
from src.ui_context import current_role, get_context, leave_role
from src.ui_theme import apply_theme, signed_in_note

st.set_page_config(page_title="REX AI · Andalusia Academy", page_icon="assets/andalusia_mark.png",
                   layout="centered")
apply_theme()   # Andalusia brand colors, banner styles, logo; also keeps Arabic right-to-left

try:
    ctx = get_context()
except (ConfigError, KnowledgeBaseError, DatabaseError) as exc:
    st.error(f"REX AI could not start: {exc}")
    st.stop()

role = current_role()
welcome = st.Page("views/welcome.py", title="Welcome", icon=":material/home:", default=True)
chat = st.Page("views/chat.py", title="REX Assistant" if role != "staff" else "Assistant chat",
               icon=":material/chat:", url_path="chat", default=True)

if role is None:
    navigation = st.navigation([welcome], position="hidden")
elif role == "student":
    navigation = st.navigation([chat], position="hidden")
else:
    navigation = st.navigation({
        "Assistant": [chat],
        "Academy data": [st.Page("admin_pages/1_Knowledge_Base.py", title="Knowledge base", icon=":material/library_books:"),
                         st.Page("admin_pages/2_Leads.py", title="Leads", icon=":material/contacts:"),
                         st.Page("admin_pages/5_Offers.py", title="Offers", icon=":material/sell:")],
        "Agents": [st.Page("admin_pages/3_RFP_Opportunities.py", title="RFP opportunities", icon=":material/gavel:"),
                   st.Page("admin_pages/5_Curriculum.py", title="Curriculum", icon=":material/school:"),
                   st.Page("admin_pages/6_Documents.py", title="Documents", icon=":material/description:"),
                   st.Page("admin_pages/4_Marketing.py", title="Marketing", icon=":material/campaign:")],
    })

if role is not None:
    with st.sidebar:
        if ctx.dataset.is_synthetic:
            st.warning(ctx.dataset.notice)
        if role == "staff":
            signed_in_note("Signed in as academy staff")
            with st.expander("System status"):
                if not ctx.dataset.is_synthetic:
                    st.caption(ctx.dataset.notice)
                st.caption(f"Courses: {ctx.course_count} ({ctx.course_backend_status})  \n"
                           f"LLM: {ctx.llm_status}  \nDocuments: {ctx.rag_status}")
                for w in ctx.warnings:
                    st.warning(w)
                for info in ctx.registry.list_agents():
                    mark = "Ready:" if info.status == AgentStatus.IMPLEMENTED else "Planned:"
                    st.caption(f"{mark} {info.description}")
        else:
            st.caption("Contact details are saved only after you agree to the consent text "
                       "(type \"register\" / \"سجلني\").")
        if st.button("Start new conversation", width="stretch"):
            for key in ("messages", "agent_state"):
                st.session_state.pop(key, None)
            st.rerun()
        if st.button("Sign out" if role == "staff" else "Change role", key="leave_role",
                     width="stretch"):
            leave_role()
            st.rerun()

navigation.run()
