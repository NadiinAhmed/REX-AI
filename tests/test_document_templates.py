"""Agent 4 — academy-approved templates: reading the outline, applying it, and the approval workflow."""
import io
import zipfile

import pytest

from src.documents.academy_templates import TemplateError, apply_outline, read_outline, similarity
from src.documents.context import DocumentError
from src.documents.models import DocType, DocumentRequest
from src.pdf.content import DocumentContent, Section, para

ACADEMY_HEADINGS = ["Introduction", "Learning Objectives", "Course Content", "Assessment", "Final Project", "Code of Conduct"]


def make_docx(paragraphs: list[tuple[str | None, str]]) -> bytes:
    """A minimal real .docx: (style, text) pairs, e.g. ("Heading1", "Introduction") or (None, "body text")."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    parts = []
    for style, text in paragraphs:
        props = '<w:pPr><w:pStyle w:val="' + style + '"/></w:pPr>' if style else ""
        parts.append("<w:p>" + props + "<w:r><w:t>" + text + "</w:t></w:r></w:p>")
    body = "".join(parts)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buffer.getvalue()


ACADEMY_DOCX = make_docx([("Title", "Andalusia Academy Participant Guide"), ("Heading1", "1. Introduction"),
                          (None, "Welcome text that must NOT be copied."), ("Heading1", "2. Learning Objectives"),
                          ("Heading1", "3. Course Content"), ("Heading2", "Assessment"), ("Heading1", "Final Project"),
                          ("Heading1", "Code of Conduct"), ("Heading3", "Too deep: ignored")])


# ------------------------------------------------------------------ reading
def test_docx_outline_uses_heading_styles_and_strips_numbering():
    assert read_outline("guide.docx", ACADEMY_DOCX) == ACADEMY_HEADINGS


def test_markdown_and_text_outlines():
    md = b"# Introduction\nSome text.\n## 2. Learning objectives\n### Assessment\nPlain line that is a sentence.\n"
    assert read_outline("t.md", md) == ["Introduction", "Learning objectives", "Assessment"]
    txt = "INTRODUCTION\nThis paragraph is body text, so it ends with a period.\n1. Course Content\nAssessment Plan\n".encode()
    assert read_outline("t.txt", txt) == ["INTRODUCTION", "Course Content", "Assessment Plan"]


def test_pdf_outline(make_pdf):
    pdf = make_pdf("guide.pdf", ["Introduction\nWelcome to the programme, we hope you enjoy it.\nLearning Objectives",
                                 "Course Content\nThe course is delivered over eight weeks in Cairo.\nAssessment\nPage 2"])
    assert read_outline("guide.pdf", pdf.read_bytes()) == ["Introduction", "Learning Objectives", "Course Content", "Assessment"]


@pytest.mark.parametrize("name, data, message", [
    ("guide.pptx", b"x", "Upload a Word"),
    ("guide.docx", b"not a zip", "could not be read"),
    ("guide.md", b"No headings here, just a sentence.", "No section headings"),
    ("guide.pdf", b"%PDF-broken", "could not be read"),
])
def test_unusable_files_are_rejected_with_a_clear_message(name, data, message):
    with pytest.raises(TemplateError, match=message):
        read_outline(name, data)


# ------------------------------------------------------------------ applying
def built_in() -> DocumentContent:
    return DocumentContent(title="T", language="en", meta=[("Template version", "1.0"), ("Course", "X")], sections=[
        Section(heading="Welcome", blocks=[para("welcome")]),
        Section(heading="Course at a glance", blocks=[para("glance")]),     # also a "summary": loses the tie
        Section(heading="What you will achieve", blocks=[para("outcomes")]),
        Section(heading="Module 1: Basics (10 h)", blocks=[para("m1")]),
        Section(heading="Module 2: Advanced (12 h)", blocks=[para("m2")]),
        Section(heading="Your capstone project", blocks=[para("capstone")]),
        Section(heading="How you will be assessed", blocks=[para("assessment")]),
        Section(heading="Tips for success", blocks=[para("tips")])])


def test_apply_outline_follows_academy_order_and_keeps_everything():
    doc, notices = apply_outline(built_in(), ACADEMY_HEADINGS, "Academy guide (TPL-X)")
    headings = [s.heading for s in doc.sections]
    assert headings == ["Introduction", "Learning Objectives", "Course Content", "Module 1: Basics (10 h)",
                        "Module 2: Advanced (12 h)", "Assessment", "Final Project", "Code of Conduct",
                        "Course at a glance", "Tips for success"]
    by = {s.heading: s for s in doc.sections}
    assert by["Introduction"].blocks[0].text == "welcome" and by["Final Project"].blocks[0].text == "capstone"
    assert by["Course Content"].blocks[0].text == "2 parts, detailed below."             # modules moved as a group
    assert by["Code of Conduct"].blocks[0].kind == "note" and "To be completed" in by["Code of Conduct"].blocks[0].text
    assert ("Template version", "Academy guide (TPL-X)") in doc.meta
    assert any("Code of Conduct" in n for n in notices) and any("Tips for success" in n for n in notices)


def test_template_text_is_never_copied():
    doc, _ = apply_outline(built_in(), read_outline("guide.docx", ACADEMY_DOCX), "TPL")
    assert "must NOT be copied" not in str(doc.model_dump())


def test_arabic_matching():
    assert similarity("التقييم", "كيف سيتم تقييمك") == 1.0
    assert similarity("أهداف البرنامج", "ما الذي ستحققه") == 1.0
    assert similarity("Code of Conduct", "Tips for success") < 0.34


# ------------------------------------------------------------------ workflow (service + database)
@pytest.fixture
def service(test_env):
    from src.bootstrap import build_app
    from src.config import load_settings
    from tests.conftest import OFFICIAL_DATASET
    return build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))), enable_rag=False).document_service


def generate(service, **kw):
    return service.generate(DocumentRequest(doc_type=DocType.PARTICIPANT_GUIDE, course_id="TRK-001", **kw), use_llm=False)


def test_pending_template_is_not_used_until_approved(service):
    tpl = service.upload_template(DocType.PARTICIPANT_GUIDE, "en", "Academy guide 2026", "guide.docx", ACADEMY_DOCX, "Mona")
    assert tpl.status == "pending" and tpl.headings == ACADEMY_HEADINGS
    assert generate(service).template_version == "1.0"                                   # built-in
    with pytest.raises(DocumentError, match="reviewer"):
        service.review_template(tpl.id, True, " ")
    service.review_template(tpl.id, True, "Dr. Reviewer")
    doc = generate(service)
    assert doc.template_version == f"academy {tpl.id}"
    assert [s.heading for s in doc.content.sections][:3] == ["Introduction", "Learning Objectives", "Course Content"]
    assert any("approved template" in n for n in doc.notices)
    assert service.pdf(doc.id)[:4] == b"%PDF"                                              # still renders


def test_opt_out_language_and_type_are_respected(service):
    tpl = service.upload_template(DocType.PARTICIPANT_GUIDE, "en", "Academy guide", "g.docx", ACADEMY_DOCX, "Mona")
    service.review_template(tpl.id, True, "Rev")
    assert generate(service, use_academy_template=False).template_version == "1.0"
    assert generate(service, language="ar").template_version == "1.0"                     # English template only
    other = service.generate(DocumentRequest(doc_type=DocType.COURSE_PROPOSAL, course_id="TRK-001"), use_llm=False)
    assert other.template_version == "1.0"


def test_duplicate_upload_and_replacement(service):
    first = service.upload_template(DocType.PARTICIPANT_GUIDE, "en", "v1", "g.docx", ACADEMY_DOCX, "Mona")
    with pytest.raises(DocumentError, match="already uploaded"):
        service.upload_template(DocType.PARTICIPANT_GUIDE, "en", "again", "g2.docx", ACADEMY_DOCX, "Mona")
    second = service.upload_template(DocType.PARTICIPANT_GUIDE, "en", "v2", "g.md", b"# Overview\n## Modules\n", "Mona")
    service.review_template(first.id, True, "Rev")
    service.review_template(second.id, True, "Rev")
    statuses = {t.id: t.status for t in service.list_templates()}
    assert statuses == {first.id: "retired", second.id: "approved"}
    assert generate(service).template_version == f"academy {second.id}"
    service.review_template(second.id, False, "Rev")                                     # rejecting stops its use
    assert generate(service).template_version == "1.0"
    service.delete_template(first.id)
    assert {t.id for t in service.list_templates()} == {second.id}


def test_arabic_template_for_arabic_documents(service):
    ar_docx = make_docx([("Heading1", "مقدمة"), ("Heading1", "أهداف البرنامج"), ("Heading1", "محتوى البرنامج"),
                         ("Heading1", "التقييم"), ("Heading1", "قواعد السلوك")])
    tpl = service.upload_template(DocType.PARTICIPANT_GUIDE, "ar", "دليل المشارك", "ar.docx", ar_docx, "Mona")
    service.review_template(tpl.id, True, "Rev")
    doc = generate(service, language="ar")
    headings = [s.heading for s in doc.content.sections]
    assert headings[:3] == ["مقدمة", "أهداف البرنامج", "محتوى البرنامج"] and headings[3].startswith("الوحدة 1")
    assert service.pdf(doc.id)[:4] == b"%PDF"


def test_upload_validation(service):
    with pytest.raises(DocumentError, match="name"):
        service.upload_template(DocType.QUESTION_BANK, "en", " ", "g.docx", ACADEMY_DOCX, "Mona")
    with pytest.raises(DocumentError, match="No section headings"):
        service.upload_template(DocType.QUESTION_BANK, "en", "x", "g.md", b"just text", "Mona")
    with pytest.raises(DocumentError, match="No template"):
        service.review_template("TPL-000000", True, "Rev")


def test_documents_page_has_templates_tab(monkeypatch, test_env):
    from streamlit.testing.v1 import AppTest
    from src.config import PROJECT_ROOT
    from src.ui_context import get_context
    from tests.conftest import OFFICIAL_DATASET
    for k, v in test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET)).items():
        monkeypatch.setenv(k, v)
    get_context.clear()
    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "6_Documents.py"), default_timeout=90)
    at.run()
    assert not at.exception
    assert any("Academy templates" in t.label for t in at.tabs)
    assert any("built-in template is used" in c.value for c in at.caption)
    get_context.clear()
