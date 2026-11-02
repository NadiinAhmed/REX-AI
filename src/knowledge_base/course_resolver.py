"""Work out which catalog course a message is about: "TRK-002", "Flutter Development",
"the data science track", "كورس الفلاتر".

Why a separate class: REX Assistant and the Marketing Agent both need this, and before this
module each one only recognised a course by its exact code or full title. One shared resolver
keeps the two agents consistent and gives one place to test.

The resolver never guesses. It returns a course only when exactly one course matches; if two
courses match ("flutter or react?") it returns no course plus the candidates, so the caller can
ask which one was meant.

Matching levels, strongest first:

1. exact   - course code or full English/Arabic title (the source's own `find_by_title`).
2. alias   - only when the caller says the message is about a course (`course_context=True`)
             or the message contains a course noun ("track", "course", "كورس", "مسار" ...):
               * a phrase of two or more consecutive title words ("data science", "machine learning",
                 "علم البيانات");
               * a distinctive title word that is not a common word ("flutter", "react", "فلاتر");
3. noun    - a title word that belongs to one course only, written right next to a course noun
             ("the testing track", "مسار الاختبار"). This works even for common words, because
             the noun shows the word names a course.

Example of why level 2 needs context: "I want to work in machine learning" is a learner's goal,
which should get a recommendation with reasons, not a fixed course. "Who teaches the machine
learning track?" is a question about one course.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from src.knowledge_base.text_utils import SYNONYMS, normalize_arabic
from src.models.course import Course

_TOKEN = re.compile(r"[a-z0-9+#]+|[\u0621-\u064A]+")
# Split a title into segments: "Data Science, AI & Machine Learning" -> 3 segments.
_SEGMENT_SPLIT = re.compile(r"[—–\-,&/()،]|\s+و\s+|\s+and\s+")
# Longest first. "و" (and) is attached to the next word in Arabic: "وتعلم" -> "تعلم".
_ARABIC_PREFIXES = ("وال", "بال", "فال", "كال", "لل", "ال", "و")

COURSE_NOUNS = {
    "track", "tracks", "course", "courses", "program", "programs", "programme", "programmes",
    "diploma", "bootcamp", "كورس", "كورسات", "مسار", "مسارات", "برنامج", "برامج", "دوره", "دورات",
    "دبلومه",
}
# Title words that are too common to identify a course on their own. They still count inside a
# multi-word phrase ("software testing") or right next to a course noun ("the testing track").
GENERIC_WORDS = {
    "software", "testing", "data", "development", "engineering", "science", "machine", "learning",
    "full", "stack", "ai", "web", "app", "apps", "applications", "fundamentals", "foundations",
    "basics", "essentials", "introduction", "intro", "design", "management", "project", "analysis",
    "financial", "accounting", "digital", "marketing", "with", "for", "and", "of", "the", "to", "in",
    "اساسيات", "تطوير", "تطبيقات", "هندسه", "بيانات", "علم", "تعلم", "اله", "ويب", "متكامل", "ذكاء",
    "اصطناعي", "اختبار", "برمجيات", "معسكر", "تسويق", "رقمي", "اداره", "مشاريع", "امن", "تصميم",
    "واجهات", "مستخدم", "محاسبه", "ماليه", "باستخدام", "تحليل", "ل",
}
_MIN_NAME_LENGTH = 3


class CourseSource(Protocol):
    """Anything that can list courses and find one by code/title (keyword, semantic or MCP retriever)."""

    def all_courses(self) -> list[Course]: ...

    def find_by_title(self, text: str) -> Course | None: ...


@dataclass(frozen=True)
class CourseMatch:
    """Result of `CourseResolver.resolve`.

    `course` is set only when exactly one course matched. `candidates` lists course IDs when the
    message matched several courses, so the caller can ask "did you mean A or B?".
    `level` says how the match was found: exact | alias | noun | ambiguous | none.
    """
    course: Course | None
    level: str
    candidates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return self.course is not None


def _strip_arabic_prefix(token: str) -> str:
    for prefix in _ARABIC_PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            return token[len(prefix):]
    return token


def _clean_token(token: str) -> str:
    """'لكورس' -> 'كورس', 'الفلاتر' -> 'فلاتر'. English tokens are returned unchanged."""
    if not re.match(r"[\u0621-\u064A]", token):
        return token
    token = _strip_arabic_prefix(token)
    if token[0] in "لب" and token[1:] in COURSE_NOUNS:
        return token[1:]
    return token


def canonical(words: list[str] | set[str]) -> set[str]:
    """Map words through the shared search glossary: "qa" -> "testing", "موبايل" -> "mobile",
    "دوت" -> ".net", "net" -> ".net". This lets nicknames that are not in any title find a course."""
    return {c for w in words for c in SYNONYMS.get(w, w).split()}


def tokens(text: str) -> list[str]:
    """Normalized, prefix-stripped tokens, in order (order matters for phrases)."""
    return [_clean_token(t) for t in _TOKEN.findall(normalize_arabic(text.lower()))]


@dataclass
class _CourseAliases:
    course_id: str
    phrases: set[tuple[str, ...]]      # consecutive title words, length >= 2
    names: set[str]                    # distinctive single title words
    words: set[str]                    # every title word (used with a neighbouring course noun)
    canon_names: set[str] = field(default_factory=set)   # glossary forms of the names ("qa" -> testing)
    canon_words: set[str] = field(default_factory=set)


class CourseResolver:
    def __init__(self, source: CourseSource) -> None:
        self._source = source

    def resolve(self, text: str, *, course_context: bool = False) -> CourseMatch:
        if not text or not text.strip():
            return CourseMatch(None, "none")
        exact = self._source.find_by_title(text)
        if exact is not None:
            return CourseMatch(exact, "exact")

        courses = {c.course_id: c for c in self._source.all_courses()}
        if not courses:
            return CourseMatch(None, "none")
        aliases = self._build_aliases(list(courses.values()))
        words = tokens(text)
        has_noun = any(w in COURSE_NOUNS for w in words)

        alias_hits: set[str] = set()
        canon = canonical(words)
        if course_context or has_noun:
            for a in aliases:
                if (any(self._contains(words, p) for p in a.phrases) or any(n in words for n in a.names)
                        or canon & a.canon_names):
                    alias_hits.add(a.course_id)
        noun_hits = self._noun_adjacent_hits(words, aliases)

        hits = alias_hits | noun_hits
        if len(hits) == 1:
            cid = next(iter(hits))
            return CourseMatch(courses[cid], "alias" if cid in alias_hits else "noun")
        if len(hits) > 1:
            return CourseMatch(None, "ambiguous", tuple(sorted(hits)))
        return CourseMatch(None, "none")

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _title_segments(course: Course) -> list[list[str]]:
        segments: list[list[str]] = []
        for title in (course.title, course.title_ar or ""):
            for part in _SEGMENT_SPLIT.split(normalize_arabic(title.lower())):
                words = [w for w in tokens(part) if w not in {"and", "و"}]
                if words:
                    segments.append(words)
        return segments

    def _build_aliases(self, courses: list[Course]) -> list[_CourseAliases]:
        courses_by_id = {c.course_id: c for c in courses}
        result: list[_CourseAliases] = []
        word_owners: dict[str, set[str]] = {}
        for c in courses:
            segments = self._title_segments(c)
            phrases = {tuple(seg[i:j]) for seg in segments
                       for i in range(len(seg)) for j in range(i + 2, len(seg) + 1)}
            words = {w for seg in segments for w in seg}
            for w in words:
                word_owners.setdefault(w, set()).add(c.course_id)
            result.append(_CourseAliases(c.course_id, phrases, set(), words))
        for a in result:
            # A word shared by two titles ("data") identifies neither course.
            a.words = {w for w in a.words if len(word_owners[w]) == 1}
            a.names = {w for w in a.words if w not in GENERIC_WORDS and len(w) >= _MIN_NAME_LENGTH}
        # Same idea for glossary forms: "mobile" (from "تطبيقات") belongs only to the Flutter track.
        canon_owners: dict[str, set[str]] = {}
        for a in result:
            for w in canonical(a.words | {w for seg in self._title_segments(courses_by_id[a.course_id])
                                          for w in seg}):
                canon_owners.setdefault(w, set()).add(a.course_id)
        for a in result:
            mine = {w for w, owners in canon_owners.items() if owners == {a.course_id}}
            a.canon_words = mine
            a.canon_names = {w for w in mine if w not in GENERIC_WORDS and len(w) >= _MIN_NAME_LENGTH}
        return result

    @staticmethod
    def _contains(words: list[str], phrase: tuple[str, ...]) -> bool:
        n = len(phrase)
        return any(tuple(words[i:i + n]) == phrase for i in range(len(words) - n + 1))

    @staticmethod
    def _noun_adjacent_hits(words: list[str], aliases: list[_CourseAliases]) -> set[str]:
        """'testing track', 'the testing course', 'مسار الاختبار', 'كورس الفلاتر'."""
        neighbours: set[str] = set()
        for i, w in enumerate(words):
            if w in COURSE_NOUNS:
                if i > 0:
                    neighbours.add(words[i - 1])
                if i + 1 < len(words):
                    neighbours.add(words[i + 1])
        canon_neighbours = canonical(neighbours)
        return {a.course_id for a in aliases if a.words & neighbours or a.canon_words & canon_neighbours}
