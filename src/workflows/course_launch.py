"""Course Launch workflow: one staff sentence -> curriculum -> catalog course -> offer -> e-mail campaign.

Three chat messages, each one a human decision:

    1. prepare(plan)          the curriculum is generated and put IN REVIEW. Nothing is live.
    2. approve(record, name)  a named person approves what they just saw:
                                curriculum approved -> the course is published to the catalog
                                -> fee stored -> offer proposed + approved -> e-mail draft generated
                              The e-mail is only DRAFTED here: nobody has read it yet.
    3. send(record, name)     the same kind of decision for the e-mail they have now read:
                                draft approved -> sent ONE e-mail per recipient.

Why this order: an offer and a campaign can only exist for a course in the catalog (OfferService and
the marketing fact loader refuse unknown courses), and a new course enters the catalog only when its
curriculum is approved. So the e-mail cannot be shown before step 2.

This class owns NO business rules of its own. Every rule (who may approve, fact-checking the e-mail
against today's data, dry-run mode, duplicate sends, delivery logs) stays in the existing services,
so the chat and the admin pages always behave the same.

Each step is recorded in `LaunchRecord.completed`. If a step fails, the steps before it stay done
and repeating the command continues from the failed step (nothing is created twice).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.curriculum.generator import CurriculumError
from src.curriculum.models import CurriculumRequest
from src.marketing.models import CampaignBrief, CampaignObjective, Platform
from src.utils.logging_config import get_logger
from src.workflows.launch_plan import LaunchPlan

logger = get_logger("workflows.course_launch")

Stage = Literal["awaiting_approval", "awaiting_send", "sent"]


class LaunchError(ValueError):
    """A launch step failed. The message is safe to show to staff."""


class LaunchRecord(BaseModel):
    """Where one launch stands. Kept in the conversation state; every object it points to
    (curriculum, course, offer, draft, deliveries) is a normal record in the shared database."""
    plan_id: str
    plan: LaunchPlan
    stage: Stage = "awaiting_approval"
    curriculum_id: str
    course_id: str | None = None
    offer_id: str | None = None
    campaign_id: str | None = None
    draft_id: str | None = None
    email: dict[str, Any] | None = None          # the drafted e-mail, as shown to staff
    approved_by: str | None = None
    completed: list[str] = Field(default_factory=list)
    sent_to: list[str] = Field(default_factory=list)


@dataclass
class SendReport:
    sent: list[str] = field(default_factory=list)        # delivered (or logged as a dry run)
    skipped: list[str] = field(default_factory=list)     # already sent earlier
    failed: dict[str, str] = field(default_factory=dict)  # address -> reason
    dry_run: bool = True


class CourseLaunchWorkflow:
    def __init__(self, *, curriculum, courses, offers, marketing, approvals, distribution, leads) -> None:
        self._curriculum = curriculum        # CurriculumService
        self._courses = courses              # CourseRepository (get, set_fee)
        self._offers = offers                # OfferService
        self._marketing = marketing          # MarketingAgent (create_campaign)
        self._approvals = approvals          # ApprovalService (marketing drafts)
        self._distribution = distribution    # DistributionService (the ONLY sender)
        self._leads = leads                  # LeadRepository (leads exist only with consent)

    # ------------------------------------------------------------------ 1. prepare
    def prepare(self, plan: LaunchPlan, actor: str = "launch-chat"):
        if plan.missing:
            raise LaunchError("The plan is incomplete: " + "; ".join(plan.missing))
        try:
            stored = self._curriculum.create(CurriculumRequest(
                title=plan.title, topics=plan.topics, skill_level=plan.skill_level,
                duration_hours=plan.duration_hours), actor=actor)
            stored = self._curriculum.submit(stored.id, actor, note="Course launch workflow")
        except CurriculumError as exc:
            raise LaunchError(f"The curriculum could not be prepared: {exc}") from exc
        record = LaunchRecord(plan_id=f"LP-{uuid.uuid4().hex[:6].upper()}", plan=plan,
                              curriculum_id=stored.id, completed=["curriculum_drafted"])
        logger.info("Launch %s prepared (curriculum %s)", record.plan_id, stored.id)
        return record, stored

    def curriculum(self, record: LaunchRecord):
        return self._curriculum.get(record.curriculum_id)

    # ------------------------------------------------------------------ 2. approve
    def approve(self, record: LaunchRecord, reviewer: str) -> LaunchRecord:
        reviewer = (reviewer or "").strip()
        if not reviewer:
            raise LaunchError("Approval needs your name: approve <plan id> as <your name>.")
        if record.stage != "awaiting_approval":
            raise LaunchError(f"{record.plan_id} is already approved.")
        r = record.model_copy(deep=True, update={"approved_by": reviewer})
        plan = r.plan
        try:
            if "curriculum_approved" not in r.completed:
                stored = self._curriculum.approve(r.curriculum_id, reviewer, note=f"Approved in launch {r.plan_id}")
                r.course_id = stored.curriculum.course_id
                if not r.course_id:
                    raise LaunchError("The curriculum was approved but no catalog course was created.")
                r.completed.append("curriculum_approved")
            if plan.fee_amount is not None and "fee_set" not in r.completed:
                self._courses.set_fee(r.course_id, plan.fee_amount, plan.fee_currency)
                r.completed.append("fee_set")
            if plan.offer_percent is not None and "offer_approved" not in r.completed:
                if r.offer_id is None:
                    r.offer_id = self._offers.propose(
                        course_id=r.course_id, title_en="Launch offer", title_ar="عرض الإطلاق",
                        discount_percent=plan.offer_percent, valid_from=date.today(),
                        valid_until=plan.offer_valid_until, proposed_by=reviewer)
                self._offers.approve(r.offer_id, reviewer)
                r.completed.append("offer_approved")
            if "email_drafted" not in r.completed:
                self._draft_email(r)
                r.completed.append("email_drafted")
        except LaunchError as exc:
            raise _Partial(r, str(exc)) from exc
        except (CurriculumError, ValueError) as exc:        # OfferError, OfferRepository and pydantic errors
            raise _Partial(r, str(exc)) from exc
        r.stage = "awaiting_send"
        logger.info("Launch %s approved by %s (course %s)", r.plan_id, reviewer, r.course_id)
        return r

    def _draft_email(self, r: LaunchRecord) -> None:
        response = self._marketing.create_campaign(CampaignBrief(
            course_id=r.course_id, platforms=[Platform.EMAIL], objective=CampaignObjective.ENROLLMENT,
            language=r.plan.language, variations=1))
        draft_ids = response.data.get("draft_ids") or []
        variations = response.data.get("variations") or []
        if not draft_ids or not variations:
            raise LaunchError("The e-mail draft could not be created: " + response.text[:300])
        r.campaign_id, r.draft_id, r.email = response.data.get("campaign_id"), draft_ids[0], variations[0]

    # ------------------------------------------------------------------ 3. send
    def recipients(self, record: LaunchRecord) -> list[str]:
        """Typed addresses + consented leads (a lead row exists only after consent), without duplicates.
        Closed leads asked not to be contacted again, so they are left out."""
        out = list(record.plan.recipients)
        if record.plan.email_leads:
            out += [lead.email.strip().lower() for lead in self._leads.list()
                    if lead.email and getattr(lead, "status", "new") != "closed"]
        return list(dict.fromkeys(out))

    def send(self, record: LaunchRecord, requester: str) -> tuple[LaunchRecord, SendReport]:
        if record.stage == "awaiting_approval":
            raise LaunchError(f"Approve {record.plan_id} first, then read the e-mail before sending it.")
        requester = (requester or "").strip()
        if not requester:
            raise LaunchError("Sending needs your name: send <plan id> as <your name>.")
        r = record.model_copy(deep=True)
        if "email_approved" not in r.completed:
            try:
                self._approvals.approve(r.draft_id, requester, note=f"Approved in launch {r.plan_id}")
            except ValueError as exc:                # ApprovalError: e.g. the text no longer matches the data
                raise LaunchError(f"The e-mail could not be approved: {exc}") from exc
            r.completed.append("email_approved")
        report = SendReport(dry_run=self._distribution.dry_run)
        addresses = self.recipients(r)
        if not addresses:
            raise LaunchError("There is nobody to send to: no typed addresses and no consented leads with an e-mail.")
        for address in addresses:
            if address in r.sent_to:
                report.skipped.append(address)
                continue
            try:      # one e-mail per person: recipients never see each other's addresses
                self._distribution.deliver(r.draft_id, "email", requested_by=requester, confirmed=True,
                                           recipients=address)
                r.sent_to.append(address)
                report.sent.append(address)
            except ValueError as exc:                # DistributionError
                report.failed[address] = str(exc)
        if not report.failed:
            r.stage = "sent"
        logger.info("Launch %s: %d sent, %d failed (dry_run=%s)", r.plan_id, len(report.sent),
                    len(report.failed), report.dry_run)
        return r, report


class _Partial(LaunchError):
    """A step failed after earlier steps succeeded. Carries the record with the completed steps,
    so retrying continues where it stopped instead of creating things twice."""
    def __init__(self, record: LaunchRecord, message: str) -> None:
        super().__init__(message)
        self.record = record


PartialLaunchError = _Partial
