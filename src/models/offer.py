"""Approved, dated promotional offers. SHARED by REX Assistant and the Marketing Agent.

Why a separate model instead of `Course.promotions` (free text):
- an offer must be APPROVED by staff before any agent may mention it;
- an offer must EXPIRE: outside [valid_from, valid_until] it is never used;
- an offer must be MEASURABLE (a percent or a price), so the claim checker can compare an ad's
  numbers with it.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator


class Offer(BaseModel):
    id: str
    course_id: str = Field(pattern=r"^[A-Z]{2,5}-\d{3}$")
    title_en: str = Field(min_length=3, max_length=200)
    title_ar: str | None = Field(default=None, max_length=200)
    discount_percent: float | None = Field(default=None, gt=0, le=100)
    price_amount: float | None = Field(default=None, ge=0)
    currency: str | None = None
    valid_from: date
    valid_until: date
    approval_status: str = "pending"          # pending | approved | rejected (ApprovalStatus values)
    proposed_by: str | None = None
    approved_by: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency must be a 3-letter ISO code such as EGP or SAR")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "Offer":
        if (self.discount_percent is None) == (self.price_amount is None):
            raise ValueError("an offer needs exactly one of discount_percent or price_amount")
        if self.price_amount is not None and self.currency is None:
            raise ValueError("a price offer needs a currency")
        if self.valid_until < self.valid_from:
            raise ValueError("valid_until must be on or after valid_from")
        return self

    def is_active(self, on: date) -> bool:
        return self.approval_status == "approved" and self.valid_from <= on <= self.valid_until

    def describe(self, lang: str = "en") -> str:
        title = (self.title_ar or self.title_en) if lang == "ar" else self.title_en
        value = (f"{self.discount_percent:g}%" if self.discount_percent is not None
                 else f"{self.price_amount:g} {self.currency}")
        until = "حتى" if lang == "ar" else "until"
        return f"{title}: {value} ({until} {self.valid_until.isoformat()})"
