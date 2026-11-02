"""How one agent answer is shown in the chat. Pure Python (no Streamlit), so it is unit-tested.

Two audiences see the same AgentResponse differently:
  - students get the answer, a plain-language note on where it came from, and readable sources;
  - staff additionally get diagnostics: which agent answered, why it was routed there, whether an
    LLM wrote the text, and retrieval scores.
The view is stored in st.session_state as a dict, so chat history re-renders exactly as it first did.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.agents.base import AgentResponse, ResponseMode, SourceReference

Role = Literal["student", "staff"]

# Student-facing note on provenance. Honest about the LLM, without engineering jargon.
_NOTE = {
    ResponseMode.LLM: {"en": "Written by AI from academy records", "ar": "كتبه الذكاء الاصطناعي من بيانات الأكاديمية"},
    ResponseMode.DETERMINISTIC: {"en": "Answer from academy records", "ar": "إجابة من بيانات الأكاديمية"},
    ResponseMode.ERROR: {"en": "This answer could not be completed", "ar": "تعذر إكمال هذه الإجابة"},
}
# Staff-facing mode label (kept identical to the previous UI wording).
_MODE = {
    ResponseMode.LLM: "LLM-generated (grounded in academy data)",
    ResponseMode.DETERMINISTIC: "Deterministic rule-based answer (no LLM)",
    ResponseMode.ERROR: "Error",
}


@dataclass
class ResponseView:
    text: str
    note: str
    sources: list[str] = field(default_factory=list)
    diagnostics: str | None = None      # staff only
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResponseView":
        return cls(**data)

    @classmethod
    def error(cls, exc: Exception, role: Role, lang: str = "en") -> "ResponseView":
        text = ("حدث خطأ أثناء الإجابة. حاول مرة أخرى." if lang == "ar"
                else "Something went wrong while answering. Please try again.")
        return cls(text=text, note=_NOTE[ResponseMode.ERROR][_lang(lang)], is_error=True,
                   diagnostics=f"Error: {type(exc).__name__}" if role == "staff" else None)

    @classmethod
    def from_response(cls, response: AgentResponse, role: Role,
                      agent_label: str | None = None) -> "ResponseView":
        lang = _lang(response.language)
        sources = list(dict.fromkeys(source_label(s, role, lang) for s in response.sources))
        diagnostics = None
        if role == "staff":
            parts = []
            routed = response.data.get("routing", {})
            if agent_label and routed.get("action") == "dispatch":
                parts.append(f"Agent: {agent_label} (routed by {routed.get('reason', 'rule')})")
            parts.append(_MODE[response.mode])
            diagnostics = "  |  ".join(parts)
        return cls(text=tidy_markdown(response.text), note=_NOTE[response.mode][lang], sources=sources,
                   diagnostics=diagnostics, is_error=response.mode == ResponseMode.ERROR)


def source_label(source: SourceReference, role: Role, lang: str = "en") -> str:
    """Students see a readable reference; staff see the full label plus the retrieval score."""
    if role == "staff":
        label = source.label()
        return f"{label} (score {source.score:.2f})" if source.score is not None else label
    ar = lang == "ar"
    if source.source_type == "document":
        return f"{source.filename}، صفحة {source.page}" if ar else f"{source.filename}, page {source.page}"
    if source.source_type == "course":
        demo = (" (بيانات تجريبية)" if ar else " (demo data)") if source.synthetic else ""
        return (f"سجل الكورس {source.course_id}" if ar else f"Course record {source.course_id}") + demo
    return source.label()


_LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def tidy_markdown(text: str) -> str:
    """Put a blank line between a list and the sentence after it.

    In Markdown a plain line directly under "- item" is glued onto that item, so
    "- Data Science ...\nTell me your goal" rendered as one bullet. Agents build answers line by
    line, so this is fixed once here for every agent. Code blocks are left untouched."""
    out: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif (not in_code and out and _LIST_ITEM.match(out[-1]) and line.strip()
              and not _LIST_ITEM.match(line) and not line.startswith((" ", "\t"))):
            out.append("")
        out.append(line)
    return "\n".join(out)


def _lang(lang: str | None) -> str:
    return "ar" if lang == "ar" else "en"
