"""REX AI Streamlit entry point. Run with:  streamlit run app.py
Admin pages (knowledge base, leads) are in pages/ and appear in the sidebar."""
from __future__ import annotations

import streamlit as st

from src.agents.base import AgentRequest, AgentStatus, ResponseMode
from src.config import ConfigError
from src.db.database import DatabaseError
from src.knowledge_base.loader import KnowledgeBaseError
from src.ui_context import get_context
from src.utils.logging_config import get_logger

AGENT = "rex_assistant"
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

st.title("🎓 REX Assistant")
st.caption("Ask in English or Arabic · اسأل بالعربية أو الإنجليزية")

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
            response = ctx.registry.dispatch(AGENT, AgentRequest.from_text(prompt, st.session_state.agent_state))
            st.session_state.agent_state = response.state
            text, meta = response.text, MODE_BADGE[response.mode]
            if response.sources:
                meta += " · Sources: " + "; ".join(dict.fromkeys(s.label() for s in response.sources))
        except Exception as exc:  # keep the UI alive; details go to the log
            logger.exception("Unhandled error while answering")
            text, meta = "Sorry, something went wrong. Please try again.", f"Error: {type(exc).__name__}"
        st.markdown(text)
        st.caption(meta)
    st.session_state.messages.append({"role": "assistant", "content": text, "meta": meta})
