"""REX AI Streamlit entry point. Run with:  streamlit run app.py"""
from __future__ import annotations

import streamlit as st

from src.agents.base import AgentRequest, AgentStatus, ResponseMode
from src.bootstrap import AppContext, build_app
from src.config import ConfigError, load_settings
from src.knowledge_base.loader import KnowledgeBaseError
from src.utils.logging_config import configure_logging, get_logger

AGENT = "rex_assistant"
logger = get_logger("app")
MODE_BADGE = {
    ResponseMode.LLM: "🤖 LLM-generated (grounded in course data)",
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


@st.cache_resource
def get_context() -> AppContext:
    settings = load_settings()
    configure_logging(settings.log_level)
    return build_app(settings)


try:
    ctx = get_context()
except (ConfigError, KnowledgeBaseError) as exc:
    st.error(f"REX AI could not start: {exc}")
    st.stop()

with st.sidebar:
    st.header("REX AI")
    st.caption("Sprint 1 prototype — Andalusia Academy graduation project")
    if ctx.dataset.is_synthetic:
        st.warning(ctx.dataset.notice)
    st.write(f"**Courses loaded:** {ctx.course_count}  \n**LLM:** {ctx.llm_status}")
    st.subheader("Agents")
    for info in ctx.registry.list_agents():
        icon = "✅" if info.status == AgentStatus.IMPLEMENTED else "🕓 planned"
        st.write(f"{icon} {info.description}")
    if st.button("Start new conversation"):
        st.session_state.clear()
        st.rerun()
    st.caption("This prototype does not collect names, phone numbers or emails.")

st.title("🎓 REX Assistant")
st.caption("Ask in English or Arabic · اسأل بالعربية أو الإنجليزية")

st.session_state.setdefault("messages", [])
st.session_state.setdefault("agent_state", {})

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("meta"):
            st.caption(msg["meta"])

prompt = st.chat_input("e.g. I'm a beginner and want to become a data analyst")
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
                meta += " · Sources: " + ", ".join(
                    f"{s.course_id} ({'synthetic' if s.synthetic else s.reference}, {s.last_updated or 'date unknown'})"
                    for s in response.sources
                )
        except Exception as exc:  # keep the UI alive; details go to the log
            logger.exception("Unhandled error while answering")
            text, meta = "Sorry, something went wrong. Please try again.", f"Error: {type(exc).__name__}"
        st.markdown(text)
        st.caption(meta)
    st.session_state.messages.append({"role": "assistant", "content": text, "meta": meta})
