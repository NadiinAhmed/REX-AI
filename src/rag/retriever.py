"""Passage retrieval over APPROVED documents, with source metadata and diagnostics."""
from __future__ import annotations

import time
from collections.abc import Callable

from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, Field

from src.db.repositories import DocumentRepository
from src.knowledge_base.text_utils import tokenize
from src.rag.vector_store import ChromaVectorStore
from src.utils.logging_config import get_logger

logger = get_logger("rag.retriever")


class Passage(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    page: int
    text: str
    score: float

    @property
    def citation(self) -> str:
        return f"{self.filename}, p.{self.page}"


class RetrievalDiagnostics(BaseModel):
    query: str
    candidates: int = 0                 # hits returned by the vector store
    kept: int = 0                       # after score threshold + de-duplication
    min_score: float
    top_score: float | None = None
    searched_documents: int = 0
    latency_ms: float = 0.0
    dropped_below_threshold: int = 0
    dropped_duplicates: int = 0
    dropped_no_term_overlap: int = 0
    key_terms: list[str] = Field(default_factory=list)
    evidence_terms: list[str] = Field(default_factory=list)   # key terms minus course-name words
    dropped_no_evidence: int = 0        # whole result dropped: no passage had an evidence term


class RetrievalResult(BaseModel):
    passages: list[Passage] = Field(default_factory=list)
    diagnostics: RetrievalDiagnostics

    @property
    def found(self) -> bool:
        return bool(self.passages)


# Words that appear all over academy documents, so they prove nothing about relevance.
GENERIC_TERMS = {"student", "students", "program", "programs", "programme", "course", "courses", "track",
                 "tracks", "academy", "training", "university", "final", "year", "andalusia", "does",
                 "about", "there", "offer", "offers", "have"}


def key_terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if len(t) >= 4 and t not in GENERIC_TERMS}


def shares_key_term(query_terms: set[str], passage: str) -> bool:
    """True if the passage contains a query term (prefix match handles plural/-ing forms)."""
    passage_terms = set(tokenize(passage))
    for q in query_terms:
        # Keep all but the last 3 letters (at least 5): "certificate" -> "certific" matches
        # "certificates". The old fixed 5-letter stem let "international" match "interview".
        stem = q[:max(5, len(q) - 3)] if len(q) >= 6 else q
        if any(p == q or (len(p) >= 4 and p.startswith(stem)) for p in passage_terms):
            return True
    return False


class PassageRetriever:
    """Vector search over approved documents, plus an optional keyword gate (hybrid retrieval).

    With the offline hashing embedding, similarity reflects spelling, so an unrelated passage that
    shares common words can score as high as a relevant one (measured: 0.266 for an unanswerable
    "scholarships" question vs 0.269 for a correct "contact" match). `require_term_overlap`
    keeps only passages containing at least one distinctive word of the question. It is off for
    semantic models, which legitimately match paraphrases that share no words.

    `topic_terms` returns the words of course titles. They identify WHICH course a question is
    about, not WHAT it asks: "How much does the Flutter Development track cost?" matched the Flutter
    pages through "flutter" and "development" although no page states a cost. With the gate on, the
    result is kept only if at least one kept passage contains a question word that is not a
    course-name word (an "evidence term"); otherwise nothing is returned and REX says "not found".
    The check is on the whole result, not per passage, so answerable questions keep exactly the
    passages they had before (measured: no change in context precision/recall).
    """

    def __init__(self, documents: DocumentRepository, vectors: ChromaVectorStore, embeddings: Embeddings,
                 top_k: int = 4, min_score: float = 0.15, require_term_overlap: bool = False,
                 topic_terms: Callable[[], set[str]] | None = None) -> None:
        self._docs = documents
        self._vectors = vectors
        self._embeddings = embeddings
        self.top_k = top_k
        self.min_score = min_score
        self.require_term_overlap = require_term_overlap
        self._topic_terms = topic_terms

    def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        started = time.perf_counter()
        k = top_k or self.top_k
        approved = self._docs.approved_indexed_ids()
        diag = RetrievalDiagnostics(query=query, min_score=self.min_score, searched_documents=len(approved))
        if not approved or not query.strip():
            return RetrievalResult(diagnostics=diag)

        # Ask for extra candidates so filtering still leaves up to k passages.
        hits = self._vectors.query(self._embeddings.embed_query(query), top_k=k * 3, document_ids=approved)
        diag.candidates = len(hits)
        diag.top_score = hits[0].similarity if hits else None

        passages: list[Passage] = []
        seen_texts: set[str] = set()
        terms = key_terms(query)
        diag.key_terms = sorted(terms)
        topic = self._topic_terms() if self._topic_terms else set()
        evidence = (terms - topic) or terms        # only course names asked about -> they count
        diag.evidence_terms = sorted(evidence)
        chunk_texts = self._docs.chunk_texts([h.id for h in hits])
        for hit in hits:
            if hit.similarity < self.min_score:
                diag.dropped_below_threshold += 1
                continue
            text = chunk_texts.get(hit.id)
            if text is None:        # vector without a chunk row: stale, skip (see check_consistency)
                continue
            if self.require_term_overlap and terms and not shares_key_term(terms, text):
                diag.dropped_no_term_overlap += 1
                continue
            key = " ".join(text.lower().split())[:200]
            if key in seen_texts:
                diag.dropped_duplicates += 1
                continue
            seen_texts.add(key)
            passages.append(Passage(chunk_id=hit.id, document_id=hit.metadata["document_id"],
                                    filename=hit.metadata.get("filename", "?"), page=int(hit.metadata.get("page", 0)),
                                    text=text, score=hit.similarity))
            if len(passages) == k:
                break
        if (self.require_term_overlap and passages and evidence != terms
                and not any(shares_key_term(evidence, p.text) for p in passages)):
            diag.dropped_no_evidence = len(passages)    # only course names matched: abstain
            passages = []
        diag.kept = len(passages)
        diag.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.info("retrieve kept=%d/%d top=%s", diag.kept, diag.candidates, diag.top_score)
        return RetrievalResult(passages=passages, diagnostics=diag)
