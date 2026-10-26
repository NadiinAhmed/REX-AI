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


# ---------------------------------------------------------------- new PDF reaches the assistant
def _make_pdf(path, lines):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c, y = canvas.Canvas(str(path), pagesize=A4), 800
    for line in lines:
        c.drawString(40, y, line); y -= 20
    c.save()
    return path


@pytest.fixture
def ctx_with_new_track(ctx, tmp_path):
    """A PDF uploaded after start-up describing a track that is NOT in the course catalog."""
    pdf = _make_pdf(tmp_path / "cyber_track.pdf", [
        "Andalusia Academy - Cybersecurity Track",
        "Track code: TRK-006. Duration: 120 hours.",
        "The Cybersecurity track prepares students for SOC analyst and penetration tester roles.",
        "Tools: Wireshark, Kali Linux, Burp Suite, Splunk."])
    ctx.knowledge.ingestion.ingest(pdf, doc_type="course_catalog", approve=True)
    return ctx


@pytest.mark.parametrize("message", [
    "I want to learn cybersecurity, I'm a beginner",
    "What tools does the cybersecurity track use?",
    "عايز اتعلم الامن السيبراني",
])
def test_topic_only_in_a_new_pdf_is_answered_from_that_pdf(ctx_with_new_track, message):
    r = ask(ctx_with_new_track, message)
    assert r.data.get("fallback") == "documents_not_in_catalog"
    assert [s.filename for s in r.sources] == ["cyber_track.pdf"]       # weak portfolio pages filtered out
    assert all(s.source_type == "document" for s in r.sources)          # no unrelated catalog course
    assert "catalog" in r.text or "كتالوج" in r.text                    # says it's not in the catalog


def test_pending_pdf_is_not_used(ctx, tmp_path):
    pdf = _make_pdf(tmp_path / "cyber.pdf", ["Cybersecurity Track. Tools: Wireshark, Kali Linux."])
    ctx.knowledge.ingestion.ingest(pdf, approve=False)
    r = ask(ctx, "I want to learn cybersecurity")
    assert not any(s.filename == "cyber.pdf" for s in r.sources)


def test_attribute_word_is_not_treated_as_the_topic(ctx):
    # Before the fix, "tools" + the module "Security" matched Data Engineering (TRK-003).
    r = ask(ctx, "What tools does the cybersecurity track use?")
    assert not any(s.course_id == "TRK-003" for s in r.sources)


def test_topic_missing_everywhere_still_says_no_course(ctx):
    r = ask(ctx, "I want to learn cooking")
    assert r.sources == [] and "couldn't find" in r.text
