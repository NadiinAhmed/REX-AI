"""Human review of marketing drafts (brief requirement 42: manual approval before publishing).

Design rules
- Every draft starts as pending_review. Only a NAMED reviewer can approve or reject it.
- Approval re-checks the text against TODAY's approved data (not the generation-time snapshot):
  if an offer expired or a fee changed since the draft was written, approval is refused.
- Editing a draft re-runs the fact-checker and sends the draft back to pending_review.
- There is intentionally NO publish/send method in this codebase. "Approved" means a person may
  now copy the text and distribute it themselves. Automatic distribution is out of scope.
"""
from __future__ import annotations

from src.db.models import DraftStatus
from src.db.repositories import MarketingRepository
from src.marketing.claim_validator import ClaimValidator, has_blocking
from src.marketing.evaluator import MarketingEvaluator
from src.marketing.facts import CourseFactsProvider
from src.marketing.models import CampaignBrief, ClaimIssue, DraftVariation


class ApprovalError(ValueError):
    """The approval action is not allowed (unknown draft, missing reviewer, blocking issues)."""


class ApprovalService:
    def __init__(self, drafts: MarketingRepository, facts: CourseFactsProvider,
                 validator: ClaimValidator | None = None, evaluator: MarketingEvaluator | None = None) -> None:
        self._drafts = drafts
        self._facts = facts
        self._validator = validator or ClaimValidator()
        self._evaluator = evaluator or MarketingEvaluator(self._validator)

    def recheck(self, draft_id: str) -> list[ClaimIssue]:
        """Fact-check a draft against the CURRENT approved data."""
        draft, campaign = self._load(draft_id)
        facts = self._facts.facts_for(campaign.course_id)
        return self._validator.validate(DraftVariation.model_validate(draft.content).full_text(), facts)

    def edit(self, draft_id: str, content: DraftVariation, editor: str) -> list[ClaimIssue]:
        self._require_name(editor)
        draft, campaign = self._load(draft_id)
        if content.platform.value != draft.platform:
            raise ApprovalError("An edit cannot change the draft's platform.")
        facts = self._facts.facts_for(campaign.course_id)
        issues = self._validator.validate(content.full_text(), facts)
        evaluation = self._evaluator.evaluate(content, CampaignBrief.model_validate(campaign.brief), facts, issues)
        self._drafts.update_content(draft_id, content=content.model_dump(mode="json"),
                                    claim_issues=[i.model_dump() for i in issues],
                                    evaluation=evaluation.model_dump(mode="json"))
        return issues

    def approve(self, draft_id: str, reviewer: str, note: str | None = None) -> None:
        self._require_name(reviewer)
        draft, _ = self._load(draft_id)
        if draft.approval_status != DraftStatus.PENDING_REVIEW.value:
            raise ApprovalError(f"Only drafts pending review can be approved (this one is {draft.approval_status}).")
        issues = self.recheck(draft_id)
        if has_blocking(issues):
            kinds = ", ".join(sorted({i.kind for i in issues if i.severity == "block"}))
            raise ApprovalError(f"Cannot approve: the text makes claims the approved data does not support ({kinds}). "
                                "Edit the text, or fix the data first.")
        self._drafts.set_status(draft_id, DraftStatus.APPROVED, reviewer.strip(), note)

    def reject(self, draft_id: str, reviewer: str, note: str | None = None) -> None:
        self._require_name(reviewer)
        self._load(draft_id)
        self._drafts.set_status(draft_id, DraftStatus.REJECTED, reviewer.strip(), note)

    # ---------------------------------------------------------------- helpers
    def _load(self, draft_id: str):
        draft = self._drafts.get_draft(draft_id)
        if draft is None:
            raise ApprovalError(f"No draft with id {draft_id}.")
        campaign = self._drafts.get_campaign(draft.campaign_id)
        return draft, campaign

    @staticmethod
    def _require_name(name: str) -> None:
        if not name or not name.strip():
            raise ApprovalError("A reviewer name is required.")
