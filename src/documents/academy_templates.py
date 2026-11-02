"""Academy-approved templates (brief §10: "Is there an approved template to follow?" and §11 reference documents).

The academy uploads one of its own documents (e.g. its real participant guide) as the template for a document
type and language. Two steps:

1. `read_outline()` reads the uploaded file and keeps only its STRUCTURE: the section headings, in order.
   Word (.docx) headings come from the heading styles; Markdown from `#` lines; PDF / text from lines that look
   like headings (short, numbered, Title Case or ALL CAPS). The template's own text is never copied into
   generated documents.
2. `apply_outline()` re-arranges a generated document to follow that structure: every academy heading becomes a
   section, filled with the generated section that matches it best. A heading with no matching content gets a
   visible "to be completed" note (never invented text); generated sections the template has no place for are
   kept at the end, so no information is silently lost. The reviewer can move or delete them in Edit.

A template is only used after a named person approves it (same rule as everything else in REX AI).
"""
from __future__ import annotations

import io
import re
import zipfile
from difflib import SequenceMatcher
from xml.etree import ElementTree

from src.knowledge_base.text_utils import normalize_arabic
from src.pdf.content import DocumentContent, Section, note, para

MIN_HEADINGS = 2
MAX_HEADINGS = 40
MAX_HEADING_CHARS = 120
MATCH_THRESHOLD = 0.34
SUPPORTED_EXTENSIONS = (".docx", ".pdf", ".md", ".txt")


class TemplateError(ValueError):
    """The uploaded file cannot be used as a template (unreadable, or no usable headings)."""


# ====================================================================== 1. reading the outline
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NUMBERING = re.compile(r"^\s*(?:(?:section|part|chapter|unit)\s+)?(?:\d+(?:\.\d+)*|[ivxlc]+|[a-h])[.):\-]?\s+", re.IGNORECASE)


def _clean(heading: str) -> str:
    text = re.sub(r"\s+", " ", heading).strip().strip(":·-–— ")
    text = _NUMBERING.sub("", text).strip()
    return text[:MAX_HEADING_CHARS]


def _docx_headings(data: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise TemplateError(f"The Word file could not be read ({type(exc).__name__}).") from exc
    headings = []
    for p in root.iter(f"{_W}p"):
        style = p.find(f"{_W}pPr/{_W}pStyle")
        name = (style.get(f"{_W}val") or "") if style is not None else ""
        # "Heading1", "Heading 2", localized "Titre1"/"عنوان1" all end with a level digit; level 1–2 only
        level = re.search(r"(\d)$", name)
        if not (name.lower().startswith(("heading", "titre", "überschrift", "عنوان")) and level and level.group(1) in "12"):
            continue
        text = "".join(t.text or "" for t in p.iter(f"{_W}t"))
        if text.strip():
            headings.append(text)
    return headings


def _markdown_headings(text: str) -> list[str]:
    return [m.group(2) for m in re.finditer(r"^(#{1,3})\s+(.+?)\s*#*\s*$", text, re.MULTILINE) if len(m.group(1)) >= 1]


def _looks_like_heading(line: str) -> bool:
    words = line.split()
    if not (1 <= len(words) <= 10) or len(line) > MAX_HEADING_CHARS or len(line) < 3:
        return False
    if line.endswith((".", ",", ";")) or sum(ch.isdigit() for ch in line) > len(line) / 3:
        return False
    if re.match(r"^(page|صفحة)\s*\d+", line, re.IGNORECASE):
        return False
    if _NUMBERING.match(line):
        return True
    letters = [ch for ch in line if ch.isalpha()]
    if letters and all(not ch.isascii() for ch in letters):        # Arabic: short line without final punctuation
        return len(words) <= 6
    capitalised = [w for w in words if w[:1].isupper() or not w[:1].isalpha()]
    return line.isupper() or len(capitalised) >= max(1, len(words) - 1)


def _plain_headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _looks_like_heading(line.strip())]


def _pdf_text(data: bytes, max_pages: int) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise TemplateError("The PDF is password-protected.")
        pages = reader.pages[:max_pages]
        return "\n".join(page.extract_text() or "" for page in pages)
    except TemplateError:
        raise
    except (PdfReadError, ValueError, OSError, KeyError) as exc:
        raise TemplateError(f"The PDF could not be read ({type(exc).__name__}).") from exc


def read_outline(filename: str, data: bytes, max_pages: int = 60) -> list[str]:
    """The template's section headings, in order, numbering removed, duplicates dropped."""
    name = (filename or "").lower()
    if not name.endswith(SUPPORTED_EXTENSIONS):
        raise TemplateError("Upload a Word (.docx), PDF, Markdown (.md) or text (.txt) file.")
    if name.endswith(".docx"):
        raw = _docx_headings(data)
    elif name.endswith(".pdf"):
        raw = _plain_headings(_pdf_text(data, max_pages))
    else:
        text = data.decode("utf-8", errors="replace")
        raw = _markdown_headings(text) if name.endswith(".md") else _plain_headings(text)
    headings: list[str] = []
    for h in raw:
        cleaned = _clean(h)
        if cleaned and cleaned.lower() not in {x.lower() for x in headings}:
            headings.append(cleaned)
    if len(headings) < MIN_HEADINGS:
        raise TemplateError("No section headings were found. In Word, use the Heading 1 / Heading 2 styles; "
                            "in Markdown, start headings with '#'.")
    return headings[:MAX_HEADINGS]


# ====================================================================== 2. applying the outline
# Words that mean the same thing in section headings (English; Arabic is matched on its own words).
_SYNONYMS = {
    "overview": "summary", "introduction": "summary", "description": "summary", "about": "summary", "executive": "summary",
    "objectives": "outcomes", "goals": "outcomes", "aims": "outcomes", "learning": "outcomes", "competencies": "outcomes",
    "evaluation": "assessment", "grading": "assessment", "marking": "assessment", "assessments": "assessment", "exam": "assessment",
    "requirements": "prerequisites", "entry": "prerequisites", "eligibility": "prerequisites",
    "content": "modules", "syllabus": "modules", "topics": "modules", "outline": "modules", "units": "modules", "sessions": "modules",
    "timeline": "schedule", "timetable": "schedule", "plan": "schedule", "calendar": "schedule",
    "participants": "audience", "learners": "audience", "trainees": "audience",
    "trainer": "instructor", "facilitator": "instructor", "teacher": "instructor",
    "project": "capstone", "final": "capstone", "fees": "fee", "price": "fee", "cost": "fee", "tools": "resources",
    "materials": "resources", "careers": "career", "jobs": "career", "rubric": "rubrics", "criteria": "rubrics",
    "module": "modules", "unit": "modules", "session": "modules", "lesson": "modules", "lessons": "modules",
    "structure": "modules", "welcome": "summary", "glance": "summary", "achieve": "outcomes", "assessed": "assessment",
    "start": "prerequisites", "started": "prerequisites", "ستحقق": "outcomes",
    # Arabic (after normalisation and removing the article "ال")
    "وحده": "modules", "وحدات": "modules", "محتوي": "modules", "دروس": "modules", "مواضيع": "modules", "هيكل": "modules",
    "اهداف": "outcomes", "مخرجات": "outcomes", "تقييم": "assessment", "مشروع": "capstone", "تخرج": "capstone",
    "مقدمه": "summary", "ملخص": "summary", "لمحه": "summary", "مرحبا": "summary", "متطلبات": "prerequisites",
}
_STOP = {"the", "and", "of", "for", "a", "an", "to", "in", "on", "with", "your", "our", "this", "course", "program", "programme",
         "what", "you", "will", "how", "be", "is", "are", "we", "at", "by", "from", "before", "after", "about",
         "ما", "الذي", "التي", "كيف", "سيتم", "قبل", "بعد", "ان", "في", "من", "علي", "عن", "مع", "برنامج", "المقرر", "مقرر"}
_AR_KEYS = [k for k in _SYNONYMS if not k.isascii()]


def _canonical(word: str) -> str:
    if word in _SYNONYMS:
        return _SYNONYMS[word]
    if not word.isascii():                       # Arabic: "تقييمك" (your assessment) → "تقييم" → assessment
        for key in _AR_KEYS:
            if len(key) >= 3 and word.startswith(key) and len(word) - len(key) <= 2:
                return _SYNONYMS[key]
    return word


def _tokens(heading: str) -> set[str]:
    text = normalize_arabic(heading.lower())
    words = re.findall(r"[a-z]+|[؀-ۿ]+", text)
    out = set()
    for w in words:
        if w in _STOP or len(w) < 2:
            continue
        w = w[2:] if w.startswith("ال") and len(w) > 4 else w             # Arabic definite article
        if w in _STOP:
            continue
        out.add(_canonical(w))
    return out


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    overlap = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(overlap, SequenceMatcher(None, normalize_arabic(a.lower()), normalize_arabic(b.lower())).ratio() * 0.8)


_SERIES = re.compile(r"^\s*(\D{2,30}?)\s*\d+\s*[:：.\-]")


def _units(sections: list[Section]) -> list[tuple[str, list[int]]]:
    """Group a numbered series ("Module 1: …", "Module 2: …" / "الوحدة 1: …") into ONE unit labelled by its prefix,
    so all modules move together under the matching academy heading. Other sections are units of one."""
    prefixes: dict[str, list[int]] = {}
    for i, s in enumerate(sections):
        m = _SERIES.match(s.heading)
        if m:
            prefixes.setdefault(m.group(1).strip().lower(), []).append(i)
    series = {i: p for p, members in prefixes.items() if len(members) >= 2 for i in members}
    units: list[tuple[str, list[int]]] = []
    seen: set[str] = set()
    for i, s in enumerate(sections):
        if i not in series:
            units.append((s.heading, [i]))
        elif series[i] not in seen:
            seen.add(series[i])
            units.append((series[i], prefixes[series[i]]))
    return units


def apply_outline(doc: DocumentContent, headings: list[str], template_label: str) -> tuple[DocumentContent, list[str]]:
    """Re-arrange `doc` under the academy's headings. Returns the new document and reviewer notices."""
    ar = doc.language == "ar"
    units = _units(doc.sections)
    # best score first; on a tie, the earlier academy heading and the earlier generated section win
    pairs = sorted(((similarity(h, label), hi, ui) for hi, h in enumerate(headings)
                    for ui, (label, _) in enumerate(units)), key=lambda p: (-p[0], p[1], p[2]))
    heading_to_unit: dict[int, int] = {}
    used: set[int] = set()
    for score, hi, ui in pairs:                              # best matches first; each used once
        if score < MATCH_THRESHOLD:
            break
        if hi not in heading_to_unit and ui not in used:
            heading_to_unit[hi] = ui
            used.add(ui)

    todo = ("لا توجد بيانات معتمدة لهذا القسم في بيانات الأكاديمية. يُكمله المدرب أو المراجع." if ar else
            "No approved academy data for this section yet. To be completed by the instructor or reviewer.")
    sections: list[Section] = []
    empty: list[str] = []
    for hi, heading in enumerate(headings):
        if hi not in heading_to_unit:
            sections.append(Section(heading=heading, blocks=[note(todo)]))
            empty.append(heading)
            continue
        members = [doc.sections[i] for i in units[heading_to_unit[hi]][1]]
        if len(members) == 1:
            sections.append(Section(heading=heading, blocks=members[0].blocks))
        else:                                                 # a series: academy heading, then each member in order
            intro = (f"{len(members)} أقسام، بالتفصيل أدناه." if ar else f"{len(members)} parts, detailed below.")
            sections.append(Section(heading=heading, blocks=[para(intro)]))
            sections.extend(members)
    extra = [doc.sections[i] for ui, (_, members) in enumerate(units) if ui not in used for i in members]
    sections.extend(extra)

    meta = [(k, template_label) if k in ("Template version", "إصدار القالب") else (k, v) for k, v in doc.meta]
    notices = [f"Structured with the academy's approved template: {template_label}."]
    if empty:
        notices.append(f"{len(empty)} section(s) of the academy template have no matching data and are marked "
                       f"'to be completed': {', '.join(empty[:8])}{'…' if len(empty) > 8 else ''}.")
    if extra:
        notices.append(f"{len(extra)} generated section(s) have no place in the academy template and were kept at the end "
                       f"so no information is lost: {', '.join(s.heading for s in extra[:8])}. Move or delete them in Edit.")
    return doc.model_copy(update={"sections": sections, "meta": meta}), notices
