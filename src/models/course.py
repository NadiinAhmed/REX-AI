"""Validated course schema. SHARED INTERFACE (owned by Role 2).

Rule: unknown values are stored as None (or empty lists), never guessed.
Use `Course.missing_fields()` to report what is unavailable.
"""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SkillLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    ALL_LEVELS = "all_levels"
    UNKNOWN = "unknown"


class DeliveryFormat(str, Enum):
    ONLINE = "online"
    IN_PERSON = "in_person"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    SYNTHETIC = "synthetic"          # invented demo data
    OFFICIAL = "official"            # confirmed by the academy
    WEBSITE = "website"              # copied from a public page, not yet confirmed


class SourceMetadata(BaseModel):
    source_type: SourceType
    reference: str = Field(min_length=1)
    last_updated: date | None = None
    verified: bool = False

    @property
    def is_synthetic(self) -> bool:
        return self.source_type == SourceType.SYNTHETIC


class Fee(BaseModel):
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency must be a 3-letter ISO code such as EGP or SAR")
        return v

    @property
    def is_known(self) -> bool:
        return self.amount is not None and self.currency is not None


class Course(BaseModel):
    course_id: str = Field(pattern=r"^[A-Z]{2,5}-\d{3}$")
    title: str = Field(min_length=3)
    title_ar: str | None = None
    department: str | None = None
    description: str | None = None
    target_audience: list[str] = Field(default_factory=list)
    skill_level: SkillLevel = SkillLevel.UNKNOWN
    industry: str | None = None
    duration_hours: float | None = Field(default=None, gt=0)
    duration_text: str | None = None
    delivery_format: DeliveryFormat = DeliveryFormat.UNKNOWN
    prerequisites: list[str] = Field(default_factory=list)
    learning_outcomes: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    career_paths: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    fee: Fee = Field(default_factory=Fee)
    schedule: str | None = None
    promotions: str | None = None
    instructor: str | None = None
    country: str | None = None
    source: SourceMetadata

    _OPTIONAL_FIELDS = (
        "department", "description", "target_audience", "industry", "duration_hours",
        "prerequisites", "learning_outcomes", "modules", "schedule", "promotions",
        "instructor", "country",
    )

    def missing_fields(self) -> list[str]:
        missing = [f for f in self._OPTIONAL_FIELDS if getattr(self, f) in (None, [], "")]
        if not self.fee.is_known:
            missing.append("fee")
        if self.skill_level == SkillLevel.UNKNOWN:
            missing.append("skill_level")
        if self.delivery_format == DeliveryFormat.UNKNOWN:
            missing.append("delivery_format")
        return missing


class DatasetInfo(BaseModel):
    name: str
    version: str
    is_synthetic: bool
    notice: str


class CourseCatalog(BaseModel):
    dataset: DatasetInfo
    courses: list[Course] = Field(min_length=1)

    @field_validator("courses")
    @classmethod
    def _unique_ids(cls, courses: list[Course]) -> list[Course]:
        seen: set[str] = set()
        for c in courses:
            if c.course_id in seen:
                raise ValueError(f"duplicate course_id '{c.course_id}'")
            seen.add(c.course_id)
        return courses
