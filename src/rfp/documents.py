"""Reading the real tender notice document.

Real notices (e.g. from the World Bank API) arrive as an HTML document of several pages of text,
not as neat fields. This module turns that document into plain text and pulls out the parts the
agent needs when the source did not provide them as fields:

- `html_to_text()`          HTML notice → clean plain text
- `requirement_sentences()` the sentences that state eligibility / qualification requirements
- `deadline_from_text()`    the submission deadline written inside the notice

Everything is deterministic and works on English and Arabic text. Nothing is guessed: if no
requirement or deadline sentence is found, the result is empty / None.
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

from src.knowledge_base.text_utils import normalize_arabic
from src.rfp.extraction import parse_date

MAX_TEXT_CHARS = 30_000          # a notice longer than this is cut (very long annexes add nothing)
MAX_REQUIREMENT_SENTENCES = 4

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str | None) -> str:
    """HTML (or plain text) → plain text with one paragraph per line."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
        text = "".join(parser.parts)
    except Exception:                       # badly broken HTML: fall back to stripping tags
        text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_TEXT_CHARS]


def _sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?؟])\s+|\n+", text)
    return [p.strip() for p in pieces if len(p.strip()) > 20]


# Words that mark a requirement sentence (English + Arabic, after Arabic normalisation).
_REQUIREMENT_WORDS = re.compile(
    r"\b(eligib\w*|qualif\w*|experience|registered|registration|licen[cs]e\w*|certif\w*|"
    r"must (have|be|provide|demonstrate)|shall (have|be|provide|demonstrate)|minimum|at least|"
    r"track record|similar (assignments|projects|contracts)|key experts?|shortlist\w*)\b"
    r"|شروط|التاهل|خبره|مسجل|ترخيص|معتمد|سابقه اعمال",
    re.IGNORECASE)


def requirement_sentences(text: str) -> str | None:
    """The first few sentences of the notice that state requirements, joined; None if none found."""
    found: list[str] = []
    for sentence in _sentences(text):
        if _REQUIREMENT_WORDS.search(normalize_arabic(sentence.lower())):
            cleaned = sentence if len(sentence) <= 400 else sentence[:397].rstrip() + "..."
            if cleaned not in found:
                found.append(cleaned)
        if len(found) == MAX_REQUIREMENT_SENTENCES:
            break
    return " ".join(found) or None


_DEADLINE_WORDS = re.compile(
    r"deadline|closing date|must be (delivered|submitted|received)|no later than|on or before|"
    r"submitted (by|before)|اخر موعد|موعد (تقديم|استلام)|الموعد النهائي", re.IGNORECASE)
_DATE_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",                                            # 2026-11-20
    r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\b",                              # 20/11/2026
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4}\b",            # 20 November 2026
    r"\b[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",            # November 20, 2026
]


def deadline_from_text(text: str):
    """The date written in the notice's deadline sentence, or None. Only looks at deadline sentences."""
    for sentence in _sentences(text):
        if not _DEADLINE_WORDS.search(normalize_arabic(sentence.lower())):
            continue
        for pattern in _DATE_PATTERNS:
            for match in re.finditer(pattern, sentence):
                found = parse_date(match.group(0))
                if found:
                    return found
    return None
