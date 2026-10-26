"""Accreditation / competency alignment check: where does a curriculum cover a framework, and where not?

How it works (deliberately simple and explainable):
  every framework area has search keywords; an area is
      COVERED  if 2 or more distinct keywords occur in the curriculum text,
      PARTIAL  if exactly 1 keyword occurs,
      MISSING  otherwise.
The report lists the evidence (which module mentions which keyword) and, for gaps, what to add.

IMPORTANT (brief): this proposes alignment. It never says a course IS accredited. Every report
carries a disclaimer, and a reference checklist is labelled as such.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from src.curriculum.frameworks import Framework
from src.curriculum.models import AlignmentReport, AreaResult, AreaStatus, Curriculum

COVERED_MIN_HITS = 2


def disclaimer(framework: Framework) -> str:
    base = (f"Alignment PROPOSAL against {framework.name}. It shows where the curriculum text mentions the "
            f"framework's topics. It does not mean the course is accredited, certified or recognised by "
            f"{framework.issuer}; no accreditation is claimed without supporting evidence.")
    if framework.status != "official_text":
        base += " The checklist was assembled by the REX AI team and is not the issuer's official text."
    return base


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword.strip().lower())
    # letters/digits on either side would make it a different word (e.g. "qa" inside "aqaba"); allow plural s/es
    return re.compile(rf"(?<![a-z0-9]){escaped}(?:s|es)?(?![a-z0-9])")


def check_framework(curriculum: Curriculum, framework: Framework,
                    now: datetime | None = None) -> AlignmentReport:
    corpus = [(where, text.lower()) for where, text in curriculum.text_corpus()]
    results: list[AreaResult] = []
    for area in framework.areas:
        hits: dict[str, set[str]] = {}          # keyword -> locations
        for keyword in area.keywords:
            pattern = _keyword_pattern(keyword)
            for where, text in corpus:
                if pattern.search(text):
                    hits.setdefault(keyword, set()).add(where)
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
