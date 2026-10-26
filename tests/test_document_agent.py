"""Agent 4 — chat interface, cross-agent workflow, wiring, Streamlit page and migration."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.agents.base import AgentRequest, AgentStatus  # noqa: E402
from src.bootstrap import build_app  # noqa: E402
from src.config import PROJECT_ROOT, load_settings  # noqa: E402
from src.documents.models import DocStatus  # noqa: E402

OFFICIAL = str(PROJECT_ROOT / "data" / "courses_official.json")


@pytest.fixture
def ctx(test_env):
    return build_app(load_settings(test_env(REX_COURSES_PATH=OFFICIAL)))


def ask(ctx, text, agent="document_generation"):
    return ctx.registry.dispatch(agent, AgentRequest.from_text(text))


def test_agent_is_registered_and_marketing_is_still_planned(ctx):
    status = {a.name: a.status for a in ctx.registry.list_agents()}
    assert status["document_generation"] == AgentStatus.IMPLEMENTED and status["curriculum"] == AgentStatus.IMPLEMENTED
    assert status["marketing"] == AgentStatus.IMPLEMENTED and ctx.document_service is not None and not ctx.warnings

def test_generate_show_and_list_through_chat(ctx):
    r = ask(ctx, "generate a participant guide for TRK-001")
    doc_id = r.data["document_id"]
    assert "Draft created" in r.text and "not approved" in r.text and r.data["status"] == "draft"
    assert {s.source_type for s in r.sources} == {"course"}
    assert doc_id in ask(ctx, "list").text
    assert "Participant guide" in ask(ctx, doc_id).text and "Download PDF" in ask(ctx, f"pdf {doc_id}").text


@pytest.mark.parametrize("message,doc_type", [
    ("make me a question bank for TRK-002", "question_bank"), ("write the capstone brief for Data Engineering", "capstone_brief"),
    ("rubrics for TRK-001", "assessment_rubrics"), ("slides for TRK-004", "presentation_outline"),
    ("instructor guide for TRK-005", "instructor_guide"), ("course proposal for TRK-001", "course_proposal"),
    ("learning outcomes plan for TRK-001", "learning_outcomes_plan"), ("course description for TRK-003", "course_description"),
    ("curriculum pdf for TRK-001", "curriculum_pdf")])
def test_document_type_is_recognised_from_natural_language(ctx, message, doc_type):
    r = ask(ctx, message)
    assert ctx.document_service.get(r.data["document_id"]).doc_type.value == doc_type


def test_arabic_request_creates_an_arabic_document(ctx):
    r = ask(ctx, "اعمل دليل المدرب لـ TRK-002 بالعربية")
    assert r.language == "ar" and "مسودة" in r.text
    assert ctx.document_service.get(r.data["document_id"]).language == "ar"
    assert ask(ctx, "اعمل دليل المشارك لـ TRK-001 بالانجليزية").language == "ar"       # reply in the user's language
    english = ctx.document_service.get(ask(ctx, "اعمل دليل المشارك لـ TRK-001 بالانجليزية").data["document_id"])
    assert english.language == "en"
    assert "المستندات" in ask(ctx, "أنواع المستندات").text


def test_missing_inputs_ask_the_discovery_questions(ctx):
    assert "which course" in ask(ctx, "question bank").text and "Courses: TRK-001" in ask(ctx, "question bank").text
    assert "which document" in ask(ctx, "generate something for TRK-001").text
    assert "Documents I can prepare" in ask(ctx, "types").text
    assert "No documents yet" in ask(ctx, "list").text
    assert "No document with ID DOC-FFFFFF" in ask(ctx, "DOC-FFFFFF").text
    assert "I prepare course documents" in ask(ctx, "hello").text


def test_chat_cannot_approve_or_email(ctx):
    doc_id = ask(ctx, "generate a capstone brief for TRK-001").data["document_id"]
    ask(ctx, f"approve {doc_id}")
    assert ctx.document_service.get(doc_id).status == DocStatus.DRAFT


def test_curriculum_to_documents_workflow_between_two_agents(ctx):
    """Agent 3 creates and approves a curriculum; Agent 4 then builds from it automatically."""
    cid = ask(ctx, "create a curriculum for TRK-002 with 90 hours", agent="curriculum").data["curriculum_id"]
    before = ask(ctx, "generate a participant guide for TRK-002")
    assert ctx.document_service.get(before.data["document_id"]).curriculum_id is None           # not approved yet -> catalog
    ctx.curriculum_service.submit(cid, "author")
    ctx.curriculum_service.approve(cid, "Dr. Hana")
    after = ask(ctx, "generate a participant guide for TRK-002")
    doc = ctx.document_service.get(after.data["document_id"])
    assert doc.curriculum_id == cid and "Approved curriculum" in doc.basis
    assert {s.source_type for s in after.sources} == {"course", "curriculum"}
    rows = [r for sec in doc.content.sections for blk in sec.blocks for r in blk.rows]
    assert ["Total hours", "90"] in rows                 # the approved 90-hour curriculum, not the catalog's 100 hours


def test_agent_failure_does_not_break_the_app(test_env, monkeypatch):
    import src.documents.service as svc
    monkeypatch.setattr(svc, "DocumentService", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    ctx = build_app(load_settings(test_env(REX_COURSES_PATH=OFFICIAL)))
    assert any("Document Generation agent unavailable" in w for w in ctx.warnings)
    assert {a.name for a in ctx.registry.list_agents() if a.status == AgentStatus.IMPLEMENTED} >= {"rex_assistant", "curriculum"}


def test_migrations_create_the_documents_table_and_keep_a_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect
    from src.db.database import Database
    db = Database("sqlite://")
    db.migrate()
    assert {"rfp_opportunities", "curricula", "generated_documents"} <= set(inspect(db.engine).get_table_names())
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0004_documents"]


# ------------------------------------------------------------------ Streamlit page
@pytest.fixture
def page(monkeypatch, test_env):
    for key, value in test_env(REX_COURSES_PATH=OFFICIAL).items():
        monkeypatch.setenv(key, value)
    from src.ui_context import get_context
    get_context.clear()
    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "6_Documents.py"), default_timeout=90)
    at.run()
    yield at
    get_context.clear()


def _click(at, label):
    next(b for b in at.button if b.label == label).click().run()


def test_page_generates_edits_and_approves(page):
    assert not page.exception
    _click(page, "Generate document")
    assert not page.exception and any("Created DOC-" in s.value for s in page.success)
    assert any("Draft: not approved" in w.value for w in page.warning)
    next(t for t in page.text_input if t.label == "Reviewer name").set_value("Dr. Hana").run()
    _click(page, "✅ Approve")
    assert not page.exception and any("Approved by Dr. Hana" in s.value for s in page.success)


def test_page_shows_a_message_when_there_is_nothing_to_approve_with(page):
    _click(page, "Generate document")
    _click(page, "✅ Approve")                                   # no reviewer name
    assert not page.exception and any("reviewer's name" in e.value for e in page.error)
