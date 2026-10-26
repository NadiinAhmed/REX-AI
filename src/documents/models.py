"""Document types, the request, and the stored result of Agent 4."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.pdf.content import DocumentContent


class DocType(str, Enum):
    """The ten documents in the brief (section 3, Agent 4)."""
    COURSE_DESCRIPTION = "course_description"
    COURSE_PROPOSAL = "course_proposal"
    CURRICULUM_PDF = "curriculum_pdf"
    PARTICIPANT_GUIDE = "participant_guide"
    INSTRUCTOR_GUIDE = "instructor_guide"
    PRESENTATION_OUTLINE = "presentation_outline"
    QUESTION_BANK = "question_bank"
    ASSESSMENT_RUBRICS = "assessment_rubrics"
    CAPSTONE_BRIEF = "capstone_brief"
    LEARNING_OUTCOMES_PLAN = "learning_outcomes_plan"

    @property
    def label(self) -> str:
        return {"course_description": "Course description", "course_proposal": "Course proposal",
                "curriculum_pdf": "Curriculum PDF", "participant_guide": "Participant guide",
                "instructor_guide": "Facilitator / instructor guide", "presentation_outline": "Presentation outline",
                "question_bank": "Question bank", "assessment_rubrics": "Assessment rubrics",
                "capstone_brief": "Capstone project brief",
                "learning_outcomes_plan": "Learning outcomes and assessment plan"}[self.value]

    @property
    def label_ar(self) -> str:
        return {"course_description": "وصف المقرر", "course_proposal": "مقترح المقرر",
                "curriculum_pdf": "المنهج الدراسي", "participant_guide": "دليل المشارك",
                "instructor_guide": "دليل المدرب", "presentation_outline": "مخطط العرض التقديمي",
                "question_bank": "بنك الأسئلة", "assessment_rubrics": "معايير التقييم",
                "capstone_brief": "ملخص مشروع التخرج",
                "learning_outcomes_plan": "مخرجات التعلم وخطة التقييم"}[self.value]


class DocStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"


class DocumentRequest(BaseModel):
    """The agent's discovery questions (brief section 10) as one validated input."""
    doc_type: DocType
    course_id: str | None = None          # a catalog course ...
    curriculum_id: str | None = None      # ... or a stored curriculum (also for new courses)
    language: Literal["en", "ar"] = "en"
    audience: str | None = Field(default=None, max_length=300)    # "Who is the intended audience?"
    require_approved_curriculum: bool = False

    @model_validator(mode="after")
    def _needs_a_subject(self) -> "DocumentRequest":
        self.course_id = self.course_id.strip().upper() if self.course_id and self.course_id.strip() else None
        self.curriculum_id = self.curriculum_id.strip().upper() if self.curriculum_id and self.curriculum_id.strip() else None
        if not self.course_id and not self.curriculum_id:
            raise ValueError("Say which course (course_id) or curriculum (curriculum_id) the document is for.")
        return self


@dataclass
class StoredDocument:
    id: str
    doc_type: DocType
    course_id: str | None
    curriculum_id: str | None
    curriculum_revision: int | None
    language: str
    title: str
    status: DocStatus
    revision: int
    basis: str
    template_version: str
    generation_mode: str
    content: DocumentContent
    notices: list[str]
    log: list[dict[str, Any]]
    approved_by: str | None = None
    approved_at: datetime | None = None
