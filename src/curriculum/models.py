"""Data shapes for the Curriculum agent.

Traceability rule (brief: "all generated outputs are traceable to appropriate source data"):
every outcome, topic, prerequisite and exercise carries an `origin` and, when there is one, a
`source` (e.g. "Andalusia Academy Portfolio 2026-2027, p.3"). Items the agent made up from templates
are labelled `template`, items written by an LLM are labelled `llm`, so a reviewer can see at a
glance what is evidence and what is a proposal.

Unknown values stay empty: the agent never invents fees, prerequisites, accreditation or dates.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class CurriculumStatus(str, Enum):
    DRAFT = "draft"                          # being generated or edited
    IN_REVIEW = "in_review"                  # submitted, waiting for an instructor/administrator
    CHANGES_REQUESTED = "changes_requested"  # reviewer asked for modifications
    APPROVED = "approved"                    # signed off: other agents may use it

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


class Origin(str, Enum):
    CATALOG = "catalog"        # the approved course record
    DOCUMENT = "document"      # an approved PDF (page cited)
    FRAMEWORK = "framework"    # a gap found against a competency framework
    USER = "user"              # typed by the person requesting / reviewing the curriculum
    TEMPLATE = "template"      # produced by a deterministic template: a proposal, not evidence
    LLM = "llm"                # written by the language model: a proposal, must be reviewed


class TracedText(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    origin: Origin = Origin.TEMPLATE
    source: str | None = None


class Outcome(TracedText):
    id: str = ""                                       # "LO1", set by the generator
    module_numbers: list[int] = Field(default_factory=list)   # modules that teach it (for the LO matrix)


class CurriculumModule(BaseModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    hours: float = Field(ge=0)
    topics: list[TracedText] = Field(default_factory=list)
    exercises: list[TracedText] = Field(default_factory=list)       # practical exercises
    assessment: str = ""
    origin: Origin = Origin.CATALOG
    source: str | None = None


class AssessmentItem(BaseModel):
    name: str
    kind: str                                            # quiz | lab | project | presentation | participation
    weight_percent: float = Field(ge=0, le=100)
    description: str = ""


class Capstone(BaseModel):
    description: TracedText
    deliverables: list[TracedText] = Field(default_factory=list)
    hours: float = Field(default=0, ge=0)


class SourceNote(BaseModel):
    kind: str                       # course_record | document | framework | rfp_signal | user_input
    reference: str
    detail: str = ""
    last_updated: str | None = None


class Curriculum(BaseModel):
    title: str = Field(min_length=3)
    title_ar: str | None = None
    summary: str = ""                                        # course overview (from the course record)
    course_id: str | None = None
    department: str | None = None
    industry: str | None = None
    country: str | None = None
    skill_level: str = "unknown"
    target_audience: list[str] = Field(default_factory=list)
    prerequisites: list[TracedText] = Field(default_factory=list)
    objectives: list[TracedText] = Field(default_factory=list)
    learning_outcomes: list[Outcome] = Field(default_factory=list)
    modules: list[CurriculumModule] = Field(default_factory=list)
    capstone: Capstone | None = None
    assessments: list[AssessmentItem] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    career_paths: list[str] = Field(default_factory=list)
    framework_ids: list[str] = Field(default_factory=list)
    total_hours: float | None = Field(default=None, gt=0)
    hours_origin: Origin = Origin.CATALOG
    market_signals: list[str] = Field(default_factory=list)
    sources: list[SourceNote] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)         # gaps and assumptions the reviewer must see
    generation_mode: str = "deterministic"                   # deterministic | llm
    language: str = "en"

    # ---------------------------------------------------------------- derived values
    @property
    def module_hours(self) -> float:
        return round(sum(m.hours for m in self.modules), 2)

    @property
    def capstone_hours(self) -> float:
        return self.capstone.hours if self.capstone else 0.0

    @property
    def allocated_hours(self) -> float:
        return round(self.module_hours + self.capstone_hours, 2)

    def text_corpus(self) -> list[tuple[str, str]]:
        """(location, text) pairs used by the alignment check: where could a topic be taught?"""
        rows: list[tuple[str, str]] = [("title", self.title)]
        for m in self.modules:
            rows.append((f"Module {m.number}", m.title))
            rows += [(f"Module {m.number}", t.text) for t in m.topics]
            rows += [(f"Module {m.number}", t.text) for t in m.exercises]
        rows += [("Learning outcomes", o.text) for o in self.learning_outcomes]
        rows += [("Objectives", o.text) for o in self.objectives]
        rows += [("Tools", t) for t in self.tools]
        if self.capstone:
            rows.append(("Capstone", self.capstone.description.text))
            rows += [("Capstone", d.text) for d in self.capstone.deliverables]
        return rows

    def validation_problems(self) -> list[str]:
        """Blocking problems: a curriculum with any of these cannot be approved."""
        problems: list[str] = []
        if not self.modules:
            problems.append("The curriculum has no modules.")
        if not self.learning_outcomes:
            problems.append("The curriculum has no learning outcomes.")
        if self.total_hours is None:
            problems.append("Total training hours are not set.")
        elif abs(self.allocated_hours - self.total_hours) > 0.01:
            problems.append(f"Module + capstone hours ({self.allocated_hours}) differ from the total "
                            f"({self.total_hours}).")
        if self.assessments and abs(sum(a.weight_percent for a in self.assessments) - 100) > 0.01:
            problems.append("Assessment weights do not add up to 100%.")
        return problems


class CurriculumRequest(BaseModel):
    """The agent's discovery questions (brief section 10) as one validated input."""
    course_id: str | None = None                      # base the curriculum on this catalog course
    title: str | None = None                          # or design a new course
    target_audience: list[str] = Field(default_factory=list)
    skill_level: str | None = None
    duration_hours: float | None = Field(default=None, gt=0, le=2000)
    learning_outcomes: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    framework_ids: list[str] = Field(default_factory=list)
    industry: str | None = None
    country: str | None = None
    practical_projects: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)

    @field_validator("course_id")
    @classmethod
    def _normalise_id(cls, v: str | None) -> str | None:
        return v.strip().upper() if v and v.strip() else None

    @model_validator(mode="after")
    def _needs_a_subject(self) -> "CurriculumRequest":
        if not self.course_id and not (self.title and self.title.strip()):
            raise ValueError("Give a course_id from the catalog or a title for a new course.")
        return self


# ------------------------------------------------------------------ alignment report
class AreaStatus(str, Enum):
    COVERED = "covered"
    PARTIAL = "partial"
    MISSING = "missing"


class AreaResult(BaseModel):
    area_id: str
    title: str
    status: AreaStatus
    matched_terms: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)       # where it is taught, e.g. "Module 2"
    recommendation: str = ""                                 # what to add when not fully covered
    suggested_outcome: str = ""


class AlignmentReport(BaseModel):
    framework_id: str
    framework_name: str
    issuer: str
    framework_status: str               # reference_checklist | official_text
    framework_verified: bool = False
    source_url: str | None = None
    checked_at: datetime
    coverage_percent: float
    covered: int
    partial: int
    missing: int
    areas: list[AreaResult]
    disclaimer: str

    @property
    def gaps(self) -> list[AreaResult]:
        return [a for a in self.areas if a.status != AreaStatus.COVERED]
