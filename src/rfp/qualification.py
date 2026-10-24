"""Does the academy MAY qualify? An honest, explainable first assessment, never a promise.

Two questions, both answered from data we actually have:
1. Topic fit: does the tender's subject match tracks in the academy's approved course catalog?
2. Requirements to verify: does the eligibility text ask for things we cannot confirm from our
   data (licences, local registration, years of experience, guarantees, certifications)?

Result: STRONG (topic match, requirements published and nothing to verify) · POSSIBLE (match but
requirements to check or unpublished, or general training with no specific track) · WEAK (no
topic match and requirements to check).
"""
from __future__ import annotations

import re

from src.models.course import Course
from src.rfp.extraction import _has_term, normalize_text
from src.rfp.models import Fit, Opportunity

# Eligibility phrases we cannot verify automatically: a person must check them.
REQUIREMENT_CHECKS: dict[str, tuple[str, ...]] = {
    "local company registration": ("commercial registration", "commercial register", "registered in",
                                   "local company", "saudi company", "egyptian company", "سجل تجاري",
                                   "السجل التجاري", "شركة مسجلة"),
    "government classification / licence": ("classification", "licence", "license", "licensed",
                                            "tvtc", "ترخيص", "تصنيف", "رخصة"),
    "accreditation or ISO certificate": ("iso", "accredited", "accreditation", "certified training provider",
                                         "اعتماد", "معتمد", "شهادة الايزو"),
    "minimum years of experience": ("years of experience", "years experience", "سنوات خبرة",
                                    "سنوات من الخبرة", "خبرة لا تقل"),
    "bid bond or bank guarantee": ("bid bond", "bank guarantee", "guarantee", "ضمان بنكي", "خطاب ضمان",
                                   "ضمان ابتدائي"),
    "Saudization / Nitaqat compliance": ("saudization", "nitaqat", "نطاقات", "السعودة"),
    "previous similar contracts": ("similar projects", "previous contracts", "track record",
                                   "سابقة اعمال", "مشاريع مماثلة"),
}


def _course_terms(course: Course) -> tuple[str, ...]:
    terms = [course.title, *(course.keywords or []), *(getattr(course, "tools", None) or [])]
    if course.department:
        terms.append(course.department)
    # very short tokens ("ai", "qa") are kept: _has_term matches whole words only
    return tuple(t for t in terms if t and len(t.strip()) >= 2)


def assess(opp: Opportunity, courses: list[Course]) -> tuple[Fit, list[str], list[str]]:
    """Return (fit, human-readable reasons, matched course ids)."""
    text = normalize_text(" ".join(filter(None, [opp.title, opp.description])))
    eligibility = normalize_text(opp.eligibility)

    matched = [c for c in courses if _has_term(text, _course_terms(c))]
    to_verify = [label for label, terms in REQUIREMENT_CHECKS.items() if _has_term(eligibility, terms)]
    years = re.search(r"(\d+)\s*(?:\+\s*)?(?:years|year|سنوات|سنة|عام)", eligibility)

    reasons: list[str] = []
    if matched:
        reasons.append("Topic matches academy tracks: " + ", ".join(f"{c.title} ({c.course_id})" for c in matched))
    else:
        reasons.append("No specific academy track matches the topic; general training scope only")
    for label in to_verify:
        detail = f" ({years.group(1)}+ years)" if label == "minimum years of experience" and years else ""
        reasons.append(f"Verify: {label}{detail}")
    if not opp.eligibility:
        reasons.append("Eligibility requirements not published in the listing: check the tender documents")

    if matched and not to_verify and opp.eligibility:     # can't be STRONG if requirements are unpublished
        fit = Fit.STRONG
    elif matched or not to_verify:
        fit = Fit.POSSIBLE
    else:
        fit = Fit.WEAK
    return fit, reasons, [c.course_id for c in matched]