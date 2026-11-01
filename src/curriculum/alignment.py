"""Accreditation / competency alignment check: where does a curriculum cover a framework, and where not?

How it works (deliberately simple and explainable):
  every framework area has search keywords; an area is
      COVERED  if 2 or more distinct keywords occur in the curriculum text,
      PARTIAL  if exactly 1 keyword occurs,
      MISSING  otherwise.
  One piece of text is counted once: longer keywords are matched first and the words they used are
  "claimed", so 'code review' does not also count as 'review', and 'trade-offs' not also as 'trade-off'.
The report lists the evidence (which module mentions which keyword) and, for gaps, what to add.

IMPORTANT (brief): this proposes alignment. It never says a course IS accredited. Every report
carries a disclaimer, and a reference checklist is labelled as such.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache

from src.curriculum.frameworks import Framework
from src.curriculum.models import AlignmentReport, AreaResult, AreaStatus, Curriculum
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic

COVERED_MIN_HITS = 2


def disclaimer(framework: Framework) -> str:
    base = (f"Alignment PROPOSAL against {framework.name}. It shows where the curriculum text mentions the "
            f"framework's topics. It does not mean the course is accredited, certified or recognised by "
            f"{framework.issuer}; no accreditation is claimed without supporting evidence.")
    if framework.status != "official_text":
        base += " The checklist was assembled by the REX AI team and is not the issuer's official text."
    return base


_AR_LETTER = "\u0621-\u064A"
_AR_PREFIX = "(?:[وفبلك]?ال|لل|[وفبلك])?"           # attached "and / with / for / the": والاختبار, بالبيانات
_AR_SUFFIX = "(?:ات|ه|يه|ين|ون|ي)?"                  # simple plural / feminine endings (after normalisation)


def _normalise(text: str) -> str:
    return normalize_arabic(text.strip().lower())


def _arabic_word(word: str) -> str:
    stem = word[2:] if word.startswith("ال") and len(word) > 4 else word      # the prefix group adds "ال" back
    return _AR_PREFIX + re.escape(stem) + _AR_SUFFIX


@lru_cache(maxsize=4096)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    key = _normalise(keyword)
    if contains_arabic(key):
        body = r"\s+".join(_arabic_word(w) for w in key.split())
        return re.compile(rf"(?<![{_AR_LETTER}]){body}(?![{_AR_LETTER}])")
    escaped = re.escape(key)
    # letters/digits on either side would make it a different word (e.g. "qa" inside "aqaba"); allow plural s/es
    return re.compile(rf"(?<![a-z0-9]){escaped}(?:s|es)?(?![a-z0-9])")


def find_terms(keywords: list[str], corpus: list[tuple[str, str]]) -> dict[str, set[str]]:
    """Which keywords occur where (English and Arabic; texts are normalised here). Longest keyword first; a span
    of text used by one keyword cannot be reused by a shorter one, so one phrase never counts as two pieces of
    evidence. Keys are the keywords as written (first spelling wins when two normalise to the same text)."""
    originals: dict[str, str] = {}
    for k in keywords:
        if k.strip():
            originals.setdefault(_normalise(k), k.strip().lower())
    ordered = sorted(originals, key=len, reverse=True)
    hits: dict[str, set[str]] = {}
    for where, text in corpus:
        text = _normalise(text)
        claimed: list[tuple[int, int]] = []
        for key in ordered:
            for m in _keyword_pattern(key).finditer(text):
                if any(m.start() < end and start < m.end() for start, end in claimed):
                    continue
                claimed.append((m.start(), m.end()))
                hits.setdefault(originals[key], set()).add(where)
    return hits


def check_framework(curriculum: Curriculum, framework: Framework,
                    now: datetime | None = None) -> AlignmentReport:
    corpus = curriculum.text_corpus()
    results: list[AreaResult] = []
    for area in framework.areas:
        hits = find_terms(area.all_keywords, corpus)        # keyword -> locations
        distinct = len(hits)
        status = (AreaStatus.COVERED if distinct >= COVERED_MIN_HITS
                  else AreaStatus.PARTIAL if distinct == 1 else AreaStatus.MISSING)
        evidence = sorted({w for locs in hits.values() for w in locs}, key=_location_sort_key)
        recommendation, suggested = "", ""
        if status != AreaStatus.COVERED:
            examples = ", ".join(area.keywords[:4])
            recommendation = (f"Add content for '{area.title}' (for example: {examples})." if status == AreaStatus.MISSING
                              else f"'{area.title}' is only touched on; deepen it (for example: {examples}).")
            suggested = area.expected_outcome
        results.append(AreaResult(area_id=area.id, title=area.title, status=status,
                                  matched_terms=sorted(hits), evidence=evidence,
                                  recommendation=recommendation, suggested_outcome=suggested))
    covered = sum(r.status == AreaStatus.COVERED for r in results)
    partial = sum(r.status == AreaStatus.PARTIAL for r in results)
    missing = len(results) - covered - partial
    return AlignmentReport(
        framework_id=framework.framework_id, framework_name=framework.name, issuer=framework.issuer,
        framework_status=framework.status, framework_verified=framework.verified, source_url=framework.source_url,
        checked_at=now or datetime.now(timezone.utc),
        coverage_percent=round(100 * (covered + 0.5 * partial) / len(results), 1),
        covered=covered, partial=partial, missing=missing, areas=results, disclaimer=disclaimer(framework))


def _location_sort_key(where: str) -> tuple[int, int, str]:
    m = re.match(r"Module (\d+)", where)
    return (0, int(m.group(1)), where) if m else (1, 0, where)
