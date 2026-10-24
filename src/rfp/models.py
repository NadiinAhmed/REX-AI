"""Data shapes for the RFP agent.

`RawTender`  = what a source gives us (free text, any date format, may be incomplete).
`Opportunity` = the cleaned, validated record we screen, store and show.
Unknown values stay None / empty: we never guess a deadline, a country or a requirement.
"""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class Country(str, Enum):
    EGYPT = "EG"
    SAUDI_ARABIA = "SA"
    OTHER = "OTHER"        # a country outside the supported markets
    UNKNOWN = "UNKNOWN"    # we could not confirm the country

    @property
    def label(self) -> str:
        return {"EG": "Egypt", "SA": "Saudi Arabia", "OTHER": "Other", "UNKNOWN": "Unknown"}[self.value]


class Category(str, Enum):
    EDUCATION_TRAINING = "education_training"
    TECHNICAL_TRAINING = "technical_training"
    PROFESSIONAL_DEVELOPMENT = "professional_development"
    DIGITAL_TRANSFORMATION = "digital_transformation"
    SOFTWARE_IT_TRAINING = "software_it_training"
    CORPORATE_LEARNING = "corporate_learning"

    @property
    def label(self) -> str:
        return {"education_training": "Education & training", "technical_training": "Technical training",
                "professional_development": "Professional development",
                "digital_transformation": "Digital transformation",
                "software_it_training": "Software & IT training",
                "corporate_learning": "Corporate learning"}[self.value]


class OpportunityStatus(str, Enum):
    FLAGGED = "flagged_for_review"   # Egypt: needs a human look now (business rule)
    MONITORING = "monitoring"        # Saudi Arabia: kept in the monitored pipeline
    REVIEWED = "reviewed"            # a person looked at it
    DISMISSED = "dismissed"          # a person decided not to pursue it
    EXPIRED = "expired"              # deadline passed on a later scan


class Fit(str, Enum):
    """Our honest guess whether the academy MAY qualify. Never a guarantee."""
    STRONG = "strong"
    POSSIBLE = "possible"
    WEAK = "weak"


class ExclusionReason(str, Enum):
    OUTSIDE_MARKETS = "outside_markets"      # not Egypt / Saudi Arabia (strict rule)
    COUNTRY_UNKNOWN = "country_unknown"      # cannot confirm Egypt / Saudi Arabia
    NOT_TRAINING = "not_training_related"
    EXPIRED = "expired"
    DUPLICATE = "duplicate"
    INVALID = "invalid_record"               # e.g. no title


class RawTender(BaseModel):
    """One listing exactly as a source provides it. Everything except the title is optional."""
    title: str = ""
    organization: str | None = None
    country: str | None = None
    deadline: str | None = None
    published: str | None = None
    description: str = ""
    eligibility: str | None = None
    category: str | None = None
    link: str | None = None
    documents: list[str] = Field(default_factory=list)
    source: str = "unknown"


class Opportunity(BaseModel):
    title: str
    issuing_organization: str | None = None
    country: Country
    deadline: date | None = None
    publication_date: date | None = None
    description: str = ""
    eligibility: str | None = None
    categories: list[Category] = Field(default_factory=list)
    submission_link: str | None = None
    documents: list[str] = Field(default_factory=list)
    source_name: str = "unknown"
    language: str = "en"
    synthetic: bool = False
    # filled by later pipeline steps
    dedup_key: str = ""
    fit: Fit | None = None
    fit_reasons: list[str] = Field(default_factory=list)
    matched_course_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    summary_mode: str = "deterministic"     # "deterministic" | "llm"

    @property
    def opportunity_id(self) -> str:
        return f"RFP-{self.dedup_key[:6].upper()}"

    def missing_fields(self) -> list[str]:
        names = {"issuing_organization": self.issuing_organization, "deadline": self.deadline,
                 "publication_date": self.publication_date, "eligibility": self.eligibility,
                 "submission_link": self.submission_link}
        return [k for k, v in names.items() if not v]


class Excluded(BaseModel):
    """A listing the screen removed, with the reason (shown in the scan report)."""
    title: str
    reason: ExclusionReason
    detail: str = ""


class ScanReport(BaseModel):
    """What one scan did. Returned to the agent and the dashboard."""
    fetched: int = 0
    new: list[str] = Field(default_factory=list)          # opportunity ids stored for the first time
    updated: list[str] = Field(default_factory=list)      # seen before, refreshed
    alerts: list[str] = Field(default_factory=list)       # new Egypt opportunities: review now
    excluded: list[Excluded] = Field(default_factory=list)
    expired_marked: int = 0                                 # stored ones whose deadline has now passed
    source_notices: list[str] = Field(default_factory=list)
    summary_mode: str = "deterministic"

    def excluded_count(self, reason: ExclusionReason) -> int:
        return sum(e.reason == reason for e in self.excluded)