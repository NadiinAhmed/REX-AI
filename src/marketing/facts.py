"""The ONLY way academy facts reach the Marketing Agent.

CourseFactsProvider combines three approved sources into one `CourseFacts`:
  1. the course record    (CourseRetriever: direct database or the rex-academy MCP server)
  2. the approved curriculum (CurriculumSource: Agent 3; a null source until it exists)
  3. active offers        (OfferReader: approved and currently valid only)
Every Fact keeps its source label, so each sentence in an ad can be traced back.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel, Field

from src.knowledge_base.retriever import CourseRetriever
from src.marketing.models import CourseFacts, Fact
from src.models.course import Course, DeliveryFormat, SkillLevel
from src.services.offer_service import OfferReader


class CourseNotFoundError(LookupError):
    pass


class CurriculumFacts(BaseModel):
    """What an APPROVED curriculum adds. Produced by the Curriculum Agent (Agent 3) later."""
    course_id: str
    learning_outcomes: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    reference: str


class CurriculumSource(Protocol):
    def approved_curriculum(self, course_id: str) -> CurriculumFacts | None: ...


class NullCurriculumSource:
    """Used until the Curriculum Agent exists: there is never an approved curriculum."""
    def approved_curriculum(self, course_id: str) -> CurriculumFacts | None:
        return None


def _join(values: list[str]) -> str:
    return "; ".join(v for v in values if v)


class CourseFactsProvider:
    def __init__(self, courses: CourseRetriever, offers: OfferReader | None = None,
                 curriculum: CurriculumSource | None = None, is_synthetic: bool = False) -> None:
        self._courses = courses
        self._offers = offers
        self._curriculum = curriculum or NullCurriculumSource()
        self._is_synthetic = is_synthetic

    # ------------------------------------------------------------------ lookup
    def list_courses(self) -> list[Course]:
        return self._courses.all_courses()

    def find_course(self, text: str) -> Course | None:
        return self._courses.find_by_title(text)

    def get_course(self, course_id: str) -> Course | None:
        return self._courses.get_by_id(course_id)

    # ------------------------------------------------------------------ facts
    def facts_for(self, course_id: str, on: date | None = None) -> CourseFacts:
        course = self._courses.get_by_id(course_id)
        if course is None:
            raise CourseNotFoundError(f"No approved course with id {course_id}.")
        src = f"{course.course_id} ({course.source.reference})"
        facts: list[Fact] = []

        def add(field: str, value: str | None, source: str = src) -> None:
            if value:
                facts.append(Fact(field=field, value=value, source=source))

        add("description", course.description)
        add("target_audience", _join(course.target_audience))
        if course.duration_hours:
            add("duration", f"{course.duration_hours:g} hours")
        add("duration_text", course.duration_text)
        if course.delivery_format != DeliveryFormat.UNKNOWN:
            add("delivery_format", course.delivery_format.value)
        if course.skill_level != SkillLevel.UNKNOWN:
            add("skill_level", course.skill_level.value)
        add("prerequisites", _join(course.prerequisites))
        add("career_paths", _join(course.career_paths))
        add("tools", _join(course.tools))
        add("capstone", course.capstone)
        add("deliverables", _join(course.deliverables))
        if course.fee.is_known:
            add("fee", f"{course.fee.amount:g} {course.fee.currency}")
        add("schedule", course.schedule)
        add("instructor", course.instructor)
        add("country", course.country)

        outcomes, modules = list(course.learning_outcomes), list(course.modules)
        notes: list[str] = []
        curriculum = self._curriculum.approved_curriculum(course.course_id)
        if curriculum is not None:
            cur_src = f"approved curriculum ({curriculum.reference})"
            if curriculum.learning_outcomes:
                add("learning_outcomes", _join(curriculum.learning_outcomes), cur_src)
            if curriculum.modules:
                add("modules", _join(curriculum.modules), cur_src)
            outcomes = outcomes or curriculum.learning_outcomes
            modules = modules or curriculum.modules
        if not curriculum or not curriculum.learning_outcomes:
            add("learning_outcomes", _join(course.learning_outcomes))
        if not curriculum or not curriculum.modules:
            add("modules", _join(course.modules))

        missing = [m for m in course.missing_fields()
                   if not (m == "learning_outcomes" and outcomes) and not (m == "modules" and modules)]
        offers = []
        if self._offers is not None:
            try:
                offers = self._offers.active_offers(course.course_id, on)
            except Exception as exc:    # e.g. MCP server down: continue safely WITHOUT offers
                notes.append(f"Offers could not be loaded ({type(exc).__name__}); the copy will not mention offers.")
        if course.promotions:
            notes.append("The course record has a free-text promotion. Ads do not use it: add it as an "
                         "approved, dated offer on the Offers page to use it.")
        if not offers:
            notes.append("No approved, currently valid offer: the copy must not mention discounts.")
        if not course.source.verified:
            notes.append("Course record is not marked verified by the academy.")

        return CourseFacts(
            course_id=course.course_id, title=course.title, title_ar=course.title_ar, facts=facts,
            missing_fields=missing, offers=offers, notes=notes, is_synthetic=self._is_synthetic,
            fee_amount=course.fee.amount if course.fee.is_known else None,
            fee_currency=course.fee.currency if course.fee.is_known else None,
            duration_hours=course.duration_hours, duration_text=course.duration_text,
            delivery_format=None if course.delivery_format == DeliveryFormat.UNKNOWN else course.delivery_format.value,
            skill_level=None if course.skill_level == SkillLevel.UNKNOWN else course.skill_level.value,
            schedule=course.schedule, instructor=course.instructor)
