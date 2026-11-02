"""Semantic course retrieval (COURSE_RETRIEVER=semantic).

Implements the same `CourseRetriever` contract as `KeywordRetriever`, so REX Assistant, Marketing
and RFP use it without code changes.

How it works (hybrid retrieval):
  1. At start-up every approved course is turned into ONE text ("course card": title, Arabic title,
     description, keywords, career paths, modules, tools, ...) and embedded once.
  2. For a query, the query is embedded and compared with every course card (cosine similarity).
     The vectors are L2-normalised, so cosine similarity is a plain dot product.
  3. A course is kept if its similarity is >= `min_score` (meaning matches) OR the keyword retriever
     finds a strong exact-word match (the "safety net": a course whose title or keywords the user
     typed is never lost because of an embedding quirk).
  4. Results are ranked by similarity, plus a small bonus when the exact words also match.

Why no vector database here: the catalog has tens of courses, not millions of chunks. Comparing a
query with 5-50 in-memory vectors takes microseconds, needs no index to keep in sync, and is easy to
explain. The PDF chunks (thousands) still use Chroma (src/rag/vector_store.py).

`min_score` MUST be calibrated for the embedding model: multilingual E5 models give even unrelated
texts fairly high cosine similarity, so a value tuned for one model is wrong for another.
"""
from __future__ import annotations

from langchain_core.embeddings import Embeddings

from src.knowledge_base.retriever import CourseFilters, KeywordRetriever, RetrievalResult, passes_filters
from src.models.course import Course
from src.utils.logging_config import get_logger

logger = get_logger("knowledge_base.semantic")

# Same bar recommend_courses uses for keyword scores: a match on a strong field (title, keywords,
# career paths) or on several weaker ones.
STRONG_KEYWORD_SCORE = 2.0
# Added to the similarity when the exact words also match. Small on purpose: it breaks near-ties in
# favour of the course the user literally named, without overriding a clearly better meaning match.
KEYWORD_BONUS = 0.05


def course_card(course: Course) -> str:
    """The text that represents a course in embedding space. Facts only, no fees or dates:
    those are filters, not topics, and would only add noise to the meaning."""
    parts: list[str] = [course.title]
    if course.title_ar:
        parts.append(course.title_ar)
    for value in (course.department, course.industry, course.description, course.capstone):
        if value:
            parts.append(value)
    for label, values in (("Keywords", course.keywords), ("Career paths", course.career_paths),
                          ("Modules", course.modules), ("Tools", course.tools),
                          ("Learning outcomes", course.learning_outcomes),
                          ("Target audience", course.target_audience)):
        if values:
            parts.append(f"{label}: " + ", ".join(values))
    return "\n".join(parts)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class SemanticCourseRetriever:
    """Meaning-based course search with an exact-word safety net. See the module docstring."""

    # recommend_courses() reads these instead of its keyword-scale defaults: similarity scores live
    # in [-1, 1], so the keyword thresholds (2.0 absolute, 40% of best) do not apply to them.
    min_absolute_score = 0.0
    min_relative_score = 0.0
    # Tells recommend_courses() to send the learner's own sentence, not the keyword bag.
    uses_natural_language = True

    def __init__(self, courses: list[Course], embeddings: Embeddings, min_score: float,
                 keyword: KeywordRetriever | None = None) -> None:
        self._courses = list(courses)
        self._embeddings = embeddings
        self.min_score = min_score
        self._keyword = keyword or KeywordRetriever(self._courses)    # composition, not inheritance
        cards = [course_card(c) for c in self._courses]
        self._vectors: dict[str, list[float]] = (
            dict(zip((c.course_id for c in self._courses), embeddings.embed_documents(cards))) if cards else {})
        logger.info("Embedded %d course card(s)", len(self._vectors))

    # ---------------------------------------------------------------- CourseRetriever contract
    def all_courses(self) -> list[Course]:
        return self._keyword.all_courses()

    def get_by_id(self, course_id: str) -> Course | None:
        return self._keyword.get_by_id(course_id)

    def find_by_title(self, text: str) -> Course | None:
        return self._keyword.find_by_title(text)

    def search(self, query: str, top_k: int = 3, filters: CourseFilters | None = None,
               keyword_query: str | None = None) -> list[RetrievalResult]:
        """`query` is embedded (a full sentence works best). `keyword_query` is the exact-word view
        of the same request (defaults to `query`); it decides the safety net and `matched_terms`."""
        if not query.strip() or top_k <= 0 or not self._vectors:
            return []
        keyword_hits = {r.course.course_id: r for r in
                        self._keyword.search(keyword_query or query, top_k=len(self._courses), filters=filters)}
        q = self._embeddings.embed_query(query)

        results: list[RetrievalResult] = []
        for course in self._courses:
            if filters and not passes_filters(course, filters):
                continue
            similarity = _dot(q, self._vectors[course.course_id])
            kw = keyword_hits.get(course.course_id)
            strong_keyword = kw is not None and kw.score >= STRONG_KEYWORD_SCORE
            if similarity < self.min_score and not strong_keyword:
                continue
            score = similarity + (KEYWORD_BONUS if strong_keyword else 0.0)
            results.append(RetrievalResult(course=course, score=round(score, 4),
                                           matched_terms=kw.matched_terms if kw else []))
        results.sort(key=lambda r: (-r.score, r.course.course_id))
        logger.info("semantic search kept=%d top=%s", len(results), results[0].score if results else None)
        return results[:top_k]

    def similarities(self, query: str) -> dict[str, float]:
        """Raw similarity of `query` to every course, unfiltered. Used to calibrate `min_score`."""
        q = self._embeddings.embed_query(query)
        return {cid: round(_dot(q, v), 4) for cid, v in self._vectors.items()}
