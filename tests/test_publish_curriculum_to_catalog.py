"""An approved curriculum for a NEW course is published to the shared catalog, so every agent sees it
without a restart (REX, Marketing, RFP, Document Generation). Found 2026-11-03: a "Cloud" curriculum
was approved but REX said "not in our catalog" and Marketing could not select it."""
import pytest

from src.agents.base import AgentRequest
from src.bootstrap import build_app
from src.config import load_settings
from src.curriculum.models import CurriculumRequest
from src.curriculum.service import CurriculumWorkflowError
from src.knowledge_base.loader import load_catalog
from tests.conftest import OFFICIAL_DATASET


@pytest.fixture
def ctx(test_env):
    return build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))),
                     use_configured_llm=False, enable_rag=False)


def cloud(ctx, approve: bool = True):
    svc = ctx.curriculum_service
    cur = svc.create(CurriculumRequest(title="Cloud Computing", topics=["AWS basics", "Terraform", "Docker"],
                                       duration_hours=90), use_llm=False)
    svc.submit(cur.id, "nadin")
    return svc.approve(cur.id, "Dr. Reviewer") if approve else svc.get(cur.id)


def test_unapproved_curriculum_is_not_published(ctx):
    cloud(ctx, approve=False)
    assert [c.course_id for c in ctx.courses.list_approved()] == ["TRK-001", "TRK-002", "TRK-003", "TRK-004", "TRK-005"]


def test_approval_publishes_and_links_the_new_course(ctx):
    stored = cloud(ctx)
    assert stored.curriculum.course_id == "TRK-006"
    course = ctx.courses.get("TRK-006")
    assert course.title == "Cloud Computing" and course.duration_hours == 90
    assert "Approved curriculum" in course.source.reference and "Dr. Reviewer" in course.source.reference
    assert course.fee.amount is None and course.schedule is None          # not in a curriculum: stays unknown
    assert ctx.curriculum_service.approved_for_course("TRK-006").id == stored.id


def test_rex_recommends_it_without_a_restart(ctx):
    rex = ctx.registry.get("rex_assistant")
    before = rex.handle(AgentRequest.from_text("i want to learn cloud"))
    assert not any(s.course_id == "TRK-006" for s in before.sources)
    cloud(ctx)
    after = rex.handle(AgentRequest.from_text("i want to learn cloud"))
    assert after.sources and after.sources[0].course_id == "TRK-006"
    assert "structured course catalog" not in after.text          # it IS in the catalog now
    assert "TRK-006" in rex.handle(AgentRequest.from_text("what courses do you have?")).text


def test_marketing_sees_the_course_and_its_curriculum(ctx):
    stored = cloud(ctx)
    ctx.registry.dispatch("marketing", AgentRequest.from_text("Cloud Computing on LinkedIn for registrations"))
    campaigns = ctx.marketing.list_campaigns()
    assert campaigns and campaigns[0].course_id == "TRK-006"
    assert any(stored.id in f["source"] for f in campaigns[0].facts_snapshot["facts"])


def test_rfp_and_documents_see_it(ctx):
    cloud(ctx)
    shared = ctx.registry.get("rex_assistant")._retriever     # the same retriever RFP matching reads
    assert "TRK-006" in [c.course_id for c in shared.all_courses()]
    from src.documents.models import DocType, DocumentRequest
    doc = ctx.document_service.generate(DocumentRequest(doc_type=list(DocType)[0], course_id="TRK-006"), use_llm=False)
    assert doc.course_id == "TRK-006"


def test_second_new_course_gets_the_next_id(ctx):
    cloud(ctx)
    svc = ctx.curriculum_service
    cur = svc.create(CurriculumRequest(title="Cybersecurity Basics", topics=["Networks", "Threats"], duration_hours=40),
                     use_llm=False)
    svc.submit(cur.id, "nadin")
    assert svc.approve(cur.id, "Dr. Reviewer").curriculum.course_id == "TRK-007"


def test_catalog_course_curriculum_does_not_create_a_course(ctx):
    svc = ctx.curriculum_service
    cur = svc.create(CurriculumRequest(course_id="TRK-001"), use_llm=False)
    svc.submit(cur.id, "nadin"); svc.approve(cur.id, "Dr. Reviewer")
    assert len(ctx.courses.list_approved()) == 5


def test_reseeding_the_catalog_keeps_published_courses(ctx):
    cloud(ctx)
    ctx.courses.replace_catalog(load_catalog(OFFICIAL_DATASET), source_file=str(OFFICIAL_DATASET))
    assert ctx.courses.get("TRK-006") is not None


def test_publish_failure_leaves_the_curriculum_unapproved(ctx, monkeypatch):
    stored = cloud(ctx, approve=False)
    monkeypatch.setattr(ctx.courses, "add", lambda c: (_ for _ in ()).throw(ValueError("db down")))
    with pytest.raises(CurriculumWorkflowError, match="Could not publish"):
        ctx.curriculum_service.approve(stored.id, "Dr. Reviewer")
    assert ctx.curriculum_service.get(stored.id).status.value != "approved"


def test_arabic_request_finds_the_published_course(ctx):
    cloud(ctx)
    rex = ctx.registry.get("rex_assistant")
    for q in ("عايز اتعلم كلاود", "أريد تعلم الحوسبة السحابية"):
        r = rex.handle(AgentRequest.from_text(q))
        assert r.sources and r.sources[0].course_id == "TRK-006", q
