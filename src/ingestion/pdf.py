"""PDF validation and text extraction.

Uploaded files are untrusted input: they are validated before parsing, and their text is only
ever treated as data (never as instructions to the assistant).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path


class DocumentError(Exception):
    """Base class for document problems the user can fix (bad file, empty file, ...)."""


class InvalidDocumentError(DocumentError):
    pass


class EmptyDocumentError(DocumentError):
    pass


@dataclass(frozen=True)
class PageText:
    page: int          # 1-based
    text: str


@dataclass
class ExtractedDocument:
    pages: list[PageText]
    page_count: int
    warnings: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


class PdfValidator:
    """Cheap checks before any parsing: extension, size, magic bytes."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes

    def validate(self, path: Path, filename: str) -> None:
        if not filename.lower().endswith(".pdf"):
            raise InvalidDocumentError(f"'{filename}' is not a PDF (only .pdf files are accepted).")
        if not path.exists():
            raise InvalidDocumentError(f"File not found: {filename}")
        size = path.stat().st_size
        if size == 0:
            raise InvalidDocumentError(f"'{filename}' is empty (0 bytes).")
        if size > self._max_bytes:
            raise InvalidDocumentError(
                f"'{filename}' is {size / 1e6:.1f} MB; the limit is {self._max_bytes / 1e6:.0f} MB.")
        with path.open("rb") as fh:
            if not fh.read(1024).lstrip().startswith(b"%PDF-"):
                raise InvalidDocumentError(f"'{filename}' does not look like a valid PDF file.")


# "C U R R I C U L U M" -> "CURRICULUM": letter-spaced headings are common in designed PDFs.
_SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z0-9] ){3,}[A-Za-z0-9]\b")
_MULTI_SPACE = re.compile(r"[ \t\u00a0]+")


def clean_page_text(text: str) -> str:
    """Normalise whitespace, join letter-spaced headings, drop lines repeated on the same page."""
    text = text.replace("\r", "\n")
    text = _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), text)
    seen: set[str] = set()
    lines: list[str] = []
    for raw in text.split("\n"):
        line = _MULTI_SPACE.sub(" ", raw).strip()
        if not line:
            continue
        key = line.lower()
        # Some PDFs repeat a whole block in their text layer (page 8 of the academy portfolio
        # does). Long lines seen before on the same page are duplicates, not content.
        if len(line) > 25 and key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def _boilerplate_key(line: str) -> str:
    # Compare without spaces/digits so "ACA DEMIC P RO GRAMS" == "ACADEMIC PROGRAMS" and
    # "Page 5" == "Page 6".
    return re.sub(r"[\s\d]+", "", line.lower())


def remove_running_headers(pages: list[PageText], min_share: float = 0.4) -> list[PageText]:
    """Drop lines that repeat on many pages (running headers/footers such as the academy name).

    A line is boilerplate when it appears on at least `min_share` of the pages (and on 3+
    pages). It carries no information for retrieval and dilutes every chunk's embedding.
    """
    if len(pages) < 3:
        return pages
    counts: dict[str, int] = {}
    for page in pages:
        for key in {_boilerplate_key(l) for l in page.text.split("\n")}:
            counts[key] = counts.get(key, 0) + 1
    threshold = max(3, int(len(pages) * min_share))
    boiler = {k for k, n in counts.items() if n >= threshold and k}
    cleaned = []
    for page in pages:
        kept = [l for l in page.text.split("\n") if _boilerplate_key(l) not in boiler]
        if kept:
            cleaned.append(PageText(page=page.page, text="\n".join(kept)))
    return cleaned


class PdfTextExtractor:
    def __init__(self, max_pages: int) -> None:
        self._max_pages = max_pages

    def extract(self, path: Path, filename: str) -> ExtractedDocument:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted and not reader.decrypt(""):
                raise InvalidDocumentError(f"'{filename}' is password-protected.")
            page_count = len(reader.pages)
        except InvalidDocumentError:
            raise
        except (PdfReadError, ValueError, OSError, KeyError) as exc:
            raise InvalidDocumentError(f"'{filename}' could not be read as a PDF ({type(exc).__name__}).") from exc
        if page_count == 0:
            raise EmptyDocumentError(f"'{filename}' has no pages.")
        if page_count > self._max_pages:
            raise InvalidDocumentError(f"'{filename}' has {page_count} pages; the limit is {self._max_pages}.")

        pages: list[PageText] = []
        warnings: list[str] = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = clean_page_text(page.extract_text() or "")
            except Exception as exc:  # one broken page must not lose the whole document
                warnings.append(f"page {number}: text extraction failed ({type(exc).__name__})")
                continue
            if text:
                pages.append(PageText(page=number, text=text))
            else:
                warnings.append(f"page {number}: no text (image-only page?)")

        pages = remove_running_headers(pages)
        doc = ExtractedDocument(pages=pages, page_count=page_count, warnings=warnings)
        if doc.char_count < 20:
            raise EmptyDocumentError(
                f"'{filename}' contains no extractable text. It may be a scanned image; "
                "OCR is not supported in this MVP.")
        return doc
