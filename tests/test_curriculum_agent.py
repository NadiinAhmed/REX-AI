"""Agent 3 — chat interface, app wiring, Streamlit page and migration."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.agents.base import AgentRequest, AgentStatus, ResponseMode  # noqa: E402
from src.bootstrap import build_app  # noqa: E402
from src.config import PROJECT_ROOT, load_settings  # noqa: E402

OFFICIAL = str(PROJECT_ROOT / "data" / "courses_official.json")


@pytest.fixture
def ctx(test_env):
    return build_app(load_settings(test_env(REX_COURSES_PATH=OFFICIAL)))


def ask(ctx, text):
    return ctx.registry.dispatch("curriculum", AgentRequest.from_text(text))


def test_agent_is_registered_as_implemented_and_the_rest_still_work(ctx):
    status = {a.name: a.status for a in ctx.registry.list_agents()}
    assert status["curriculum"] == AgentStatus.IMPLEMENTED and status["rex_assistant"] == AgentStatus.IMPLEMENTED
    assert ctx.curriculum_service is not None and not ctx.warnings


def test_create_show_align_submit_through_chat(ctx):
    r = ask(ctx, "create a curriculum for TRK-001 with 80 hours")
    cid = r.data["curriculum_id"]
    assert r.mode == ResponseMode.DETERMINISTIC and "not approved" in r.text and "Total hours: 80" in r.text
    assert {s.source_type for s in r.sources} == {"course", "framework", "curriculum"}
    assert cid in ask(ctx, "list").text
    assert "Software Testing" in ask(ctx, cid).text
    align = ask(ctx, f"align {cid}")
    assert "proposal, not an accreditation" in align.text and "Static Testing" in align.text
    assert "sent for review" in ask(ctx, f"submit {cid}").text
    assert ctx.curriculum_service.get(cid).status.value == "in_review"


def test_approval_is_not_available_in_chat(ctx):
    cid = ask(ctx, "create a curriculum for TRK-002").data["curriculum_id"]
    ask(ctx, f"approve {cid}")
    assert ctx.curriculum_service.get(cid).status.value == "draft"          # chat can never approve


def test_arabic_commands_and_replies(ctx):
    r = ask(ctx, "أنشئ منهج TRK-001")
    assert r.language == "ar" and "مسودة" in r.text
    assert "المعايير" in ask(ctx, "ما هي المعايير").text or "أطر" in ask(ctx, "ما هي المعايير").text
    assert ask(ctx, "قائمة المناهج").language == "ar"


def test_missing_or_unknown_inputs_get_a_helpful_answer_not_a_crash(ctx):
    assert "Which course" in ask(ctx, "create a curriculum").text
    assert "No curriculum with ID CUR-FFFFFF" in ask(ctx, "CUR-FFFFFF").text
    assert "not in the approved catalog" in ask(ctx, "create a curriculum for ZZZ-999").text or "Which course" in ask(ctx, "create a curriculum for ZZZ-999").text
    assert "No curricula yet" in ask(ctx, "list").text
    assert "Commands" in ask(ctx, "hello there").text
    assert "crisp-dm" in ask(ctx, "frameworks").text


def test_course_can_be_named_by_title_and_level_is_read(ctx):
    r = ask(ctx, "design a curriculum for Flutter Development for beginner students")
    stored = ctx.curriculum_service.get(r.data["curriculum_id"])
    assert stored.curriculum.course_id == "TRK-002" and stored.curriculum.skill_level == "beginner"


def test_agent_never_runs_when_no_service_is_wired(test_env, monkeypatch):
    import src.curriculum.service as svc
    monkeypatch.setattr(svc, "CurriculumService", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    ctx = build_app(load_settings(test_env(REX_COURSES_PATH=OFFICIAL)))
    assert any("Curriculum agent unavailable" in w for w in ctx.warnings)
    assert "rex_assistant" in {a.name for a in ctx.registry.list_agents() if a.status == AgentStatus.IMPLEMENTED}


def test_two_migration_heads_are_applied_together():
    """Teammates add migrations on their own branches: start-up must apply every head."""
    from sqlalchemy import inspect
    from src.db.database import Database
    db = Database("sqlite://")
    db.migrate()
    assert {"rfp_opportunities", "curricula"} <= set(inspect(db.engine).get_table_names())


# ------------------------------------------------------------------ Streamlit page
@pytest.fixture
def page(monkeypatch, test_env):
    for key, value in test_env(REX_COURSES_PATH=OFFICIAL).items():
        monkeypatch.setenv(key, value)
    from src.ui_context import get_context
    get_context.clear()
    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "5_Curriculum.py"), default_timeout=90)
    at.run()
    yield at
    get_context.clear()


def _click(at, label):
    next(b for b in at.button if b.label == label).click().run()


def test_page_generates_reviews_and_approves(page):
    assert not page.exception
    _click(page, "Generate curriculum")
    assert not page.exception and any("Created CUR-" in s.value for s in page.success)
    assert any("Not approved yet" in w.value for w in page.warning)
    assert any(m.label == "Total hours" and m.value == "100" for m in page.metric)
    _click(page, "📨 Submit for review")
    next(t for t in page.text_input if t.label == "Reviewer name").set_value("Dr. Hana").run()
    _click(page, "✅ Approve")
    assert not page.exception and any("Approved by Dr. Hana" in s.value for s in page.success)


def test_page_shows_errors_instead_of_crashing(page):
    next(r for r in page.radio if r.label == "Base").set_value("New course").run()
    _click(page, "Generate curriculum")
    assert not page.exception and page.error                       # empty title is rejected with a message
