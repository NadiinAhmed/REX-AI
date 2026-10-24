"""Repository for the `rfp_opportunities` table (Agent 2). The only code that reads or writes it.

Kept in its own file so the RFP work never conflicts with changes to the other repositories.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select

from src.db.database import Database
from src.db.models import RfpOpportunityRecord
from src.rfp.models import Opportunity, OpportunityStatus

# Statuses that are still "live" in the pipeline (a scan may refresh or expire them).
ACTIVE_STATUSES = (OpportunityStatus.FLAGGED.value, OpportunityStatus.MONITORING.value,
                   OpportunityStatus.REVIEWED.value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RfpRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ reads
    def get(self, opportunity_id: str) -> RfpOpportunityRecord | None:
        with self._db.session() as s:
            return s.get(RfpOpportunityRecord, opportunity_id.upper())

    def get_by_key(self, dedup_key: str) -> RfpOpportunityRecord | None:
        with self._db.session() as s:
            return s.scalar(select(RfpOpportunityRecord).where(RfpOpportunityRecord.dedup_key == dedup_key))

    def list(self, *, country: str | None = None, statuses: tuple[str, ...] | None = None) -> list[RfpOpportunityRecord]:
        """Soonest deadline first; unknown deadlines last."""
        q = select(RfpOpportunityRecord)
        if country:
            q = q.where(RfpOpportunityRecord.country == country)
        if statuses:
            q = q.where(RfpOpportunityRecord.status.in_(statuses))
        q = q.order_by(RfpOpportunityRecord.deadline.is_(None), RfpOpportunityRecord.deadline,
                       RfpOpportunityRecord.title)
        with self._db.session() as s:
            return list(s.scalars(q).all())

    def duplicate_index(self) -> list[tuple[str, str, str | None, str | None]]:
        """(dedup_key, title_norm, organization, link) of every stored row, for duplicate checks."""
        with self._db.session() as s:
            rows = s.execute(select(RfpOpportunityRecord.dedup_key, RfpOpportunityRecord.title_norm,
                                    RfpOpportunityRecord.issuing_organization,
                                    RfpOpportunityRecord.submission_link)).all()
        return [tuple(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._db.session() as s:
            rows = s.execute(select(RfpOpportunityRecord.status, func.count())
                             .group_by(RfpOpportunityRecord.status)).all()
        return {status: n for status, n in rows}

    # ------------------------------------------------------------------ writes
    def upsert(self, opp: Opportunity, status: OpportunityStatus, title_norm: str) -> bool:
        """Insert a new opportunity or refresh an existing one. Returns True if it was new.

        A refresh never overwrites a human decision (reviewed / dismissed).
        """
        with self._db.session() as s:
            row = s.scalar(select(RfpOpportunityRecord).where(RfpOpportunityRecord.dedup_key == opp.dedup_key))
            fields = dict(
                title=opp.title, title_norm=title_norm, issuing_organization=opp.issuing_organization,
                country=opp.country.value, deadline=opp.deadline, publication_date=opp.publication_date,
                categories=[c.value for c in opp.categories], description=opp.description,
                eligibility=opp.eligibility, submission_link=opp.submission_link, documents=opp.documents,
                source_name=opp.source_name, language=opp.language, summary=opp.summary,
                summary_mode=opp.summary_mode, fit=opp.fit.value if opp.fit else None,
                fit_reasons=opp.fit_reasons, matched_course_ids=opp.matched_course_ids,
                synthetic=opp.synthetic, last_seen_at=_now())
            if row is None:
                s.add(RfpOpportunityRecord(id=opp.opportunity_id, dedup_key=opp.dedup_key,
                                           status=status.value, **fields))
                return True
            for key, value in fields.items():
                setattr(row, key, value)
            if row.status == OpportunityStatus.EXPIRED.value:
                row.status = status.value       # deadline was extended
            return False

    def mark_alerted(self, opportunity_ids: list[str]) -> None:
        with self._db.session() as s:
            for oid in opportunity_ids:
                row = s.get(RfpOpportunityRecord, oid)
                if row is not None and row.alerted_at is None:
                    row.alerted_at = _now()

    def set_status(self, opportunity_id: str, status: OpportunityStatus) -> bool:
        with self._db.session() as s:
            row = s.get(RfpOpportunityRecord, opportunity_id.upper())
            if row is None:
                return False
            row.status = status.value
            return True

    def expire_past_deadlines(self, today: date) -> int:
        """Mark stored live opportunities whose deadline has passed. Returns how many changed."""
        with self._db.session() as s:
            rows = s.scalars(select(RfpOpportunityRecord).where(
                RfpOpportunityRecord.deadline < today,
                RfpOpportunityRecord.status.in_(ACTIVE_STATUSES))).all()
            for row in rows:
                row.status = OpportunityStatus.EXPIRED.value
            return len(rows)