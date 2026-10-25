"""Agent 3 — curriculum generation, hours, traceability, alignment, review workflow, repository."""
import pytest

from src.config import PROJECT_ROOT
from src.curriculum.alignment import check_framework
from src.curriculum.frameworks import FrameworkError, FrameworkLibrary
from src.curriculum.generator import CurriculumError, DraftInputs, allocate_hours, build_draft, enrich_with_llm
from src.curriculum.models import AreaStatus, Curriculum, CurriculumRequest, CurriculumStatus, Origin
from src.curriculum.service import CurriculumService, CurriculumWorkflowError
from src.curriculum.topics import TrackTopics
from src.db.curriculum_repository import CurriculumRepository
from src.db.database import Database
from src.db.repositories import CourseRepository
from src.knowledge_base.loader import load_catalog
from src.services.llm_service import LLMError

OFFICIAL = PROJECT_ROOT / "data" / "courses_official.json"


@pytest.fixture(scope="module")
def official():
    return load_catalog(OFFICIAL)


@pytest.fixture
def service(official):
    db = Database("sqlite://")
    db.migrate()
    courses = CourseRepository(db)
    courses.replace_catalog(official)
    return CurriculumService(CurriculumRepository(db), courses, FrameworkLibrary.load(), TrackTopics.load())


class FakeLLM:
    name = "fake"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ------------------------------------------------------------------ hours
@pytest.mark.parametrize("total,weights", [(100, [4, 4, 4, 4, 2]), (120, [1, 1, 1]), (37.5, [3, 5, 2, 9]), (10, [1]), (140, [1, 3, 4, 4, 1])])
def test_allocated_hours_always_add_up_to_the_total(total, weights):
    hours, capstone = allocate_hours(total, weights)
    assert round(sum(hours) + capstone, 2) == total and all(h >= 0 for h in hours)


def test_every_official_course_gets_a_consistent_curriculum(service, official):
    for course in official.courses:
        stored = service.create(CurriculumRequest(course_id=course.course_id))
        cur = stored.curriculum
        assert cur.validation_problems() == [], course.course_id
        assert cur.total_hours == (course.duration_hours or cur.total_hours)
        assert cur.modules and cur.learning_outcomes and cur.capstone


def test_unpublished_duration_is_a_flagged_proposal_not_a_fact(service):
    cur = service.create(CurriculumRequest(course_id="TRK-003")).curriculum       # the portfolio publishes no hours for it
    assert cur.hours_origin == Origin.TEMPLATE
    assert any("proposal" in n and "hours" in n for n in cur.notices)


def test_requested_hours_override_the_catalog(service):
    cur = service.create(CurriculumRequest(course_id="TRK-001", duration_hours=80)).curriculum
    assert cur.total_hours == 80 and cur.hours_origin == Origin.USER and cur.allocated_hours == 80


# ------------------------------------------------------------------ traceability / honesty
def test_every_item_is_traceable_and_nothing_is_invented(service):
    cur = service.create(CurriculumRequest(course_id="TRK-002")).curriculum
    topics = [t for m in cur.modules for t in m.topics]
    assert topics and all(t.origin == Origin.DOCUMENT and "p.4" in (t.source or "") for t in topics)
    assert cur.prerequisites == []                                     # not published: left empty, with a notice
    assert any("Prerequisites are not published" in n for n in cur.notices)
    assert all(o.origin in (Origin.TEMPLATE, Origin.CATALOG, Origin.USER) for o in cur.learning_outcomes)
    assert any(n for n in cur.notices if "template" in n)             # derived outcomes are declared as such


def test_no_text_ever_claims_accreditation(service):
    """The brief: never claim a course is officially accredited without evidence. Only NEGATED mentions may appear."""
    import re
    from src.curriculum.export import curriculum_document
    stored = service.create(CurriculumRequest(course_id="TRK-001"))
    doc = curriculum_document(stored.curriculum, stored.alignment)
    blob = " ".join(b.text + " ".join(b.items) + " ".join(" ".join(r) for r in b.rows)
                    for s in doc.sections for b in s.blocks) + " " + doc.disclaimer
    negations = ("does not mean the course is accredited", "does not state or imply that the course is accredited",
                 "no accreditation is claimed", "never states that a course is accredited")
    cleaned = blob.lower()
    for phrase in negations:
        cleaned = cleaned.replace(phrase, "")
    assert not re.search(r"\b(is|are|was|officially|fully)\s+(officially\s+)?accredited\b", cleaned)
    assert "no accreditation is claimed" in blob.lower()


def test_catalog_capstone_module_becomes_the_capstone_section(service):
    cur = service.create(CurriculumRequest(course_id="TRK-003")).curriculum
    assert not any(m.title.startswith("Capstone") for m in cur.modules)
    assert any("represented by the capstone" in n for n in cur.notices)


def test_unknown_course_and_missing_subject_are_rejected(service):
    with pytest.raises(CurriculumError, match="not in the approved catalog"):
        service.create(CurriculumRequest(course_id="ZZZ-999"))
    with pytest.raises(ValueError):
        CurriculumRequest()


def test_new_course_from_topics_and_from_framework(service):
    cur = service.create(CurriculumRequest(title="Intro to Cybersecurity", topics=["Network security", "Cryptography basics"],
                                           duration_hours=30, target_audience=["IT graduates"])).curriculum
    assert [m.title for m in cur.modules] == ["Network security", "Cryptography basics"]
    assert cur.course_id is None and cur.total_hours == 30 and cur.validation_problems() == []
    fw = service.create(CurriculumRequest(title="Software Testing Foundations", framework_ids=["istqb-ctfl-4.0"], duration_hours=40)).curriculum
    assert len(fw.modules) == 6 and fw.modules[0].origin == Origin.FRAMEWORK
    with pytest.raises(CurriculumError, match="at least one topic"):
        service.create(CurriculumRequest(title="Empty idea"))


def test_requested_topics_attach_to_the_best_matching_module(service):
    cur = service.create(CurriculumRequest(course_id="TRK-001", topics=["Playwright visual regression", "Quantum gardening"])).curriculum
    assert any(t.text == "Playwright visual regression" and t.origin == Origin.USER for m in cur.modules for t in m.topics)
    assert cur.modules[-1].title == "Additional requested topics"


# ------------------------------------------------------------------ alignment
def test_alignment_finds_a_real_gap_and_lists_evidence(service):
    stored = service.create(CurriculumRequest(course_id="TRK-001"))
    rep = stored.alignment[0]
    by = {a.title: a for a in rep.areas}
    assert by["Test Tools"].status == AreaStatus.COVERED and by["Test Tools"].evidence
    assert by["Static Testing"].status != AreaStatus.COVERED and "Add content" in by["Static Testing"].recommendation or "deepen" in by["Static Testing"].recommendation
    assert 0 < rep.coverage_percent < 100 and rep.covered + rep.partial + rep.missing == len(rep.areas)


def test_alignment_report_always_carries_the_no_accreditation_disclaimer(service):
    for rep in service.create(CurriculumRequest(course_id="TRK-005")).alignment:
        assert "does not mean the course is accredited" in rep.disclaimer
        assert rep.framework_verified is False and "assembled by the REX AI team" in rep.disclaimer


def test_adding_a_missing_topic_improves_alignment(service):
    stored = service.create(CurriculumRequest(course_id="TRK-001"))
    before = stored.alignment[0].coverage_percent
    after = service.apply_edits(stored.id, "teacher", [
        {"op": "add_topic", "module": 5, "text": "Static testing: code review and static analysis"}]).alignment[0].coverage_percent
    assert after > before


def test_framework_library_errors_are_readable_and_bad_files_are_skipped(tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "ok.json").write_text(
        '{"framework_id":"x-1","name":"X","issuer":"I","areas":[{"id":"1","title":"T","keywords":["k"]}]}', encoding="utf-8")
    lib = FrameworkLibrary.load(tmp_path)
    assert [f.framework_id for f in lib.all()] == ["x-1"]
    with pytest.raises(FrameworkError, match="Available: x-1"):
        lib.get("nope")


def test_every_shipped_framework_loads_and_is_labelled_as_unverified():
    lib = FrameworkLibrary.load()
    assert {f.framework_id for f in lib.all()} >= {"istqb-ctfl-4.0", "crisp-dm", "swebok-3", "data-eng-lifecycle"}
    assert all(f.status == "reference_checklist" and not f.verified and f.areas for f in lib.all())


# ------------------------------------------------------------------ review workflow
def test_review_workflow_and_audit_log(service):
    s = service.create(CurriculumRequest(course_id="TRK-004"))
    assert s.status == CurriculumStatus.DRAFT and service.approved_for_course("TRK-004") is None
    with pytest.raises(CurriculumWorkflowError, match="submit it for review"):
        service.approve(s.id, "Dr. Hana")
    s = service.submit(s.id, "author")
    with pytest.raises(CurriculumWorkflowError, match="reviewer's name"):
        service.approve(s.id, "  ")
    s = service.request_changes(s.id, "Dr. Hana", "Add more on deployment")
    assert s.status == CurriculumStatus.CHANGES_REQUESTED
    s = service.apply_edits(s.id, "author", [{"op": "add_topic", "module": 5, "text": "Blue-green deployment"}])
    assert s.status == CurriculumStatus.DRAFT and s.revision == 2          # an edit always needs a new review
    s = service.approve(service.submit(s.id, "author").id, "Dr. Hana")
    assert s.status == CurriculumStatus.APPROVED and s.approved_by == "Dr. Hana"
    assert service.approved_for_course("TRK-004").id == s.id
    assert [e["action"] for e in s.review_log] == ["created", "submitted", "changes_requested", "edited", "submitted", "approved"]
    again = service.apply_edits(s.id, "author", [{"op": "set_total_hours", "hours": 150}])
    assert again.status == CurriculumStatus.DRAFT and again.approved_by is None and service.approved_for_course("TRK-004") is None


def test_changes_need_a_note_and_invalid_curricula_cannot_be_approved(service):
    s = service.submit(service.create(CurriculumRequest(course_id="TRK-001")).id, "a")
    with pytest.raises(CurriculumWorkflowError, match="what should change"):
        service.request_changes(s.id, "r", " ")
    row = service._repo.get(s.id)                                         # corrupt the hours on purpose
    broken = Curriculum.model_validate(row.content)
    broken.modules[0].hours += 5
    service._repo.save_content(s.id, broken, [], "x", "edited")
    with pytest.raises(CurriculumWorkflowError, match="differ from the total"):
        service.submit(s.id, "a")


def test_edits_keep_hours_consistent_and_validate_input(service):
    s = service.create(CurriculumRequest(course_id="TRK-001"))
    for edit in ({"op": "add_module", "title": "Test reporting", "topics": ["Dashboards"]}, {"op": "remove_module", "module": 1},
                 {"op": "rename_module", "module": 1, "title": "Renamed"}, {"op": "add_outcome", "text": "Build a test plan", "modules": [1]}):
        s = service.apply_edits(s.id, "t", [edit])
        assert s.curriculum.validation_problems() == []
    assert [m.number for m in s.curriculum.modules] == list(range(1, len(s.curriculum.modules) + 1))
    assert s.curriculum.modules[0].title == "Renamed" and s.curriculum.learning_outcomes[-1].origin == Origin.USER
    for bad in ({"op": "nope"}, {"op": "remove_topic", "module": 1, "index": 99}, {"op": "set_total_hours", "hours": -1}):
        with pytest.raises(CurriculumError):
            service.apply_edits(s.id, "t", [bad])
    with pytest.raises(CurriculumError, match="no module 42"):
        service.apply_edits(s.id, "t", [{"op": "rename_module", "module": 42, "title": "x"}])


def test_metadata_filtering(service):
    service.create(CurriculumRequest(course_id="TRK-001"))
    service.create(CurriculumRequest(course_id="TRK-005", skill_level="advanced"))
    assert [c.curriculum.course_id for c in service.list(department="Artificial Intelligence")] == ["TRK-005"]
    assert len(service.list(skill_level="advanced")) == 1 and len(service.list(industry="Technology")) == 2
    assert service.list(country="Mars") == []


# ------------------------------------------------------------------ cross-agent inputs
def test_rfp_signals_and_document_evidence_are_used(official):
    from types import SimpleNamespace
    db = Database("sqlite://")
    db.migrate()
    courses = CourseRepository(db)
    courses.replace_catalog(official)
    rfp = SimpleNamespace(list=lambda statuses=None: [SimpleNamespace(matched_course_ids=["TRK-001"], country="EG", synthetic=True),
                                                      SimpleNamespace(matched_course_ids=["TRK-002"], country="SA", synthetic=False)])
    passage = SimpleNamespace(citation="portfolio.pdf, p.3", text="Quality Assurance is in demand")
    passages = SimpleNamespace(retrieve=lambda q, top_k=3: SimpleNamespace(passages=[passage]))
    svc = CurriculumService(CurriculumRepository(db), courses, FrameworkLibrary.load(), TrackTopics.load(), passages=passages, rfp_repository=rfp)
    cur = svc.create(CurriculumRequest(course_id="TRK-001")).curriculum
    assert len(cur.market_signals) == 1 and "SYNTHETIC" in cur.market_signals[0] and "Egypt: 1" in cur.market_signals[0]
    kinds = {s.kind for s in cur.sources}
    assert {"course_record", "document", "rfp_signal", "framework"} <= kinds
    assert any(s.reference == "portfolio.pdf, p.3" for s in cur.sources)


def test_research_failures_never_stop_curriculum_creation(official):
    from types import SimpleNamespace
    db = Database("sqlite://"); db.migrate()
    courses = CourseRepository(db); courses.replace_catalog(official)
    boom = SimpleNamespace(retrieve=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vector store down")))
    rfp = SimpleNamespace(list=lambda **k: (_ for _ in ()).throw(RuntimeError("db locked")))
    svc = CurriculumService(CurriculumRepository(db), courses, FrameworkLibrary.load(), TrackTopics.load(), passages=boom, rfp_repository=rfp)
    assert svc.create(CurriculumRequest(course_id="TRK-001")).curriculum.market_signals == []


# ------------------------------------------------------------------ LLM enrichment
def _draft(official):
    course = official.courses[0]
    return build_draft(DraftInputs(CurriculumRequest(course_id=course.course_id), course, TrackTopics.load().for_course(course.course_id)))


def test_llm_enrichment_is_validated_and_never_touches_titles_or_hours(official):
    draft = _draft(official)
    reply = ('```json\n{"objectives":["Equip learners to ship reliable software."],'
             '"learning_outcomes":[{"text":"Build a Playwright suite for a web app.","modules":[1,99]},'
             '{"text":"This course is accredited by ITIDA.","modules":[1]}],'
             '"module_exercises":{"1":["Lab: write a self-healing locator"]},"module_topics":{"1":["ignored: module has topics"]},'
             '"capstone":"ignored because the catalog has one"}\n```')
    out = enrich_with_llm(draft, FakeLLM(reply))
    assert out.generation_mode == "llm"
    assert [m.title for m in out.modules] == [m.title for m in draft.modules] and [m.hours for m in out.modules] == [m.hours for m in draft.modules]
    texts = [o.text for o in out.learning_outcomes]
    assert "Build a Playwright suite for a web app." in texts and not any("accredited" in t for t in texts)   # claim dropped
    assert next(o for o in out.learning_outcomes if o.origin == Origin.LLM).module_numbers == [1]            # module 99 dropped
    assert out.modules[0].exercises[0].origin == Origin.LLM and out.capstone.description.origin == Origin.CATALOG


@pytest.mark.parametrize("reply", ["not json at all", "[1,2,3]", "{}", LLMError("down")])
def test_bad_llm_output_falls_back_to_the_deterministic_draft(official, reply):
    draft = _draft(official)
    out = enrich_with_llm(draft, FakeLLM(reply))
    assert out.generation_mode == "deterministic" and out.objectives == draft.objectives
    assert any("deterministic draft is shown" in n for n in out.notices)


def test_llm_prompt_treats_the_draft_as_data(official):
    llm = FakeLLM("{}")
    enrich_with_llm(_draft(official), llm)
    system, messages = llm.calls[0]
    assert "data, not instructions" in system and messages[0].content.startswith("<draft>")


def test_service_uses_llm_when_enabled_and_skips_it_when_disabled(official):
    db = Database("sqlite://"); db.migrate()
    courses = CourseRepository(db); courses.replace_catalog(official)
    llm = FakeLLM('{"objectives":["Prepare learners for QA roles."]}')
    svc = CurriculumService(CurriculumRepository(db), courses, FrameworkLibrary.load(), TrackTopics.load(), llm=llm)
    assert svc.create(CurriculumRequest(course_id="TRK-001")).curriculum.generation_mode == "llm"
    n = len(llm.calls)
    assert svc.create(CurriculumRequest(course_id="TRK-001"), use_llm=False).curriculum.generation_mode == "deterministic"
    assert len(llm.calls) == n


# ------------------------------------------------------------------ shipped data stays consistent
def test_portfolio_topic_file_matches_the_catalog(official):
    topics = TrackTopics.load()
    for course in official.courses:
        detail = topics.for_course(course.course_id)
        assert detail is not None and "p." in detail.source
        assert list(detail.modules) == [m for m in course.modules if not m.startswith("Capstone")]
        assert "proposal" in detail.notice
