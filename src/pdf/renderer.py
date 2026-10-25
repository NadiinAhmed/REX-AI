"""DocumentContent -> PDF bytes (reportlab), English and Arabic (right-to-left).

Arabic needs three things that reportlab does not do by itself:
  1. a font with Arabic glyphs  -> DejaVu Sans, shipped in assets/fonts (licence next to it);
  2. letter shaping             -> arabic-reshaper (letters change form inside a word);
  3. bidirectional ordering     -> python-bidi, applied PER LINE after we wrap the text ourselves,
     because wrapping a pre-reordered string would print the lines in the wrong order.
Mixed text (Arabic with English tool names) is handled by the same path.
"""
from __future__ import annotations

import io
import re
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.config import PROJECT_ROOT
from src.pdf.content import Block, DocumentContent

FONT_DIR = PROJECT_ROOT / "assets" / "fonts"
REGULAR, BOLD = "RexSans", "RexSans-Bold"
NAVY, TEAL, LIGHT, ZEBRA = colors.HexColor("#1F2A44"), colors.HexColor("#0E7C86"), colors.HexColor("#E8F3F4"), colors.HexColor("#F5F7FA")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
LABELS = {
    "en": {"page": "Page {n} of {total}", "facts": "Document details"},
    "ar": {"page": "صفحة {n} من {total}", "facts": "بيانات المستند"},
}


class PdfRenderError(Exception):
    """The PDF could not be produced (message is safe to show)."""


@lru_cache(maxsize=1)
def _register_fonts() -> None:
    regular, bold = FONT_DIR / "DejaVuSans.ttf", FONT_DIR / "DejaVuSans-Bold.ttf"
    if not regular.is_file() or not bold.is_file():
        raise PdfRenderError(f"PDF fonts are missing in {FONT_DIR}. Restore assets/fonts from the repository.")
    pdfmetrics.registerFont(TTFont(REGULAR, str(regular)))
    pdfmetrics.registerFont(TTFont(BOLD, str(bold)))
    pdfmetrics.registerFontFamily(REGULAR, normal=REGULAR, bold=BOLD, italic=REGULAR, boldItalic=BOLD)


@lru_cache(maxsize=1)
def _arabic_tools():
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError as exc:
        raise PdfRenderError("Arabic PDFs need 'arabic-reshaper' and 'python-bidi'. "
                             "Run: pip install arabic-reshaper python-bidi") from exc
    return arabic_reshaper.ArabicReshaper(configuration={"support_ligatures": True}), get_display


def has_arabic(text: str) -> bool:
    return bool(_ARABIC.search(text))


def visual(text: str, rtl: bool = False) -> str:
    """Shape + reorder ONE line for drawing (no wrapping)."""
    if not (rtl or has_arabic(text)):
        return text
    reshaper, get_display = _arabic_tools()
    return get_display(reshaper.reshape(text), base_dir="R" if rtl else None)


def markup(text: str, style: ParagraphStyle, width: float, rtl: bool) -> str:
    """Escaped Paragraph markup. Arabic/RTL text is wrapped here, line by line, then reordered."""
    text = text.replace("\r", "")
    if not (rtl or has_arabic(text)):
        return escape(text).replace("\n", "<br/>")
    reshaper, get_display = _arabic_tools()
    lines: list[str] = []
    for paragraph in text.split("\n"):
        shaped = reshaper.reshape(paragraph)
        for line in simpleSplit(shaped, style.fontName, style.fontSize, max(width, 20)) or [""]:
            lines.append(get_display(line, base_dir="R" if rtl else None))
    return "<br/>".join(escape(line) for line in lines)


class _NumberedCanvas(canvas.Canvas):
    """Two-pass canvas so the footer can say 'Page 2 of 5'."""

    footer_text = ""
    header_text = ""
    page_label = LABELS["en"]["page"]
    rtl = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        w, h = A4
        self.setFont(REGULAR, 8)
        self.setFillColor(colors.HexColor("#5B6475"))
        self.setStrokeColor(colors.HexColor("#C9D1DC"))
        self.line(20 * mm, 16 * mm, w - 20 * mm, 16 * mm)
        footer = visual(self.footer_text, self.rtl)
        page = visual(self.page_label.format(n=self._pageNumber, total=total), self.rtl)
        if self.rtl:
            self.drawRightString(w - 20 * mm, 11 * mm, footer)
            self.drawString(20 * mm, 11 * mm, page)
        else:
            self.drawString(20 * mm, 11 * mm, footer)
            self.drawRightString(w - 20 * mm, 11 * mm, page)
        if self._pageNumber > 1 and self.header_text:
            header = visual(self.header_text[:90], self.rtl)
            (self.drawRightString if self.rtl else self.drawString)((w - 20 * mm) if self.rtl else 20 * mm, h - 12 * mm, header)
            self.line(20 * mm, h - 14 * mm, w - 20 * mm, h - 14 * mm)


def render_pdf(doc: DocumentContent) -> bytes:
    """Render to PDF bytes. Raises PdfRenderError with a readable message on failure."""
    _register_fonts()
    rtl = doc.language == "ar"
    uses_arabic = rtl or has_arabic(doc.title + doc.subtitle) or any(
        has_arabic(s.heading + " ".join(b.text + " ".join(b.items) + " ".join(b.header) + " ".join(" ".join(r) for r in b.rows)
                                         for b in s.blocks)) for s in doc.sections)
    if uses_arabic:
        _arabic_tools()          # fail early with a clear message if the libraries are missing

    page_w = A4[0] - 40 * mm
    align = TA_RIGHT if rtl else TA_LEFT
    base = dict(fontName=REGULAR, fontSize=10, leading=14.5, alignment=align, textColor=colors.HexColor("#1B2330"))
    styles = {
        "title": ParagraphStyle("title", **{**base, "fontName": BOLD, "fontSize": 22, "leading": 28, "textColor": NAVY}),
        "subtitle": ParagraphStyle("subtitle", **{**base, "fontSize": 12, "leading": 17, "textColor": TEAL}),
        "h1": ParagraphStyle("h1", **{**base, "fontName": BOLD, "fontSize": 14, "leading": 19, "textColor": NAVY,
                                      "spaceBefore": 14, "spaceAfter": 5, "keepWithNext": 1}),
        "body": ParagraphStyle("body", **{**base, "spaceAfter": 5}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8.5, "leading": 11.5}),
        "head": ParagraphStyle("head", **{**base, "fontName": BOLD, "fontSize": 8.5, "leading": 11.5, "textColor": colors.white}),
        "note": ParagraphStyle("note", **{**base, "fontSize": 9, "leading": 13}),
        "disclaimer": ParagraphStyle("disclaimer", **{**base, "fontSize": 8, "leading": 11, "textColor": colors.HexColor("#5B6475")}),
    }

    def P(text: str, style: str, width: float = page_w, **extra) -> Paragraph:
        st = styles[style]
        if extra:
            st = ParagraphStyle(style + "x", parent=st, **extra)
        return Paragraph(markup(text, st, width * 0.985, rtl), st)

    story: list = [P(doc.title, "title"), Spacer(1, 3)]
    if doc.subtitle:
        story.append(P(doc.subtitle, "subtitle"))
    if doc.meta:
        story += [Spacer(1, 8), _table([], [[k, v] for k, v in doc.meta], styles, page_w, rtl, show_header=False,
                                       weights=[0.3, 0.7], bold_first=True)]

    for section in doc.sections:
        story.append(P(section.heading, "h1"))
        story.append(_rule(page_w))
        for block in section.blocks:
            story += _block(block, styles, page_w, rtl, P)
    if doc.disclaimer:
        story += [Spacer(1, 14), _rule(page_w), P(doc.disclaimer, "disclaimer")]

    buffer = io.BytesIO()

    class _Canvas(_NumberedCanvas):
        footer_text = doc.footer
        header_text = doc.title
        page_label = LABELS[doc.language]["page"]
    _Canvas.rtl = rtl
    template = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm,
                                 bottomMargin=22 * mm, title=doc.title[:200], author="REX AI")
    try:
        template.build(story, canvasmaker=_Canvas)
    except Exception as exc:        # reportlab raises many types; callers only need a safe message
        raise PdfRenderError(f"The PDF could not be built ({type(exc).__name__}).") from exc
    return buffer.getvalue()


def _rule(width: float):
    t = Table([[""]], colWidths=[width], rowHeights=[1.2])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1.2, TEAL)]))
    t.keepWithNext = True          # a heading's rule must stay with the content that follows
    return t


def _block(block: Block, styles: dict, width: float, rtl: bool, P) -> list:
    if block.kind == "paragraph":
        return [P(block.text, "body")]
    if block.kind == "note":
        inner = Paragraph(markup(block.text, styles["note"], (width - 16) * 0.985, rtl), styles["note"])
        t = Table([[inner]], colWidths=[width])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF6DD")),
                               ("LINEBEFORE" if not rtl else "LINEAFTER", (0, 0), (-1, -1), 3, colors.HexColor("#E0A800")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        return [t, Spacer(1, 6)]
    if block.kind in ("bullets", "numbered"):
        out = []
        for n, item in enumerate(block.items, 1):
            mark = "•" if block.kind == "bullets" else f"{n}."
            if rtl:
                out.append(P(f"{mark} {item}", "body", width - 14, spaceAfter=2))
            else:
                out.append(Paragraph(markup(item, styles["body"], (width - 16) * 0.985, False),
                                     ParagraphStyle("li", parent=styles["body"], leftIndent=16, bulletIndent=3, spaceAfter=2),
                                     bulletText=mark))
        out.append(Spacer(1, 4))
        return out
    if block.kind == "table":
        return [_table(block.header, block.rows, styles, width, rtl), Spacer(1, 8)]
    return []


def _table(head: list[str], rows: list[list[str]], styles: dict, width: float, rtl: bool, show_header: bool = True,
           weights: list[float] | None = None, bold_first: bool = False):
    ncols = len(head) if head else (len(rows[0]) if rows else 1)
    rows = [(list(r) + [""] * ncols)[:ncols] for r in rows]
    if weights is None:                                  # size columns by how much text they hold
        raw = []
        for c in range(ncols):
            lengths = [len(head[c]) if head else 0] + [min(len(r[c]), 60) for r in rows]
            longest_word = max((len(w) for r in rows for w in r[c].split()), default=0)
            longest_word = max(longest_word, max((len(w) for w in head[c].split()), default=0) if head else 0)
            raw.append(max(8.0, longest_word * 1.15 + 3, min(55.0, sum(lengths) / len(lengths) + 4)))
        weights = [w / sum(raw) for w in raw]
    total = sum(weights)
    col_w = [width * w / total for w in weights]

    def cell(text: str, style_name: str, w: float):
        st = styles[style_name]
        if bold_first and style_name == "cell" and w == col_w[0]:
            st = ParagraphStyle("cellb", parent=st, fontName=BOLD)
        return Paragraph(markup(str(text), st, (w - 10) * 0.985, rtl), st)

    data = []
    if show_header and head:
        data.append([cell(h, "head", col_w[i]) for i, h in enumerate(head)])
    data += [[cell(c, "cell", col_w[i]) for i, c in enumerate(r)] for r in rows]
    if not data:
        return Spacer(1, 1)
    if rtl:                                              # first column sits on the right in Arabic
        data = [list(reversed(r)) for r in data]
        col_w = list(reversed(col_w))
    t = Table(data, colWidths=col_w, repeatRows=1 if (show_header and head) else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D1DC")),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
    first_body = 1 if (show_header and head) else 0
    if first_body:
        style.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    for i in range(first_body, len(data)):
        if (i - first_body) % 2 == 1:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    if not first_body:
        style.append(("BACKGROUND", (0 if not rtl else len(col_w) - 1, 0), (0 if not rtl else len(col_w) - 1, -1), LIGHT))
    t.setStyle(TableStyle(style))
    return t
