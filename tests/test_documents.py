"""Agent 4 — templates, bilingual output, LLM helpers, review workflow, e-mail, repository."""
import json
import re
from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from src.config import PROJECT_ROOT, load_settings
from src.curriculum.frameworks import FrameworkLibrary
from src.curriculum.models import CurriculumRequest
from src.curriculum.service import CurriculumService
from src.curriculum.topics import TrackTopics
from src.db.curriculum_repository import CurriculumRepository
from src.db.database import Database
from src.db.document_repository import GeneratedDocumentRepository
from src.db.repositories import CourseRepository
from src.documents.context import DocumentError
from src.documents.llm_helpers import generate_mcq, translate_document
from src.documents.models import DocStatus, DocType, DocumentRequest
from src.documents.service import DocumentService
from src.documents.templates import TEMPLATE_VERSION, _milestone_hours, _split
from src.knowledge_base.loader import load_catalog
from src.pdf.content import DocumentContent, Section, para
from src.pdf.renderer import has_arabic
from src.services.email_service import EmailError, EmailService, parse_recipients
from src.services.llm_service import LLMError

OFFICIAL = PROJECT_ROOT / "data" / "courses_official.json"
TODAY = date(2026, 10, 25)


class FakeLLM:
    name = "fake"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if callable(self.reply):
            return self.reply(system_prompt, messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def echo_translator(system, messages):
    """A 'translation' that tags every string, so tests can see exactly what was sent."""
    return json.dumps(["ترجمة: " + s for s in json.loads(messages[0].content)], ensure_ascii=False)


@pytest.fixture
def world():
    db = Database("sqlite://")
    db.migrate()
    courses = CourseRepository(db)
    courses.replace_catalog(load_catalog(OFFICIAL))
    curricula = CurriculumService(CurriculumRepository(db), courses, FrameworkLibrary.load(), TrackTopics.load())
    return db, courses, curricula


def make_service(world, llm=None, email=None):
    db, courses, curricula = world
    return DocumentService(GeneratedDocumentRepository(db), curricula, courses, llm=llm, email=email, today=lambda: TODAY)


@pytest.fixture
def svc(world):
    return make_service(world)


def req(doc_type, **kw):
    kw.setdefault("course_id", "TRK-001")
    return DocumentRequest(doc_type=doc_type, **kw)


def all_text(doc: DocumentContent) -> str:
    parts = [doc.title, doc.subtitle, doc.disclaimer, *[v for _, v in doc.meta]]
    for s in doc.sections:
        parts.append(s.heading)
        for b in s.blocks:
            parts += [b.text, *b.items, *b.header, *[c for r in b.rows for c in r]]
    return "\n".join(parts)


# ------------------------------------------------------------------ every type, both languages
@pytest.mark.parametrize("language", ["en", "ar"])
@pytest.mark.parametrize("doc_type", list(DocType))
def test_every_document_type_builds_stores_and_renders(svc, doc_type, language):
    doc = svc.generate(req(doc_type, language=language))
    assert doc.status == DocStatus.DRAFT and doc.revision == 1 and doc.template_version == TEMPLATE_VERSION
    assert doc.content.sections and all(s.blocks for s in doc.content.sections)
    pdf = svc.pdf(doc.id)
    pages = PdfReader(BytesIO(pdf)).pages
    assert pdf.startswith(b"%PDF") and len(pages) >= 1
    expected_language = "en" if doc_type == DocType.CURRICULUM_PDF else language     # curriculum PDF is English-only
    assert doc.language == expected_language and (doc.content.language == expected_language)


@pytest.mark.parametrize("course_id", ["TRK-001", "TRK-002", "TRK-003", "TRK-004", "TRK-005"])
def test_every_official_course_works_for_every_type(svc, course_id):
    for doc_type in DocType:
        assert svc.pdf(svc.generate(req(doc_type, course_id=course_id)).id).startswith(b"%PDF")


def test_arabic_documents_use_arabic_headings_and_the_catalog_arabic_title(svc):
    doc = svc.generate(req(DocType.PARTICIPANT_GUIDE, course_id="TRK-002", language="ar"))
    assert doc.content.language == "ar" and has_arabic(doc.title) and "فلاتر" in doc.title
    assert all(has_arabic(s.heading) for s in doc.content.sections[:4])
    assert any("English" in n and "LLM" in n for n in doc.notices)          # honest about untranslated content


# ------------------------------------------------------------------ honesty
def test_unpublished_facts_are_never_invented(svc):
    text = all_text(svc.generate(req(DocType.COURSE_DESCRIPTION)).content)
    assert text.count("Not published") >= 3                             # fee, schedule, instructor are unpublished
    assert not re.search(r"\b(EGP|USD|SAR)\b|\$\s?\d", text)
    proposal = all_text(svc.generate(req(DocType.COURSE_PROPOSAL)).content)
    assert "Not published" in proposal and "No accreditation is claimed" in proposal


@pytest.mark.parametrize("language", ["en", "ar"])
def test_no_document_claims_accreditation(svc, language):
    banned = re.compile(r"\b(is|are|was|fully|officially)\s+(officially\s+)?accredited\b", re.I)
    negations = ("does not state or imply that the course is accredited", "no accreditation is claimed", "does not mean the course is accredited",
                 "never states that a course is accredited")
    for doc_type in DocType:
        text = all_text(svc.generate(req(doc_type, language=language)).content).lower()
        for phrase in negations:
            text = text.replace(phrase, "")
        assert not banned.search(text), doc_type


def test_template_proposals_are_labelled(svc):
    assert "template proposal" in all_text(svc.generate(req(DocType.INSTRUCTOR_GUIDE)).content).lower()
    assert "template proposal" in all_text(svc.generate(req(DocType.ASSESSMENT_RUBRICS)).content).lower()


# ------------------------------------------------------------------ data basis
def test_documents_prefer_the_approved_curriculum_and_say_so(world, svc):
    first = svc.generate(req(DocType.CAPSTONE_BRIEF))
    assert first.curriculum_id is None and "no approved curriculum" in first.basis and any("No approved curriculum" in n for n in first.notices)
    curricula = world[2]
    cur = curricula.create(CurriculumRequest(course_id="TRK-001", duration_hours=80))
    curricula.approve(curricula.submit(cur.id, "a").id, "Dr. Hana")
    second = svc.generate(req(DocType.CAPSTONE_BRIEF))
    assert second.curriculum_id == cur.id and second.basis.startswith("Approved curriculum") and second.curriculum_revision == 1
    assert "12" in all_text(second.content)                              # 80 h curriculum -> 12 capstone hours, not the catalog's 15


def test_strict_mode_and_unknown_inputs_are_rejected_with_readable_errors(svc, world):
    with pytest.raises(DocumentError, match="no approved curriculum"):
        svc.generate(req(DocType.CAPSTONE_BRIEF, require_approved_curriculum=True))
    with pytest.raises(DocumentError, match="not in the approved catalog"):
        svc.generate(req(DocType.CAPSTONE_BRIEF, course_id="ZZZ-999"))
    with pytest.raises(DocumentError, match="No curriculum with ID CUR-FFFFFF"):
        svc.generate(DocumentRequest(doc_type=DocType.CAPSTONE_BRIEF, curriculum_id="CUR-FFFFFF"))
    with pytest.raises(ValueError):
        DocumentRequest(doc_type=DocType.CAPSTONE_BRIEF)
    cur = world[2].create(CurriculumRequest(course_id="TRK-002"))
    with pytest.raises(DocumentError, match="approve it first"):
        svc.generate(DocumentRequest(doc_type=DocType.CAPSTONE_BRIEF, curriculum_id=cur.id, require_approved_curriculum=True))
    unapproved = svc.generate(DocumentRequest(doc_type=DocType.CAPSTONE_BRIEF, curriculum_id=cur.id))
    assert any("which is draft" in n for n in unapproved.notices)


def test_new_course_curriculum_can_be_the_source_of_a_document(world, svc):
    cur = world[2].create(CurriculumRequest(title="Intro to Cybersecurity", topics=["Network security", "Cryptography"], duration_hours=30))
    doc = svc.generate(DocumentRequest(doc_type=DocType.PARTICIPANT_GUIDE, curriculum_id=cur.id))
    assert "Intro to Cybersecurity" in doc.title and doc.course_id is None
    assert "Network security" in all_text(doc.content)


# ------------------------------------------------------------------ template content
def test_hours_in_generated_plans_add_up():
    for h in (15, 18, 21, 17, 12.5, 1):
        assert round(sum(_split(h)), 2) == h and all(x >= 0 for x in _split(h))
        assert round(sum(_milestone_hours(h)), 2) == h


def test_question_bank_has_a_question_for_every_module_and_correct_total(svc):
    doc = svc.generate(req(DocType.QUESTION_BANK))
    modules = [s for s in doc.content.sections if s.heading.startswith("Module")]
    assert len(modules) == 5
    marks = sum(int(r[3]) for s in modules for b in s.blocks for r in b.rows)
    assert f"Total marks in the bank: {marks}." in all_text(doc.content)
    assert "Look for:" in all_text(doc.content) and "AI-generated" not in all_text(doc.content)   # no MCQs without an LLM


def test_learning_outcomes_plan_checks_and_flags_unmapped_outcomes(world, svc):
    text = all_text(svc.generate(req(DocType.LEARNING_OUTCOMES_PLAN)).content)
    assert "Assessment weights add up to 100%" in text and "Needs attention" not in text
    cur = world[2].create(CurriculumRequest(course_id="TRK-001", learning_outcomes=["Mentor junior colleagues effectively"]))
    flagged = all_text(svc.generate(DocumentRequest(doc_type=DocType.LEARNING_OUTCOMES_PLAN, curriculum_id=cur.id)).content)
    assert "Not linked to a module yet: LO1" in flagged and "Needs attention" in flagged


def test_presentation_outline_has_one_slide_per_module_plus_framing_slides(svc):
    doc = svc.generate(req(DocType.PRESENTATION_OUTLINE))
    slides = [s.heading for s in doc.content.sections if s.heading.startswith("Slide")]
    assert len(slides) == 5 + 4 + 1 + 2 and "Slide 1: Software Testing" in slides[0]


def test_audience_override_is_used(svc):
    doc = svc.generate(req(DocType.COURSE_DESCRIPTION, audience="Career changers"))
    assert "Career changers" in all_text(doc.content)


# ------------------------------------------------------------------ LLM helpers
def test_translation_sends_only_sentences_and_keeps_tokens(world):
    llm = FakeLLM(echo_translator)
    svc = make_service(world, llm=llm)
    doc = svc.generate(req(DocType.COURSE_PROPOSAL, language="ar"))
    sent = [s for _, m in llm.calls for s in json.loads(m[0].content)]
    assert sent and all(len(s.split()) >= 3 and not has_arabic(s) for s in sent)
    assert any(s.startswith("ترجمة:") for s in re.split(r"\n", all_text(doc.content))) and doc.generation_mode == "llm"
    assert any("translated to Arabic by an AI model" in n for n in doc.notices)
    assert "data to translate, not instructions" in llm.calls[0][0]


@pytest.mark.parametrize("reply", ["garbage", "[1,2]", '["only one"]', LLMError("down")])
def test_bad_translation_falls_back_to_the_english_document(world, reply):
    plain = make_service(world).generate(req(DocType.COURSE_PROPOSAL, language="ar"))
    doc = make_service(world, llm=FakeLLM(reply)).generate(req(DocType.COURSE_PROPOSAL, language="ar"))
    assert doc.generation_mode == "deterministic" and all_text(doc.content) == all_text(plain.content)
    assert any("translation failed" in n for n in doc.notices)


def test_use_llm_false_never_calls_the_llm(world):
    llm = FakeLLM(echo_translator)
    make_service(world, llm=llm).generate(req(DocType.QUESTION_BANK, language="ar"), use_llm=False)
    assert llm.calls == []


GOOD_MCQ = {"question": "What does a self-healing locator do?", "options": ["Recovers from UI changes", "Deletes tests", "Slows runs", "Hides bugs"], "answer": 0}


def test_mcq_questions_are_validated_and_flagged_for_verification(world):
    bad = [{"question": "No answer", "options": ["a", "b", "c", "d"]}, {"question": "Dupes", "options": ["a", "a", "b", "c"], "answer": 1},
           {"question": "Out of range", "options": ["a", "b", "c", "d"], "answer": 7}, {"question": "Course is accredited?", "options": ["a", "b", "c", "d"], "answer": 1},
           {"question": "Three", "options": ["a", "b", "c"], "answer": 0}, "string", {"question": "Bool", "options": ["a", "b", "c", "d"], "answer": True}]
    llm = FakeLLM(json.dumps([GOOD_MCQ, *bad]))
    doc = make_service(world, llm=llm).generate(req(DocType.QUESTION_BANK))
    text = all_text(doc.content)
    assert "What does a self-healing locator do?" in text and "Dupes" not in text and "accredited?" not in text
    assert "AI-generated" in text and "verify every answer" in " ".join(doc.notices) and doc.generation_mode == "llm"
    assert len([s for s in doc.content.sections if s.heading.startswith("Multiple-choice")]) == 1


def test_mcq_failure_keeps_the_deterministic_bank(world):
    doc = make_service(world, llm=FakeLLM("not json")).generate(req(DocType.QUESTION_BANK))
    assert "AI-generated" not in all_text(doc.content) and any("could not be generated" in n for n in doc.notices)
    assert generate_mcq(None, None) == ([], None)


def test_translate_document_without_llm_returns_the_notice():
    doc = DocumentContent(title="T", language="ar", sections=[Section(heading="ع", blocks=[para("A long English sentence here.")])])
    same, notice = translate_document(doc, None)
    assert same == doc and "Enable an LLM provider" in notice


# ------------------------------------------------------------------ review workflow
def test_edit_approve_edit_cycle_and_log(svc):
    doc = svc.generate(req(DocType.COURSE_DESCRIPTION), actor="author")
    md = svc.markdown(doc.id)
    assert "## Overview" in md
    edited = svc.edit_markdown(doc.id, md.replace("## Overview", "## Summary"), "editor", "renamed a heading")
    assert edited.revision == 2 and edited.content.sections[0].heading == "Summary" and edited.status == DocStatus.DRAFT
    assert edited.content.meta == doc.content.meta                       # cover facts survive an edit
    with pytest.raises(DocumentError, match="reviewer's name"):
        svc.approve(doc.id, "  ")
    approved = svc.approve(doc.id, "Dr. Hana", "looks good")
    assert approved.status == DocStatus.APPROVED and approved.approved_by == "Dr. Hana"
    with pytest.raises(DocumentError, match="already approved"):
        svc.approve(doc.id, "Dr. Hana")
    again = svc.edit_markdown(doc.id, svc.markdown(doc.id) + "\n\nExtra paragraph.", "editor")
    assert again.status == DocStatus.DRAFT and again.approved_by is None and again.revision == 3
    assert [e["action"] for e in again.log] == ["generated", "edited", "approved", "edited"]


def test_empty_or_oversized_edits_are_rejected(svc):
    doc = svc.generate(req(DocType.COURSE_DESCRIPTION))
    with pytest.raises(DocumentError, match="would be empty"):
        svc.edit_markdown(doc.id, "# Only a title", "e")
    with pytest.raises(DocumentError, match="too long"):
        svc.edit_markdown(doc.id, "## S\n\n" + "x" * 400_000, "e")
    with pytest.raises(DocumentError, match="No document with ID DOC-FFFFFF"):
        svc.approve("DOC-FFFFFF", "r")
    assert svc.get(doc.id).revision == 1                                 # failed edits changed nothing


def test_printed_output_shows_draft_banner_until_approved(svc):
    doc = svc.generate(req(DocType.COURSE_PROPOSAL))
    draft = svc.output_content(doc)
    assert draft.sections[0].heading == "Review status" and "DRAFT" in all_text(draft) and "Notes for the reviewer" in all_text(draft)
    approved = svc.output_content(svc.approve(doc.id, "Dr. Hana"))
    assert "Review status" not in all_text(approved) and "approved by Dr. Hana" in all_text(approved) and "Notes for the reviewer" not in all_text(approved)
    text = "".join(p.extract_text() for p in PdfReader(BytesIO(svc.pdf(doc.id))).pages)
    assert "approved by Dr. Hana" in text


def test_markdown_edits_to_arabic_documents_keep_them_arabic(svc):
    doc = svc.generate(req(DocType.CAPSTONE_BRIEF, language="ar"))
    edited = svc.edit_markdown(doc.id, svc.markdown(doc.id), "e")
    assert edited.content.language == "ar" and svc.pdf(doc.id).startswith(b"%PDF")


# ------------------------------------------------------------------ e-mail
def email_settings(**over):
    env = {"REX_COURSES_PATH": str(OFFICIAL), "SMTP_HOST": "smtp.test", "SMTP_FROM": "Academy <a@test.org>", **over}
    return load_settings(env)


def test_email_config_is_off_by_default_and_hides_the_password():
    assert not load_settings({"REX_COURSES_PATH": str(OFFICIAL)}).email_enabled
    s = email_settings(SMTP_PASSWORD="hunter2", SMTP_PORT="2525", SMTP_STARTTLS="off")
    assert s.email_enabled and s.smtp_port == 2525 and not s.smtp_starttls and "hunter2" not in repr(s)


def test_only_approved_documents_are_emailed_with_the_pdf_attached(world, caplog):
    sent = []
    svc = make_service(world, email=EmailService(email_settings(), transport=sent.append))
    doc = svc.generate(req(DocType.COURSE_DESCRIPTION))
    with pytest.raises(DocumentError, match="Only approved documents"):
        svc.email(doc.id, "student@example.com", "admin")
    svc.approve(doc.id, "Dr. Hana")
    with caplog.at_level("INFO"):
        assert svc.email(doc.id, "student@example.com; boss@example.com", "admin", "Hello") == 2
    msg = sent[0]
    assert msg["To"] == "student@example.com, boss@example.com" and msg["From"] == "Academy <a@test.org>"
    attachment = next(msg.iter_attachments())
    assert attachment.get_content_type() == "application/pdf" and attachment.get_payload(decode=True).startswith(b"%PDF")
    assert svc.get(doc.id).log[-1]["action"] == "emailed" and svc.get(doc.id).log[-1]["note"] == "2 recipient(s)"
    assert "student@example.com" not in caplog.text and "student@example.com" not in str(svc.get(doc.id).log)   # no addresses in logs


@pytest.mark.parametrize("raw,message", [("", "at least one"), ("not-an-email", "not valid"), (",".join(f"u{i}@b.co" for i in range(11)), "At most 10"), ("x@y", "not valid")])
def test_recipient_validation(raw, message):
    with pytest.raises(EmailError, match=message):
        parse_recipients(raw)


def test_duplicate_recipients_are_sent_once():
    assert parse_recipients("A@b.co, a@b.co; c@d.org") == ["a@b.co", "c@d.org"]


def test_email_errors_are_safe_and_disabled_mode_is_explicit(world):
    off = make_service(world, email=EmailService(load_settings({"REX_COURSES_PATH": str(OFFICIAL)})))
    doc = off.generate(req(DocType.COURSE_DESCRIPTION))
    off.approve(doc.id, "r")
    assert not off.email_enabled
    with pytest.raises(EmailError, match="not configured"):
        off.email(doc.id, "a@b.co", "r")

    def boom(msg):
        raise OSError("connection refused: secret-host")
    failing = make_service(world, email=EmailService(email_settings(), transport=boom))
    d2 = failing.generate(req(DocType.COURSE_DESCRIPTION))
    failing.approve(d2.id, "r")
    with pytest.raises(EmailError) as err:
        failing.email(d2.id, "a@b.co", "r")
    assert "secret-host" not in str(err.value) and not any(e["action"] == "emailed" for e in failing.get(d2.id).log)
    with pytest.raises(EmailError, match="line breaks"):
        EmailService(email_settings(), transport=lambda m: None).send("a@b.co", "Hi\nBcc: x@y.co", "body")


# ------------------------------------------------------------------ repository
def test_library_filters(svc):
    a = svc.generate(req(DocType.COURSE_DESCRIPTION))
    svc.generate(req(DocType.QUESTION_BANK, course_id="TRK-002", language="ar"))
    svc.approve(a.id, "r")
    assert len(svc.list()) == 2 and [d.id for d in svc.list(status="approved")] == [a.id]
    assert [d.course_id for d in svc.list(language="ar")] == ["TRK-002"] and len(svc.list(doc_type="question_bank")) == 1
    assert svc.list(course_id="TRK-009") == []
