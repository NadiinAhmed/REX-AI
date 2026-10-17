"""Recommendation workflow: learner profile -> retrieved courses -> scored, explained matches.

Reasons are structured (code + facts) so they can be rendered in Arabic or English, tested
without the UI, and handed to an LLM as grounded facts.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from src.knowledge_base.retriever import CourseFilters, CourseRetriever
from src.knowledge_base.text_utils import tokenize
from src.models.course import Course, DeliveryFormat, SkillLevel
from src.workflows.learner_profile import LearnerProfile


class Reason(BaseModel):
    code: str     # e.g. topic_match, career_match, level_match, budget_ok, prerequisites
    facts: dict[str, str | list[str]] = Field(default_factory=dict)
    positive: bool = True


class Recommendation(BaseModel):
    course: Course
    score: float
    reasons: list[Reason]


class RecommendationResult(BaseModel):
    recommendations: list[Recommendation]
    filters_relaxed: bool = False


def _filters_from(profile: LearnerProfile) -> CourseFilters:
    return CourseFilters(
        skill_level=profile.skill_level,
        delivery_format=profile.preferred_format,
        max_fee=profile.budget.amount if profile.budget else None,
        currency=profile.budget.currency if profile.budget else None,
    )


def explain_match(course: Course, matched_terms: list[str], profile: LearnerProfile) -> list[Reason]:
    reasons = [Reason(code="topic_match", facts={"terms": matched_terms})]

    if profile.career_goal:
        goal_tokens = set(tokenize(profile.career_goal))
        paths = [p for p in course.career_paths if goal_tokens & set(tokenize(p))]
        if paths:
            reasons.append(Reason(code="career_match", facts={"paths": paths}))

    if profile.skill_level:
        ok = course.skill_level in (profile.skill_level, SkillLevel.ALL_LEVELS)
        unknown = course.skill_level == SkillLevel.UNKNOWN
        reasons.append(Reason(
            code="level_unknown" if unknown else ("level_match" if ok else "level_mismatch"),
            facts={"course_level": course.skill_level.value},
            positive=ok,
        ))

    if profile.preferred_format:
        ok = course.delivery_format in (profile.preferred_format, DeliveryFormat.HYBRID)
        reasons.append(Reason(
            code="format_match" if ok else "format_mismatch",
            facts={"course_format": course.delivery_format.value},
            positive=ok,
        ))

    if profile.budget:
        if not course.fee.is_known:
            reasons.append(Reason(code="fee_unknown", positive=False))
        elif course.fee.currency != profile.budget.currency:
            reasons.append(Reason(
                code="fee_other_currency",
                facts={"fee": f"{course.fee.amount:g} {course.fee.currency}"}, positive=False,
            ))
        else:
            ok = course.fee.amount <= profile.budget.amount
            reasons.append(Reason(
                code="budget_ok" if ok else "over_budget",
                facts={"fee": f"{course.fee.amount:g} {course.fee.currency}"}, positive=ok,
            ))

    if course.prerequisites:
        reasons.append(Reason(code="prerequisites", facts={"items": course.prerequisites}, positive=False))
    return reasons


# Drop results scoring below this fraction of the best match (removes incidental matches,
# e.g. a marketing course that merely has an "Analytics" module).
MIN_RELATIVE_SCORE = 0.4


def recommend_courses(
    profile: LearnerProfile, retriever: CourseRetriever, top_k: int = 3
) -> RecommendationResult:
    """Retrieve courses for the profile. If hard filters remove everything, retry without
    them and flag `filters_relaxed` so the assistant can say so honestly."""
    query = profile.search_query()
    if not query:
        return RecommendationResult(recommendations=[])

    results = retriever.search(query, top_k=top_k, filters=_filters_from(profile))
    relaxed = False
    if not results:
        results = retriever.search(query, top_k=top_k)
        relaxed = bool(results)
    if results:
        cutoff = results[0].score * MIN_RELATIVE_SCORE
        results = [r for r in results if r.score >= cutoff]

    recs = [
        Recommendation(course=r.course, score=r.score, reasons=explain_match(r.course, r.matched_terms, profile))
        for r in results
    ]
    return RecommendationResult(recommendations=recs, filters_relaxed=relaxed)
