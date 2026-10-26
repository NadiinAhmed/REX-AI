"""Marketing Agent data models. Plain Pydantic: no LLM, database or UI code here."""
from __future__ import annotations

from enum import Enum
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from src.models.offer import Offer


class Platform(str, Enum):
    SOCIAL_CAPTION = "social_caption"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class CampaignObjective(str, Enum):
    AWARENESS = "awareness"              # people should know the course exists
    LEAD_GENERATION = "lead_generation"  # people should ask for details / leave contact
    ENROLLMENT = "enrollment"            # people should register
    RE_ENGAGEMENT = "re_engagement"      # past contacts should come back


class Tone(str, Enum):
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    ENTHUSIASTIC = "enthusiastic"
    FORMAL = "formal"


Language = Literal["en", "ar"]


class CampaignBrief(BaseModel):
    """What the user wants. Built up over one or more turns; never holds academy facts."""
    course_id: str | None = None
    platforms: list[Platform] = Field(default_factory=list)
    objective: CampaignObjective | None = None
    audience: str | None = Field(default=None, max_length=300)
    language: Language | None = None
    tone: Tone | None = None
    variations: int = Field(default=3, ge=1, le=5)
    budget_note: str | None = Field(default=None, max_length=200)
    requested_offer: str | None = Field(default=None, max_length=300)   # user text: untrusted

    REQUIRED: ClassVar[tuple[str, ...]] = ("course_id", "platforms", "objective")

    def missing_required(self) -> list[str]:
        return [f for f in self.REQUIRED if getattr(self, f) in (None, [], "")]

    def merged_with(self, newer: "CampaignBrief", newer_fields: set[str]) -> "CampaignBrief":
        """Values the user gave in `newer` (listed in `newer_fields`) replace older ones."""
        data = self.model_dump()
        data.update({f: getattr(newer, f) for f in newer_fields})
        return CampaignBrief.model_validate(data)


class Fact(BaseModel):
    """One approved fact the copy may use, with where it came from."""
    field: str
    value: str
    source: str


class CourseFacts(BaseModel):
    """Everything the generator is ALLOWED to say about one course."""
    course_id: str
    title: str
    title_ar: str | None = None
    facts: list[Fact] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)          # approved AND currently valid only
    notes: list[str] = Field(default_factory=list)
    is_synthetic: bool = False
    # raw values the claim checker compares numbers against (None = unknown)
    fee_amount: float | None = None
    fee_currency: str | None = None
    duration_hours: float | None = None
    duration_text: str | None = None
    delivery_format: str | None = None       # online | in_person | hybrid | None
    skill_level: str | None = None           # beginner | ... | None
    schedule: str | None = None
    instructor: str | None = None

    def value(self, field: str) -> str | None:
        return next((f.value for f in self.facts if f.field == field), None)

    def all_text(self) -> str:
        return " ".join([self.title, self.title_ar or ""] + [f.value for f in self.facts]).lower()

    def prompt_view(self, lang: str) -> dict:
        """The exact data handed to the LLM (also stored as the campaign's facts snapshot)."""
        return {
            "course_id": self.course_id,
            "title": self.title_ar if lang == "ar" and self.title_ar else self.title,
            "title_en": self.title,
            "facts": {f.field: f.value for f in self.facts},
            "approved_offers": [o.describe(lang) for o in self.offers],
            "not_available": self.missing_fields,
        }


class StrategyPlan(BaseModel):
    platform_order: list[Platform]
    angles: dict[str, str] = Field(default_factory=dict)          # platform -> message angle
    variation_angles: list[str] = Field(default_factory=list)     # one per variation
    sequence: list[str] = Field(default_factory=list)             # phases, no invented dates
    notes: list[str] = Field(default_factory=list)


class DraftVariation(BaseModel):
    platform: Platform
    language: Language
    angle: str = ""
    headline: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    cta: str = Field(min_length=1, max_length=200)
    hashtags: list[str] = Field(default_factory=list)
    email_subject: str | None = Field(default=None, max_length=200)

    def full_text(self) -> str:
        parts = [self.email_subject or "", self.headline, self.body, self.cta, " ".join(self.hashtags)]
        return "\n".join(p for p in parts if p)


class ClaimIssue(BaseModel):
    kind: str                         # price | discount | offer | schedule | duration | delivery_format |
                                      # certification | instructor | prerequisites | forbidden_claim | superlative
    span: str                         # the text that triggered the rule
    severity: Literal["block", "warn"]
    explanation: str


class DimensionScore(BaseModel):
    dimension: str
    score: float | None = Field(default=None, ge=0, le=1)   # None = not measurable by this method
    method: Literal["deterministic", "judge"] = "deterministic"
    reason: str = ""


class DraftEvaluation(BaseModel):
    scores: list[DimensionScore]
    passed: bool
    mean: float | None

    def score(self, dimension: str) -> float | None:
        return next((s.score for s in self.scores if s.dimension == dimension), None)
