"""Chat page. Students talk to REX Assistant only; staff reach all five agents through the
orchestrator. The answer layout (text, provenance note, sources, staff diagnostics) is built by
src.response_view.ResponseView so it is the same for live answers and for chat history."""
from __future__ import annotations

import streamlit as st

from src.agents.base import AgentRequest
from src.agents.orchestrator import ALL_AGENTS, PROFILES, PUBLIC_AGENTS
from src.response_view import ResponseView
from src.ui_context import current_role, get_context
from src.utils.logging_config import get_logger

logger = get_logger("app.chat")
ctx = get_context()
role = "staff" if current_role() == "staff" else "student"
allowed = ALL_AGENTS if role == "staff" else PUBLIC_AGENTS

STARTERS = {
    "student": [
        "I'm a final-year student and want to become a QA automation engineer",
        "What programs do you offer?",
        "How long is the Data Engineering track?",
        "عايز اشتغل مطور فلاتر",
    ],
    "staff": [
        "/help",
        "Any education tenders in Egypt?",
        "Create a curriculum for Software Testing",
        "Write a LinkedIn post for TRK-001",
    ],
}


def render(view: ResponseView) -> None:
    st.markdown(view.text)
    if not view.diagnostics:          # staff diagnostics already say how the answer was made
        st.caption(view.note)
    if view.sources:
        with st.expander(f"Sources ({len(view.sources)})"):
            for label in view.sources:
                st.caption(label)
    if view.diagnostics:
        st.caption(view.diagnostics)


def answer(prompt: str) -> ResponseView:
    try:
        response = ctx.orchestrator.handle(
            AgentRequest.from_text(prompt, st.session_state.agent_state), allowed_agents=allowed)
    except Exception as exc:  # keep the UI alive; details go to the log
        logger.exception("Unhandled error while answering")
        return ResponseView.error(exc, role)
    st.session_state.agent_state = response.state
    routed = response.data.get("routing", {})
    label = PROFILES[routed["agent"]].label_en if routed.get("agent") in PROFILES else None
    return ResponseView.from_response(response, role, agent_label=label)


if role == "staff":
    st.title("REX AI")
    st.caption("Ask in English or Arabic. Requests go to the right agent automatically; type /help for commands.")
else:
    st.title("REX Assistant")
    st.caption("Ask in English or Arabic about Andalusia Academy's training tracks.  \n"
               "اسأل بالعربي أو بالإنجليزي عن مسارات أكاديمية الأندلس.")

st.session_state.setdefault("messages", [])
st.session_state.setdefault("agent_state", {})

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render(ResponseView.from_dict(msg["view"]))
        else:
            st.markdown(msg["content"])

prompt = st.chat_input("e.g. Create a curriculum for Data Engineering" if role == "staff"
                       else "e.g. I'm a final-year student and want to work in data")
if not st.session_state.messages and not prompt:
    st.markdown("Try one of these:")
    for i, starter in enumerate(STARTERS[role]):
        if st.button(starter, key=f"starter_{i}"):
            prompt = starter

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            view = answer(prompt)
        render(view)
    st.session_state.messages.append({"role": "assistant", "view": view.to_dict()})
    if len(st.session_state.messages) == 2:
        st.rerun()  # removes the starter buttons once the conversation has begun
