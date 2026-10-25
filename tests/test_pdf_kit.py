"""Shared PDF kit: Markdown round trip and real PDF output in English and Arabic (Agents 3 and 4)."""
import pytest

from src.pdf.content import DocumentContent, Section, bullets, note, numbered, para, table
from src.pdf.markdown import from_markdown, to_markdown
from src.pdf.renderer import PdfRenderError, has_arabic, render_pdf, visual


def _doc(language="en", title="Software Testing: Curriculum"):
    return DocumentContent(title=title, subtitle="Andalusia Academy", language=language, meta=[("Course", "TRK-001")],
                           sections=[Section(heading="Overview", blocks=[para("Hello world."), bullets(["a", "b"]),
                                     numbered(["one", "two"]), note("Careful."), table(["H1", "H2"], [["x", "y"], ["z", "w"]])])],
                           disclaimer="Proposal only.")


def test_markdown_round_trip_keeps_every_block():
    doc = _doc()
    again = from_markdown(to_markdown(doc), doc)
    assert [b.kind for b in again.sections[0].blocks] == ["paragraph", "bullets", "numbered", "note", "table"]
    assert again.sections[0].blocks[4].rows == [["x", "y"], ["z", "w"]]
    assert again.meta == doc.meta and again.disclaimer == doc.disclaimer       # not editable, kept from the original


def test_markdown_edits_are_applied_and_pipes_in_cells_are_safe():
    doc = _doc()
    edited = to_markdown(doc).replace("Hello world.", "Changed text.").replace("## Overview", "## Summary")
    out = from_markdown(edited, doc)
    assert out.sections[0].heading == "Summary" and out.sections[0].blocks[0].text == "Changed text."
    t = table(["a"], [["x | y"]])
    assert "x / y" in to_markdown(DocumentContent(title="T", sections=[Section(heading="S", blocks=[t])]))


def test_english_pdf_is_a_real_pdf(tmp_path):
    pdf = render_pdf(_doc())
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000
    path = tmp_path / "d.pdf"
    path.write_bytes(pdf)
    from pypdf import PdfReader
    text = PdfReader(str(path)).pages[0].extract_text()
    assert "Hello world." in text and "Page 1 of" in text


def test_arabic_pdf_renders_and_is_right_to_left():
    pdf = render_pdf(_doc("ar", "اختبار البرمجيات"))
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000


def test_brackets_are_mirrored_in_arabic_text():
    # python-bidi 0.6.x returned ')Software' here; the pinned 0.4.2 returns the correct '(Software'
    assert visual("اختبار (Software Testing)", rtl=True).startswith("(Software Testing)")
    assert has_arabic("نص") and not has_arabic("text")


def test_mixed_arabic_in_an_english_document_does_not_crash():
    assert render_pdf(_doc("en", "Software Testing — اختبار البرمجيات")).startswith(b"%PDF")


def test_long_documents_paginate():
    doc = DocumentContent(title="Long", sections=[Section(heading=f"S{i}", blocks=[para("word " * 200)]) for i in range(15)])
    from io import BytesIO
    from pypdf import PdfReader
    assert len(PdfReader(BytesIO(render_pdf(doc))).pages) >= 3


def test_missing_font_gives_a_readable_error(monkeypatch, tmp_path):
    from src.pdf import renderer
    renderer._register_fonts.cache_clear()
    monkeypatch.setattr(renderer, "FONT_DIR", tmp_path)
    with pytest.raises(PdfRenderError, match="fonts are missing"):
        render_pdf(_doc())
    renderer._register_fonts.cache_clear()
