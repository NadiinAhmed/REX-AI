"""Role 3 — end-to-end wiring: config -> data -> retriever -> assistant -> registry."""
import pytest

from src.agents.base import AgentRequest, AgentStatus, ResponseMode
from src.bootstrap import build_app
from src.config import load_settings
from src.knowledge_base.loader import KnowledgeBaseError


@pytest.fixture
def ctx(test_env):
    # Pinned to the synthetic demo catalog and a temporary database (see conftest.test_env).
    return build_app(load_settings(test_env()), enable_rag=False)


def test_app_builds_in_deterministic_mode(ctx):
    assert ctx.course_count >= 5 and ctx.dataset.is_synthetic
    assert "deterministic" in ctx.llm_status
    implemented = [a.name for a in ctx.registry.list_agents() if a.status == AgentStatus.IMPLEMENTED]
    assert implemented == ["rex_assistant", "rfp_research"]          # Sprint 2: Agent 2 added


def test_full_bilingual_conversation_through_registry(ctx):
    state = {}
    turns = [
        ("Hello", "follow_up"),
        ("I'm a beginner and want to become a data analyst", "recommendation"),
        ("what are the prerequisites?", "course_question"),
        ("عايز اتعلم أمن سيبراني", "recommendation"),
    ]
    for message, intent in turns:
        r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text(message, state))
        assert r.data["intent"] == intent, message
        assert r.mode == ResponseMode.DETERMINISTIC
        state = r.state
    assert r.language == "ar" and r.sources[0].course_id == "SEC-210"


def test_bad_data_path_fails_with_clear_error(test_env):
    with pytest.raises(KnowledgeBaseError, match="REX_COURSES_PATH"):
        build_app(load_settings(test_env(REX_COURSES_PATH="missing/courses.json")), enable_rag=False)
