"""Screening rules: which opportunities we keep, and what status a kept one gets.

Order matters and is fixed: market (strict) → training-related → not expired. The first rule
that fails decides the exclusion reason, so every removed listing has exactly one clear reason.
"""
from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher

from src.rfp.extraction import normalize_text
from src.rfp.models import Country, Excluded, ExclusionReason, Opportunity, OpportunityStatus

SUPPORTED_MARKETS = (Country.EGYPT, Country.SAUDI_ARABIA)
TITLE_SIMILARITY = 0.88      # titles at least this similar (same organization) are the same tender


def screen(opp: Opportunity, training_related: bool, today: date) -> Excluded | None:
    """Return why `opp` must be excluded, or None to keep it."""
    if not opp.title:
        return Excluded(title="(untitled)", reason=ExclusionReason.INVALID, detail="no title")
    if opp.country == Country.OTHER:
        return Excluded(title=opp.title, reason=ExclusionReason.OUTSIDE_MARKETS,
                        detail="only Egypt and Saudi Arabia are supported")
    if opp.country == Country.UNKNOWN:
        return Excluded(title=opp.title, reason=ExclusionReason.COUNTRY_UNKNOWN,
                        detail="could not confirm Egypt or Saudi Arabia")
    if not training_related:
        return Excluded(title=opp.title, reason=ExclusionReason.NOT_TRAINING,
                        detail="no education or training scope")
    if opp.deadline is not None and opp.deadline < today:
        return Excluded(title=opp.title, reason=ExclusionReason.EXPIRED,
                        detail=f"deadline {opp.deadline.isoformat()} has passed")
    return None


def initial_status(opp: Opportunity) -> OpportunityStatus:
    """Business rule: Egypt → flag for immediate review; Saudi Arabia → monitored pipeline."""
    return OpportunityStatus.FLAGGED if opp.country == Country.EGYPT else OpportunityStatus.MONITORING


# ----------------------------------------------------------------------------- duplicates
class DuplicateDetector:
    """Finds the same tender listed twice (re-posted, re-worded, or on two portals).

    Same tender = same submission link, OR same organization and near-identical title.
    It knows every stored opportunity, and learns each new one as the scan goes, so duplicates
    are caught both against the database and inside one batch.
    """

    def __init__(self, known: list[tuple[str, str, str | None, str | None]] | None = None) -> None:
        # (dedup_key, normalized title, organization, link)
        self._known: list[tuple[str, str, str, str]] = [
            (key, title_norm, normalize_text(org), (link or "").strip().lower())
            for key, title_norm, org, link in (known or [])]

    def match(self, opp: Opportunity) -> str | None:
        """dedup_key of the stored/seen opportunity this one duplicates, else None."""
        title = normalize_text(opp.title)
        org = normalize_text(opp.issuing_organization)
        link = (opp.submission_link or "").strip().lower()
        for key, k_title, k_org, k_link in self._known:
            if key == opp.dedup_key:
                return key
            if link and link == k_link:
                return key
            if org and org == k_org and SequenceMatcher(None, title, k_title).ratio() >= TITLE_SIMILARITY:
                return key
        return None

    def remember(self, opp: Opportunity) -> None:
        self._known.append((opp.dedup_key, normalize_text(opp.title),
                            normalize_text(opp.issuing_organization),
                            (opp.submission_link or "").strip().lower()))