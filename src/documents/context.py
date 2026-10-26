"""Which data a document is built from.

Preference order (traceability): an APPROVED curriculum  >  any stored curriculum  >  the catalog record.
The chosen basis is written on the document ("Built from approved curriculum CUR-1A2B3C rev 3"), so
a reader always knows how trustworthy the content is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.curriculum.generator import CurriculumError
from src.curriculum.models import AlignmentReport, Curriculum, CurriculumStatus
from src.curriculum.service import CurriculumService
from src.documents.models import DocumentRequest
from src.models.course import Course


class DocumentError(Exception):
    """A document cannot be produced (message is safe to show to the user)."""


@dataclass
class DocContext:
    curriculum: Curriculum
    course: Course | None
    alignment: list[AlignmentReport]
    basis: str
    language: str
    audience: str | None
    today: date
    curriculum_id: str | None = None
    curriculum_revision: int | None = None
    curriculum_status: CurriculumStatus | None = None
    notices: list[str] = field(default_factory=list)

    @property
    def approved_basis(self) -> bool:
        return self.curriculum_status == CurriculumStatus.APPROVED


def resolve_context(request: DocumentRequest, curricula: CurriculumService, courses, today: date) -> DocContext:
    course: Course | None = courses.get(request.course_id) if request.course_id else None
    notices: list[str] = []
    stored = None
    if request.curriculum_id:
        stored = curricula.get(request.curriculum_id)
        if stored is None:
            raise DocumentError(f"No curriculum with ID {request.curriculum_id}.")
    elif request.course_id:
        if course is None:
            raise DocumentError(f"Course {request.course_id} is not in the approved catalog.")
        stored = curricula.approved_for_course(request.course_id)

    if stored is not None:
        cur = stored.curriculum
        if course is None and cur.course_id:
            course = courses.get(cur.course_id)
        if request.require_approved_curriculum and stored.status != CurriculumStatus.APPROVED:
            raise DocumentError(f"Curriculum {stored.id} is {stored.status.label}: approve it first, "
                                "or allow documents from unapproved curricula.")
        basis = f"{'Approved' if stored.status == CurriculumStatus.APPROVED else stored.status.label.capitalize()} curriculum {stored.id} (revision {stored.revision})"
        if stored.status != CurriculumStatus.APPROVED:
            notices.append(f"Built from curriculum {stored.id}, which is {stored.status.label}: content may still change.")
        return DocContext(curriculum=cur, course=course, alignment=stored.alignment, basis=basis, language=request.language,
                          audience=request.audience, today=today, curriculum_id=stored.id, curriculum_revision=stored.revision,
                          curriculum_status=stored.status, notices=notices)

    if request.require_approved_curriculum:
        raise DocumentError(f"There is no approved curriculum for {request.course_id}. Approve one on the Curriculum page first.")
    try:
        cur = curricula.draft_for_course(request.course_id)
    except CurriculumError as exc:
        raise DocumentError(str(exc)) from exc
    notices.append("No approved curriculum exists for this course: the document was built directly from the course record "
                   "and portfolio topics. Approve a curriculum to base documents on reviewed content.")
    return DocContext(curriculum=cur, course=course, alignment=[], basis=f"Course record {request.course_id} (no approved curriculum)",
                      language=request.language, audience=request.audience, today=today, notices=notices)
