"""Offers business rules. SHARED: REX Assistant, the Marketing Agent and the MCP server use this.

The one rule every agent relies on: `active_offers()` returns only offers that a named person
approved AND whose validity window includes today. Pending, rejected and expired offers are
invisible to the agents.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Protocol

from src.db.models import ApprovalStatus
from src.db.repositories import OfferRepository
from src.models.offer import Offer


class OfferError(ValueError):
    """Invalid offer data or an invalid approval action."""


class CourseLookup(Protocol):
    def get(self, course_id: str) -> object | None: ...


class OfferReader(Protocol):
    """What agents need: read active offers. Implemented directly or over MCP."""
    def active_offers(self, course_id: str, on: date | None = None) -> list[Offer]: ...


class OfferService:
    def __init__(self, offers: OfferRepository, courses: CourseLookup) -> None:
        self._offers = offers
        self._courses = courses

    def active_offers(self, course_id: str, on: date | None = None) -> list[Offer]:
        day = on or date.today()
        return [o for o in self._offers.list(course_id=course_id) if o.is_active(day)]

    def list_offers(self, course_id: str | None = None) -> list[Offer]:
        return self._offers.list(course_id=course_id)

    def propose(self, *, course_id: str, title_en: str, valid_from: date, valid_until: date,
                proposed_by: str, title_ar: str | None = None, discount_percent: float | None = None,
                price_amount: float | None = None, currency: str | None = None) -> str:
        """Create an offer in PENDING state. It is not used anywhere until someone approves it."""
        course_id = course_id.strip().upper()
        if self._courses.get(course_id) is None:
            raise OfferError(f"No approved course with id {course_id}.")
        if not proposed_by.strip():
            raise OfferError("proposed_by is required.")
        try:
            offer = Offer(id=str(uuid.uuid4()), course_id=course_id, title_en=title_en.strip(),
                          title_ar=(title_ar or "").strip() or None, discount_percent=discount_percent,
                          price_amount=price_amount, currency=currency, valid_from=valid_from,
                          valid_until=valid_until, approval_status=ApprovalStatus.PENDING.value,
                          proposed_by=proposed_by.strip())
        except ValueError as exc:      # pydantic ValidationError is a ValueError
            raise OfferError(str(exc)) from exc
        return self._offers.create(offer)

    def approve(self, offer_id: str, approver: str) -> None:
        self._decide(offer_id, approver, ApprovalStatus.APPROVED)

    def reject(self, offer_id: str, approver: str) -> None:
        self._decide(offer_id, approver, ApprovalStatus.REJECTED)

    def _decide(self, offer_id: str, approver: str, status: ApprovalStatus) -> None:
        if not approver.strip():
            raise OfferError("A reviewer name is required to approve or reject an offer.")
        if not self._offers.set_status(offer_id, status, approver.strip()):
            raise OfferError(f"No offer with id {offer_id}.")
