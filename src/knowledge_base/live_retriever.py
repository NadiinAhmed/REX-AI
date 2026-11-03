"""A course retriever that notices catalog changes.

KeywordRetriever and SemanticCourseRetriever index the courses they are given ONCE. When a course is
published from an approved curriculum while the app runs, that index would be stale until a restart.
LiveCourseRetriever asks the database for a cheap fingerprint (count + last update) on each call and
rebuilds the inner retriever only when it changed. Agents keep using the same CourseRetriever interface.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from src.knowledge_base.retriever import CourseFilters, CourseRetriever, RetrievalResult
from src.models.course import Course
from src.utils.logging_config import get_logger

logger = get_logger("kb.live_retriever")


class LiveCourseRetriever:
    def __init__(self, courses, build: Callable[[list[Course]], CourseRetriever]) -> None:
        self._courses = courses            # CourseRepository: list_approved() and version()
        self._build = build                # approved courses -> retriever (keyword or semantic)
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self._inner: CourseRetriever | None = None
        self._current()

    def _current(self) -> CourseRetriever:
        version = self._courses.version()
        if version != self._version or self._inner is None:
            with self._lock:
                if version != self._version or self._inner is None:
                    self._inner = self._build(self._courses.list_approved())
                    if self._version is not None:
                        logger.info("Course catalog changed: course search rebuilt")
                    self._version = version
        return self._inner

    # ---- CourseRetriever interface
    def search(self, query: str, top_k: int = 3, filters: CourseFilters | None = None,
               **kwargs) -> list[RetrievalResult]:
        return self._current().search(query, top_k=top_k, filters=filters, **kwargs)

    def get_by_id(self, course_id: str) -> Course | None:
        return self._current().get_by_id(course_id)

    def find_by_title(self, text: str) -> Course | None:
        return self._current().find_by_title(text)

    def all_courses(self) -> list[Course]:
        return self._current().all_courses()

    @property
    def inner(self) -> CourseRetriever:
        return self._current()

    def __getattr__(self, name: str) -> Any:
        # Retriever-specific settings (uses_natural_language, min_absolute_score, ...) come from the inner one.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._current(), name)
