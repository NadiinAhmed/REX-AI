"""REX AI Streamlit entry point. Run with:  streamlit run app.py

One chat for the whole system: every message goes to the OrchestratorAgent, which routes it to
the right agent. Students reach REX Assistant only; after the staff sign-in in the sidebar the
RFP, Curriculum, Documents and Marketing agents are reachable too. Review/approval screens stay
on the admin pages in pages/."""
from __future__ import annotations

import hmac

import streamlit as st

from src.agents.base import AgentRequest, AgentStatus, ResponseMode
from src.agents.orchestrator import ALL_AGENTS, PROFILES, PUBLIC_AGENTS
from src.config import ConfigError
from src.db.database import DatabaseError
from src.knowledge_base.loader import KnowledgeBaseError
from src.ui_context import get_context
from src.utils.logging_config import get_logger

logger = get_logger("app")
MODE_BADGE = {
    ResponseMode.LLM: "🤖 LLM-generated (grounded in academy data)",
    ResponseMode.DETERMINISTIC: "⚙️ Deterministic rule-based answer (no LLM)",
    ResponseMode.ERROR: "❌ Error",
}

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

with st.sidebar:
    st.header("REX AI")
    st.caption("Andalusia Academy graduation project")
    if ctx.dataset.is_synthetic:
        st.warning(ctx.dataset.notice)
    else:
        st.info(ctx.dataset.notice)
    st.write(f"**Courses:** {ctx.course_count} ({ctx.course_backend_status})  \n"
             f"**LLM:** {ctx.llm_status}  \n**Documents:** {ctx.rag_status}")
    for w in ctx.warnings:
        st.warning(w)
    st.subheader("Agents")
    for info in ctx.registry.list_agents():
        icon = "✅" if info.status == AgentStatus.IMPLEMENTED else "🕓 planned"
        st.write(f"{icon} {info.description}")
    if st.button("Start new conversation"):
        for key in ("messages", "agent_state"):
            st.session_state.pop(key, None)
        st.rerun()
    st.caption("Contact details are saved only after you agree to the consent text (type \"register\").")

    st.subheader("Staff")
    expected = ctx.settings.admin_password if ctx.settings else None
    if st.session_state.get("admin_ok"):
        st.success("Staff mode: all agents available.")
    elif expected:
        entered = st.text_input("Staff password", type="password", key="staff_pw")
        if entered and hmac.compare_digest(entered, expected):
            st.session_state.admin_ok = True
            st.rerun()
        elif entered:
            st.error("Wrong password.")
    elif st.checkbox("Staff mode (ADMIN_PASSWORD is not set, so this is open)"):
        st.session_state.admin_ok = True
        st.rerun()

staff = bool(st.session_state.get("admin_ok"))
allowed = ALL_AGENTS if staff else PUBLIC_AGENTS
st.title("🎓 REX AI" if staff else "🎓 REX Assistant")
st.caption("Ask in English or Arabic · اسأل بالعربية أو الإنجليزية"
           + (" · Requests are routed to the right agent automatically. Type /help for commands." if staff else ""))

st.session_state.setdefault("messages", [])
st.session_state.setdefault("agent_state", {})

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("meta"):
            st.caption(msg["meta"])

prompt = st.chat_input("e.g. I'm a final-year student and want to work in data")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            response = ctx.orchestrator.handle(AgentRequest.from_text(prompt, st.session_state.agent_state),
                                               allowed_agents=allowed)
            st.session_state.agent_state = response.state
            text, meta = response.text, MODE_BADGE[response.mode]
            routed = response.data.get("routing", {})
            if staff and routed.get("action") == "dispatch":
                meta = f"→ {PROFILES[routed['agent']].label_en} ({routed['reason']}) · " + meta
            if response.sources:
                meta += " · Sources: " + "; ".join(dict.fromkeys(s.label() for s in response.sources))
        except Exception as exc:  # keep the UI alive; details go to the log
            logger.exception("Unhandled error while answering")
            text, meta = "Sorry, something went wrong. Please try again.", f"Error: {type(exc).__name__}"
        st.markdown(text)
        st.caption(meta)
    st.session_state.messages.append({"role": "assistant", "content": text, "meta": meta})
