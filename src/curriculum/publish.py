"""Turn an APPROVED curriculum for a new course into an approved catalog course.

Why: every agent reads the same shared catalog (courses table). A curriculum designed for a new
course ("Cloud Computing") has no course_id, so REX, Marketing, RFP and Document Generation could
not see it. Approving it now publishes it to the catalog, so all agents see it at once.

Only facts the curriculum actually contains are copied; generic template filler is skipped. Fee, schedule, instructor and delivery
format are not part of a curriculum, so they stay unknown (None) and REX says "not available".
"""
from __future__ import annotations

from datetime import date

from src.curriculum.models import Curriculum, Origin
from src.db.repositories import PUBLISHED_REFERENCE_PREFIX
from src.models.course import Course, SkillLevel, SourceMetadata, SourceType


def _skill_level(value: str | None) -> SkillLevel:
    try:
        return SkillLevel((value or "unknown").strip().lower())
    except ValueError:
        return SkillLevel.UNKNOWN


def _texts(items) -> list[str]:
    """Real content only. TEMPLATE text is generic filler from the deterministic generator ("presented to
    the instructors"), a proposal rather than a course fact, so it is not published as one."""
    return [i.text for i in items if getattr(i, "text", "").strip() and i.origin != Origin.TEMPLATE]


def course_from_curriculum(cur: Curriculum, course_id: str, curriculum_id: str, reviewer: str,
                           today: date | None = None) -> Course:
    keywords = list(dict.fromkeys(
        [cur.title.lower(), *(w for w in cur.title.lower().split() if len(w) > 2),
         *([cur.title_ar] if cur.title_ar else []), *(t.lower() for t in cur.tools)]))
    return Course(
        course_id=course_id,
        title=cur.title,
        title_ar=cur.title_ar,
        department=cur.department,
        description=cur.summary or None,
        target_audience=list(cur.target_audience),
        skill_level=_skill_level(cur.skill_level),
        industry=cur.industry,
        duration_hours=cur.total_hours,
        prerequisites=_texts(cur.prerequisites),
        learning_outcomes=_texts(cur.learning_outcomes),
        modules=[m.title for m in cur.modules],
        career_paths=list(cur.career_paths),
        keywords=keywords,
        tools=list(cur.tools),
        capstone=(cur.capstone.description.text
                  if cur.capstone and cur.capstone.description.origin != Origin.TEMPLATE else None),
        deliverables=_texts(cur.capstone.deliverables) if cur.capstone else [],
        country=cur.country,
        source=SourceMetadata(source_type=SourceType.OFFICIAL,
                              reference=f"{PUBLISHED_REFERENCE_PREFIX} {curriculum_id} (approved by {reviewer})",
                              last_updated=today or date.today(), verified=True),
    )
