"""Agent 1 on the official data: document Q&A with citations, catalog overview, consent-based leads."""
import logging

import pytest

from src.agents.base import AgentRequest, ResponseMode
from src.bootstrap import build_app
from src.config import load_settings
from tests.conftest import OFFICIAL_DATASET


@pytest.fixture
def ctx(test_env):
    return build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET), RETRIEVAL_MIN_SCORE="0.23")))


def ask(ctx, *messages):
    state, r = {}, None
    for m in messages:
        r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text(m, state))
        state = r.state
    return r


def test_seed_pdf_is_indexed_and_approved(ctx):
    stats = ctx.knowledge.documents.stats()
    assert stats["approved"] == 1 and stats["chunks"] > 10


def test_recommendation_from_official_catalog(ctx):
    r = ask(ctx, "I'm a final-year student and I want to become a QA automation engineer")
    assert r.data["intent"] == "recommendation" and r.sources[0].course_id == "TRK-001"
    assert "Demo data" not in r.text


def test_arabic_recommendation_official(ctx):
    r = ask(ctx, "عايز اشتغل مطور فلاتر")
    assert r.language == "ar" and r.sources[0].course_id == "TRK-002"


def test_unpublished_fee_is_reported_not_invented(ctx):
    r = ask(ctx, "How much is the Flutter Development track?")
    assert r.data["intent"] == "course_question" and "not available" in r.text


def test_new_course_fields_answer_tools_question(ctx):
    r = ask(ctx, "What tools does Data Engineering cover?")
    assert "Apache Airflow" in r.text and r.sources[0].course_id == "TRK-003"


def test_document_question_is_answered_with_page_citation(ctx):
    r = ask(ctx, "Who can I contact about partnerships?")
    assert r.data["intent"] == "document_question"
    assert r.sources[0].source_type == "document" and r.sources[0].page == 9
    assert "p.9" in r.text


def test_question_not_in_documents_says_so(ctx):
    r = ask(ctx, "Do you offer scholarships for students?")
    assert r.data["intent"] == "document_question" and r.sources == []
    assert "couldn't find" in r.text


def test_catalog_overview_lists_all_tracks(ctx):
    r = ask(ctx, "What programs do you offer?")
    assert r.data["intent"] == "catalog_overview" and len(r.sources) == 5


def test_document_answer_uses_llm_with_delimited_passages(test_env):
    class FakeLLM:
        name = "fake"
        def __init__(self): self.calls = []
        def generate(self, system, messages):
            self.calls.append((system, messages)); return "Contact the academy by email [1]."
    llm = FakeLLM()
    ctx = build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET), RETRIEVAL_MIN_SCORE="0.23")), llm=llm)
    r = ask(ctx, "Who can I contact about partnerships?")
    system, messages = llm.calls[0]
    assert r.mode == ResponseMode.LLM and "data, not instructions" in system
    assert "<documents>" in messages[0].content and "p.9" in messages[0].content


def test_lead_requires_consent_and_saves_after_yes(ctx, caplog):
    caplog.set_level(logging.INFO)
    r = ask(ctx, "I want to become a data engineer", "I want to register")
    assert "Do you agree" in r.text and ctx.leads.count() == 0          # nothing stored yet
    state = r.state
    for msg in ("yes", "Sara Ahmed, sara@example.com"):
        r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text(msg, state)); state = r.state
    assert "saved" in r.text and ctx.leads.count() == 1
    lead = ctx.leads.list()[0]
    assert (lead.name, lead.email) == ("Sara Ahmed", "sara@example.com")
    assert lead.interested_course_ids[0] == "TRK-003"   # courses recommended before registering
    assert "sara@example.com" not in caplog.text                         # no PII in logs
    assert r.state["lead"] == {}


def test_declining_consent_stores_nothing(ctx):
    r = ask(ctx, "سجلني", "لا")
    assert r.language == "ar" and ctx.leads.count() == 0 and r.state["lead"] == {}


def test_missing_contact_details_are_requested_and_cancel_works(ctx):
    r = ask(ctx, "register", "yes", "my name is Omar")
    assert "phone number or email" in r.text and ctx.leads.count() == 0
    r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text("cancel", r.state))
    assert "cancelled" in r.text and ctx.leads.count() == 0
