"""REX AI Streamlit entry point. Run with:  streamlit run app.py

This file is now only the router. It decides which pages exist for the current visitor:

    no role yet  ->  Welcome (choose "student" or "academy staff")
    student      ->  REX Assistant chat only
    staff        ->  chat with all five agents + the admin pages (knowledge base, leads, offers,
                     RFP, curriculum, documents, marketing)

Because a page that is not passed to st.navigation does not exist for that visitor, students can
neither see nor open the admin pages. Each admin page still calls require_admin() as a second check.
"""
from __future__ import annotations

import streamlit as st

from src.agents.base import AgentStatus
from src.config import ConfigError
from src.db.database import DatabaseError
from src.knowledge_base.loader import KnowledgeBaseError
from src.ui_context import current_role, get_context, leave_role

st.set_page_config(page_title="REX AI", page_icon="🎓", layout="centered")
# Let each paragraph pick its own direction so Arabic renders right-to-left.
st.markdown(
    "<style>[data-testid='stMarkdownContainer'] p, [data-testid='stMarkdownContainer'] li"
    "{unicode-bidi: plaintext; text-align: start;}</style>",
    unsafe_allow_html=True,
)

try:
    ctx = get_context()
except (ConfigError, KnowledgeBaseError, DatabaseError) as exc:
    st.error(f"REX AI could not start: {exc}")
    st.stop()

role = current_role()
welcome = st.Page("views/welcome.py", title="Welcome", icon="🎓", default=True)
chat = st.Page("views/chat.py", title="REX Assistant" if role != "staff" else "Assistant chat",
               icon="💬", url_path="chat", default=True)

if role is None:
    navigation = st.navigation([welcome], position="hidden")
elif role == "student":
    navigation = st.navigation([chat], position="hidden")
else:
    navigation = st.navigation({
        "Assistant": [chat],
        "Academy data": [st.Page("pages/1_Knowledge_Base.py", title="Knowledge base", icon="📚"),
                         st.Page("pages/2_Leads.py", title="Leads", icon="📇"),
                         st.Page("pages/5_Offers.py", title="Offers", icon="🏷️")],
        "Agents": [st.Page("pages/3_RFP_Opportunities.py", title="RFP opportunities", icon="📑"),
                   st.Page("pages/5_Curriculum.py", title="Curriculum", icon="🗂️"),
                   st.Page("pages/6_Documents.py", title="Documents", icon="📄"),
                   st.Page("pages/4_Marketing.py", title="Marketing", icon="📣")],
    })

if role is not None:
    with st.sidebar:
        if ctx.dataset.is_synthetic:
            st.warning(ctx.dataset.notice)
        if role == "staff":
            st.success("Signed in as academy staff")
            with st.expander("System status"):
                if not ctx.dataset.is_synthetic:
                    st.caption(ctx.dataset.notice)
                st.caption(f"Courses: {ctx.course_count} ({ctx.course_backend_status})  \n"
                           f"LLM: {ctx.llm_status}  \nDocuments: {ctx.rag_status}")
                for w in ctx.warnings:
                    st.warning(w)
                for info in ctx.registry.list_agents():
                    mark = "✅" if info.status == AgentStatus.IMPLEMENTED else "🕓 planned"
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
