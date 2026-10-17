"""Course retrieval. SHARED INTERFACE (owned by Role 2, consumed by Role 1).

`CourseRetriever` is the contract. `KeywordRetriever` is the Sprint 1 implementation
(weighted bilingual keyword matching). A future `EmbeddingRetriever` can implement the
same protocol without changing the assistant.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from pydantic import BaseModel, Field

from src.knowledge_base.text_utils import normalize_arabic, tokenize
from src.models.course import Course, DeliveryFormat, SkillLevel


class CourseFilters(BaseModel):
    """Hard filters. A course with an UNKNOWN value is kept (we cannot rule it out)."""
    skill_level: SkillLevel | None = None
    delivery_format: DeliveryFormat | None = None
    max_fee: float | None = Field(default=None, ge=0)
    currency: str | None = None


class RetrievalResult(BaseModel):
    course: Course
    score: float
    matched_terms: list[str]


class CourseRetriever(Protocol):
    def search(
        self, query: str, top_k: int = 3, filters: CourseFilters | None = None
    ) -> list[RetrievalResult]: ...

    def get_by_id(self, course_id: str) -> Course | None: ...

    def find_by_title(self, text: str) -> Course | None: ...

    def all_courses(self) -> list[Course]: ...


FIELD_WEIGHTS: dict[str, float] = {
    "title": 3.0, "keywords": 2.5, "career_paths": 2.0, "department": 1.5,
    "learning_outcomes": 1.5, "modules": 1.0, "description": 1.0, "industry": 1.0,
    "target_audience": 0.5,
}


def _field_text(course: Course, field: str) -> str:
    value = getattr(course, field)
    if field == "title" and course.title_ar:
        value = f"{value} {course.title_ar}"
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


class KeywordRetriever:
    def __init__(self, courses: list[Course]) -> None:
        self._courses = list(courses)
        self._by_id = {c.course_id: c for c in self._courses}
        # course_id -> token -> best field weight
        self._index: dict[str, dict[str, float]] = {}
        for c in self._courses:
            weights: dict[str, float] = defaultdict(float)
            for field, w in FIELD_WEIGHTS.items():
                for tok in tokenize(_field_text(c, field)):
                    weights[tok] = max(weights[tok], w)
            self._index[c.course_id] = dict(weights)

    def all_courses(self) -> list[Course]:
        return list(self._courses)

    def get_by_id(self, course_id: str) -> Course | None:
        return self._by_id.get(course_id.strip().upper())

    def find_by_title(self, text: str) -> Course | None:
        """Return the course whose ID or title is mentioned in `text`, if any."""
        upper = text.upper()
        for cid, course in self._by_id.items():
            if cid in upper:
                return course
        norm = normalize_arabic(text.lower())
        for course in self._courses:
            titles = [course.title.lower()] + ([normalize_arabic(course.title_ar)] if course.title_ar else [])
            if any(t in norm for t in titles):
                return course
        return None

    def search(
        self, query: str, top_k: int = 3, filters: CourseFilters | None = None
    ) -> list[RetrievalResult]:
        query_tokens = set(tokenize(query))
        if not query_tokens or top_k <= 0:
            return []
        results: list[RetrievalResult] = []
        for course in self._courses:
            if filters and not self._passes(course, filters):
                continue
            index = self._index[course.course_id]
            matched = sorted(t for t in query_tokens if t in index)
            if not matched:
                continue
            score = sum(index[t] for t in matched)
            results.append(RetrievalResult(course=course, score=round(score, 2), matched_terms=matched))
        results.sort(key=lambda r: (-r.score, r.course.course_id))
        return results[:top_k]

    @staticmethod
    def _passes(course: Course, f: CourseFilters) -> bool:
        if f.skill_level and course.skill_level not in (
            f.skill_level, SkillLevel.ALL_LEVELS, SkillLevel.UNKNOWN
        ):
            return False
        if f.delivery_format and course.delivery_format not in (
            f.delivery_format, DeliveryFormat.HYBRID, DeliveryFormat.UNKNOWN
        ):
            return False
        if f.max_fee is not None and course.fee.is_known:
            same_currency = f.currency is None or f.currency.upper() == course.fee.currency
            if same_currency and course.fee.amount > f.max_fee:
                return False
        return True
