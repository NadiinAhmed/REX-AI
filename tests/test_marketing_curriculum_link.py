"""Agent 3 -> Agent 5: marketing copy may use a curriculum only after a human approved it.

Brief workflow: New Course -> Curriculum Approved -> Marketing Agent Generates Campaign -> Admin Reviews.
"""
import pytest

from src.agents.base import AgentRequest
from src.config import PROJECT_ROOT, load_settings
from src.curriculum.frameworks import FrameworkLibrary
from src.curriculum.models import CurriculumRequest
from src.curriculum.service import CurriculumService
from src.curriculum.topics import TrackTopics
from src.db.curriculum_repository import CurriculumRepository
from src.db.database import Database
from src.db.repositories import CourseRepository
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from src.marketing.facts import CourseFactsProvider, CurriculumServiceSource

OFFICIAL = PROJECT_ROOT / "data" / "courses_official.json"
COURSE = "TRK-001"


@pytest.fixture
def curricula():
    db = Database("sqlite://")
    db.migrate()
    courses = CourseRepository(db)
    courses.replace_catalog(load_catalog(OFFICIAL))
    return CurriculumService(CurriculumRepository(db), courses, FrameworkLibrary.load(), TrackTopics.load())


def facts_with(service, course_id=COURSE):
    retriever = KeywordRetriever(load_catalog(OFFICIAL).courses)
    return CourseFactsProvider(retriever, curriculum=CurriculumServiceSource(service)).facts_for(course_id)


def curriculum_facts(facts):
    return [f for f in facts.facts if "approved curriculum" in f.source]


def approve(service, course_id=COURSE):
    stored = service.create(CurriculumRequest(course_id=course_id), use_llm=False)
    service.submit(stored.id, "author")
    return service.approve(stored.id, "Dr. Reviewer")


def test_no_curriculum_means_course_record_only(curricula):
    assert curriculum_facts(facts_with(curricula)) == []


def test_unapproved_curricula_are_invisible_to_marketing(curricula):
    draft = curricula.create(CurriculumRequest(course_id=COURSE), use_llm=False)
    assert curriculum_facts(facts_with(curricula)) == []                   # draft
    curricula.submit(draft.id, "author")
    assert curriculum_facts(facts_with(curricula)) == []                   # in review
    curricula.request_changes(draft.id, "Dr. Reviewer", "Add more labs")
    assert curriculum_facts(facts_with(curricula)) == []                   # changes requested


def test_approved_curriculum_becomes_traceable_facts(curricula):
    approved = approve(curricula)
    cur = approved.curriculum
    facts = facts_with(curricula)
    by_field = {f.field: f for f in curriculum_facts(facts)}
    assert set(by_field) == {"learning_outcomes", "modules"}
    assert cur.learning_outcomes[0].text in by_field["learning_outcomes"].value
    assert cur.modules[0].title in by_field["modules"].value
    source = by_field["modules"].source
    assert approved.id in source and f"rev {approved.revision}" in source and "Dr. Reviewer" in source


def test_another_courses_approval_does_not_leak(curricula):
    approve(curricula, "TRK-002")
    assert curriculum_facts(facts_with(curricula, COURSE)) == []


def test_curriculum_failure_falls_back_to_the_course_record():
    class Broken:
        def approved_for_course(self, course_id):
            raise RuntimeError("table missing")

    facts = facts_with(Broken())
    assert curriculum_facts(facts) == []
    assert any("could not be loaded" in n and "curriculum" in n for n in facts.notes)


def test_end_to_end_app_wiring_uses_the_approved_curriculum(test_env):
    """Real build_app(): approve in Agent 3, then ask Agent 5 for a campaign."""
    from src.bootstrap import build_app

    ctx = build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL))), use_configured_llm=False,
                    enable_rag=False)
    assert ctx.curriculum_service is not None
    approved = approve(ctx.curriculum_service)

    ctx.registry.dispatch("marketing", AgentRequest.from_text("Software Testing on LinkedIn for registrations"))
    campaigns = ctx.marketing.list_campaigns()
    assert len(campaigns) == 1
    sources = [f["source"] for f in campaigns[0].facts_snapshot["facts"]]
    assert any(approved.id in s for s in sources), sources
