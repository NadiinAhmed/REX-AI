"""Agent 3 regressions found in the Agent 3 review (2026-11-01). Each test failed before its fix."""
import pytest

from src.agents.base import AgentRequest
from src.agents.curriculum import CurriculumAgent
from src.config import PROJECT_ROOT
from src.curriculum.alignment import check_framework
from src.curriculum.frameworks import FrameworkLibrary
from src.curriculum.generator import CurriculumError
from src.curriculum.models import AreaStatus, Curriculum, CurriculumModule, CurriculumRequest, Origin, TracedText
from src.curriculum.service import CurriculumService
from src.curriculum.topics import TrackTopics
from src.db.curriculum_repository import CurriculumRepository
from src.db.database import Database
from src.db.repositories import CourseRepository
from src.knowledge_base.loader import load_catalog

OFFICIAL = PROJECT_ROOT / "data" / "courses_official.json"


@pytest.fixture
def courses():
    db = Database("sqlite://")
    db.migrate()
    repo = CourseRepository(db)
    repo.replace_catalog(load_catalog(OFFICIAL))
    return db, repo


@pytest.fixture
def service(courses):
    db, repo = courses
    return CurriculumService(CurriculumRepository(db), repo, FrameworkLibrary.load(), TrackTopics.load())


def _links(cur: Curriculum) -> dict[str, list[str]]:
    """outcome text -> titles of the modules it is linked to (numbers are only a means to an end)."""
    titles = {m.number: m.title for m in cur.modules}
    return {o.text: [titles[n] for n in o.module_numbers] for o in cur.learning_outcomes}


# ------------------------------------------------------------------ Bug 1: remove_module
def test_removing_a_module_keeps_outcome_links_on_the_same_modules(service):
    stored = service.create(CurriculumRequest(course_id="TRK-001"))
    removed = stored.curriculum.modules[1].title
    before = _links(stored.curriculum)
    after = service.apply_edits(stored.id, "tester", [{"op": "remove_module", "module": 2}]).curriculum

    numbers = {m.number for m in after.modules}
    assert all(set(o.module_numbers) <= numbers for o in after.learning_outcomes), "link to a module that no longer exists"
    for text, titles in _links(after).items():
        assert titles == [t for t in before[text] if t != removed], text
    # the template outcome written for the removed module is gone; ids stay LO1..LOn
    assert not any(removed in o.text and o.origin == Origin.TEMPLATE for o in after.learning_outcomes)
    assert [o.id for o in after.learning_outcomes] == [f"LO{i}" for i in range(1, len(after.learning_outcomes) + 1)]
    assert after.validation_problems() == []


def test_catalog_or_reviewer_outcomes_survive_module_removal(service):
    stored = service.create(CurriculumRequest(course_id="TRK-001"))
    stored = service.apply_edits(stored.id, "tester", [{"op": "add_outcome", "text": "Explain the QA strategy", "modules": [2]}])
    after = service.apply_edits(stored.id, "tester", [{"op": "remove_module", "module": 2}]).curriculum
    kept = next(o for o in after.learning_outcomes if o.text == "Explain the QA strategy")
    assert kept.origin == Origin.USER and kept.module_numbers == []   # kept, unlinked: the reviewer decides


def test_add_outcome_rejects_unknown_module_numbers(service):
    stored = service.create(CurriculumRequest(course_id="TRK-001"))
    with pytest.raises(CurriculumError):
        service.apply_edits(stored.id, "tester", [{"op": "add_outcome", "text": "Do something", "modules": [99]}])


# ------------------------------------------------------------------ Bug 2: overlapping keywords
def _status(text: str, framework_id: str, area_id: str) -> AreaStatus:
    cur = Curriculum(title="Probe course", modules=[CurriculumModule(number=1, title="Module", hours=1,
                                                                  topics=[TracedText(text=text)])])
    report = check_framework(cur, FrameworkLibrary.load().get(framework_id))
    return next(a for a in report.areas if a.area_id == area_id).status


@pytest.mark.parametrize("text,framework_id,area_id", [
    ("Peer code review session", "istqb-ctfl-4.0", "3"),       # 'code review' contains 'review'
    ("Discussing trade-offs", "swebok-3", "12"),                # 'trade-offs' also matches 'trade-off'
    ("Cost-aware cloud design", "swebok-3", "12"),              # 'cost-aware' contains 'cost'
    ("Golden testing for Flutter widgets", "swebok-3", "4"),    # 'golden testing' contains 'testing'
    ("Model evaluation report", "crisp-dm", "5"),               # 'model evaluation' contains 'evaluation'
])
def test_one_phrase_never_counts_as_two_keywords(text, framework_id, area_id):
    assert _status(text, framework_id, area_id) == AreaStatus.PARTIAL


def test_separate_mentions_still_count_as_two():
    assert _status("Code review and design review", "istqb-ctfl-4.0", "3") == AreaStatus.COVERED
    assert _status("Static analysis and code review", "istqb-ctfl-4.0", "3") == AreaStatus.COVERED


# ------------------------------------------------------------------ minor
def test_summary_keeps_outcome_count_without_a_capstone(service, courses):
    stored = service.create(CurriculumRequest(course_id="TRK-001"))
    stored.curriculum.capstone = None
    text = CurriculumAgent(service, courses[1])._summary(stored, "en")
    assert f"Learning outcomes: {len(stored.curriculum.learning_outcomes)}" in text


def test_error_replies_carry_an_intent(service, courses):
    reply = CurriculumAgent(service, courses[1]).handle(AgentRequest(message="submit CUR-000000"))
    assert reply.data["intent"] == "error" and "CUR-000000" in reply.text
