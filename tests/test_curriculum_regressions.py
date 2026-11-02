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


# ------------------------------------------------------------------ market-demand suggestions (Agent 2 -> Agent 3)
from types import SimpleNamespace

from src.curriculum.export import curriculum_pdf


class FakeRfp:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail = rows or [], fail

    def list(self, *, country=None, statuses=None):
        if self.fail:
            raise RuntimeError("database down")
        return [r for r in self.rows if not statuses or r.status in statuses]


def tender(id, text, courses=("TRK-001",), status="flagged_for_review", synthetic=True):
    return SimpleNamespace(id=id, title=text, description="", matched_course_ids=list(courses), country="EG",
                           status=status, synthetic=synthetic)


def _service_with(courses, rfp):
    db, repo = courses
    return CurriculumService(CurriculumRepository(db), repo, FrameworkLibrary.load(), TrackTopics.load(), rfp_repository=rfp)


def test_tender_skills_missing_from_the_curriculum_are_suggested_not_added(courses):
    plain = _service_with(courses, None).create(CurriculumRequest(course_id="TRK-001")).curriculum
    rfp = FakeRfp([tender("RFP-AAA111", "Training on test automation with Selenium and Playwright"),
                   tender("RFP-BBB222", "Selenium bootcamp", status="dismissed")])           # dismissed: ignored
    cur = _service_with(courses, rfp).create(CurriculumRequest(course_id="TRK-001")).curriculum

    by_text = {t.text.lower(): t for t in cur.market_suggestions}
    assert "selenium" in by_text and "playwright" not in by_text          # Playwright is already taught
    assert by_text["selenium"].origin == Origin.MARKET
    assert "RFP-AAA111" in by_text["selenium"].source and "SYNTHETIC" in by_text["selenium"].source
    assert "RFP-BBB222" not in by_text["selenium"].source
    # suggestions never change the curriculum itself
    assert [(m.title, [t.text for t in m.topics], m.hours) for m in cur.modules] == \
           [(m.title, [t.text for t in m.topics], m.hours) for m in plain.modules]
    assert any("named in matching tenders" in n for n in cur.notices)
    assert b"%PDF" in curriculum_pdf(cur, [])[:8]


def test_an_adopted_suggestion_disappears(courses):
    svc = _service_with(courses, FakeRfp([tender("RFP-AAA111", "Selenium training")]))
    stored = svc.create(CurriculumRequest(course_id="TRK-001"))
    assert [t.text.lower() for t in stored.curriculum.market_suggestions] == ["selenium"]
    after = svc.apply_edits(stored.id, "instructor", [{"op": "add_topic", "module": 1, "text": "Legacy suites in Selenium"}])
    assert after.curriculum.market_suggestions == []


def test_rfp_failure_or_no_match_means_no_suggestions(courses):
    for rfp in (FakeRfp(fail=True), FakeRfp([tender("RFP-AAA111", "Selenium training", courses=("TRK-005",))])):
        cur = _service_with(courses, rfp).create(CurriculumRequest(course_id="TRK-001")).curriculum
        assert cur.market_suggestions == [] and cur.validation_problems() == []


# ------------------------------------------------------------------ live-LLM check script (run offline with a fake)
def test_llm_check_script_detects_rule_breaking_and_good_answers(courses):
    import json as _json
    from evaluation.curriculum.check_llm_enrichment import check_course
    course = courses[1].get("TRK-001")

    class Fake:
        name = "fake"

        def __init__(self, reply):
            self.reply = reply

        def generate(self, system_prompt, messages):
            return self.reply

    good = _json.dumps({"objectives": ["Build reliable automated test suites."],
                        "learning_outcomes": [{"text": "Design risk-based test plans.", "modules": [1]}],
                        "module_exercises": {"1": ["Write a Playwright test for a login form."]}})
    result = check_course(course, Fake(good), TrackTopics.load())
    assert result["passed"] and result["llm_items"] >= 3 and result["fallback_notice"] is None

    claim = _json.dumps({"objectives": ["This course is accredited by ISTQB."]})          # filtered -> nothing usable
    result = check_course(course, Fake(claim), TrackTopics.load())
    assert result["checks"]["used_llm"] is False and result["checks"]["no_accreditation_claims"]

    result = check_course(course, Fake("not json"), TrackTopics.load())
    assert result["checks"]["used_llm"] is False and "deterministic" in result["fallback_notice"]


# ------------------------------------------------------------------ Arabic alignment
from src.curriculum.alignment import find_terms


@pytest.mark.parametrize("text", ["الاختبار الساكن", "والاختبار الساكن", "بالتحليل الثابت", "اختبارات التكامل", "التَّحليل الثابت"])
def test_arabic_keywords_match_with_common_prefixes_suffixes_and_diacritics(text):
    assert find_terms(["الاختبار الساكن", "التحليل الثابت", "اختبار التكامل"], [("m", text)])


def test_arabic_keywords_do_not_match_inside_other_words():
    assert find_terms(["الأمن"], [("m", "تحقيق الأمنيات")]) == {}      # "wishes", not "security"
    assert find_terms(["نشر النموذج"], [("m", "نشر المحتوى")]) == {}


def test_arabic_overlap_counts_once_and_every_framework_has_arabic_terms():
    assert len(find_terms(["التحليل", "التحليل الثابت"], [("m", "التحليل الثابت")])) == 1
    assert all(a.keywords_ar for f in FrameworkLibrary.load().all() for a in f.areas)


def test_arabic_curriculum_is_aligned():
    assert _status("مراجعة الكود والتحليل الثابت", "istqb-ctfl-4.0", "3") == AreaStatus.COVERED
