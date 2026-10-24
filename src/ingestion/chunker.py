"""Split page text into overlapping chunks while keeping the page number of every chunk.

Uses LangChain's RecursiveCharacterTextSplitter: it tries to split on paragraphs, then lines,
then sentences, then words, so chunks rarely cut a sentence in half. Overlap repeats the end
of one chunk at the start of the next, so a fact that sits on a boundary is not lost.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.ingestion.pdf import PageText


@dataclass(frozen=True)
class TextChunk:
    page: int
    index: int        # position within the document (0-based)
    text: str
    text_hash: str


class TextChunker:
    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", "، ", " ", ""])

    def split(self, pages: list[PageText]) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        seen: set[str] = set()
        for page in pages:
            for piece in self._splitter.split_text(page.text):
                piece = piece.strip()
                digest = hashlib.sha256(" ".join(piece.lower().split()).encode("utf-8")).hexdigest()
                if len(piece) < 20 or digest in seen:   # skip fragments and exact duplicates
                    continue
                seen.add(digest)
                chunks.append(TextChunk(page=page.page, index=len(chunks), text=piece, text_hash=digest))
        return chunks
