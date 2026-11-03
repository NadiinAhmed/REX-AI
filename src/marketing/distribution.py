"""Sending APPROVED marketing drafts: e-mail drafts by e-mail, LinkedIn drafts to LinkedIn.

This is the ONLY module that sends marketing content. The Marketing Agent, the ApprovalService and
the MarketingRepository still cannot send anything (a test checks that).

Rules, checked in this order on every request (like a bank transfer):
  1. a named person asks, and ticks an explicit confirmation;
  2. the draft is APPROVED by a reviewer (pending or rejected drafts are refused);
  3. the channel fits the draft (e-mail drafts by e-mail, LinkedIn drafts to LinkedIn);
  4. its claims are re-checked against TODAY's approved data (an expired offer or a changed fee blocks);
  5. the same draft is not sent twice to the same target by accident;
  6. every attempt is written to `marketing_deliveries` (sent, failed or dry run).

MARKETING_DELIVERY_MODE=dry_run (the default) runs all the checks and records the attempt, but
nothing leaves the system. Set it to `live` to really send.

LinkedIn uses the official Posts API (POST /rest/posts) with an OAuth access token that has the
`w_member_social` permission ("Share on LinkedIn" product). The token and author URN come from
configuration only; they are never shown or logged.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from src.config import Settings
from src.db.models import DeliveryStatus, DraftStatus
from src.db.repositories import MarketingDeliveryRepository, MarketingRepository
from src.marketing.claim_validator import has_blocking
from src.marketing.models import DraftVariation, Platform
from src.services.email_service import EmailError, EmailService, parse_recipients
from src.utils.logging_config import get_logger

logger = get_logger("marketing.distribution")


class DistributionError(ValueError):
    """The request was refused before anything was sent (message is safe to show)."""


@dataclass(frozen=True)
class DeliveryOutcome:
    status: DeliveryStatus
    channel: str
    target: str
    message: str


class Channel(Protocol):
    name: str
    label: str

    def available(self) -> bool: ...

    def accepts(self, platform: Platform) -> bool:
        """Which drafts this channel can send (e-mail drafts by e-mail, LinkedIn posts to LinkedIn)."""

    def target_for(self, recipients: str) -> str:
        """Validated, privacy-safe description of where the message goes (stored in the log)."""

    def deliver(self, draft: DraftVariation, recipients: str) -> None:
        """Send. Raises DistributionError if the channel refuses."""


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


class EmailChannel:
    """Re-uses the Document Agent's EmailService (shared SMTP settings, no second e-mail client)."""
    name, label = "email", "E-mail"

    def __init__(self, email: EmailService) -> None:
        self._email = email

    def available(self) -> bool:
        return self._email.enabled

    def accepts(self, platform: Platform) -> bool:
        return platform == Platform.EMAIL

    def target_for(self, recipients: str) -> str:
        try:
            addresses = parse_recipients(recipients)
        except EmailError as exc:
            raise DistributionError(str(exc)) from exc
        hint = ", ".join(_mask(a) for a in addresses[:2]) + ("…" if len(addresses) > 2 else "")
        return f"{len(addresses)} recipient(s): {hint}"

    def deliver(self, draft: DraftVariation, recipients: str) -> None:
        body = "\n\n".join(p for p in (draft.headline, draft.body, draft.cta) if p)
        try:
            self._email.send(recipients, draft.email_subject or draft.headline, body)
        except EmailError as exc:
            raise DistributionError(str(exc)) from exc


_LITTLE_TEXT_RESERVED = set("\\|{}@[]()<>#*_~")


def escape_little_text(text: str) -> str:
    """LinkedIn's `commentary` field uses "little text": these characters must be escaped with a
    backslash, otherwise the post is rejected or cut ("#tag" is then posted as plain text)."""
    return "".join(f"\\{ch}" if ch in _LITTLE_TEXT_RESERVED else ch for ch in text)


def default_linkedin_version(today: date | None = None) -> str:
    """LinkedIn API versions are YYYYMM and each stays active for about a year. Two months back
    from today is always an active one. LINKEDIN_API_VERSION overrides it."""
    d = today or date.today()
    month = d.month - 2
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year}{month:02d}"


class LinkedInChannel:
    """Publishes a LinkedIn draft as a public post on the configured member or organization."""
    name, label = "linkedin", "LinkedIn"
    MAX_CHARS = 3000                                     # LinkedIn's limit for post text

    def __init__(self, access_token: str | None, author_urn: str | None, api_version: str | None = None,
                 api_base: str = "https://api.linkedin.com", timeout: float = 20,
                 opener: Callable[..., object] | None = None) -> None:
        self._token, self._author = access_token, (author_urn or "").strip()
        self._version = api_version or default_linkedin_version()
        self._base, self._timeout = api_base.rstrip("/"), timeout
        self._open = opener or urllib.request.urlopen
        self.last_post_id: str | None = None

    def available(self) -> bool:
        return bool(self._token) and self._author.startswith(("urn:li:person:", "urn:li:organization:"))

    def accepts(self, platform: Platform) -> bool:
        return platform == Platform.LINKEDIN

    def target_for(self, recipients: str) -> str:
        kind = "company page" if self._author.startswith("urn:li:organization:") else "profile"
        return f"LinkedIn {kind}"                          # never store the URN or the token

    def deliver(self, draft: DraftVariation, recipients: str) -> None:
        text = "\n\n".join(p for p in (draft.headline, draft.body, draft.cta, " ".join(draft.hashtags)) if p)
        if len(text) > self.MAX_CHARS:
            raise DistributionError(f"The post is {len(text)} characters; LinkedIn allows {self.MAX_CHARS}.")
        payload = {"author": self._author, "commentary": escape_little_text(text), "visibility": "PUBLIC",
                   "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                    "thirdPartyDistributionChannels": []},
                   "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        request = urllib.request.Request(
            f"{self._base}/rest/posts", data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
                     "LinkedIn-Version": self._version, "X-Restli-Protocol-Version": "2.0.0"})
        try:
            with self._open(request, timeout=self._timeout) as response:   # type: ignore[attr-defined]
                status = int(getattr(response, "status", 201))
                self.last_post_id = response.headers.get("x-restli-id")
        except urllib.error.HTTPError as exc:
            raise DistributionError(_linkedin_error(exc.code)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DistributionError("LinkedIn could not be reached. Check the internet connection.") from exc
        if status not in (200, 201):
            raise DistributionError(_linkedin_error(status))


def _linkedin_error(code: int) -> str:
    return {
        401: "LinkedIn refused the access token (HTTP 401): it is wrong or expired. Generate a new one.",
        403: "LinkedIn refused the post (HTTP 403): the token needs the w_member_social permission "
             "(\"Share on LinkedIn\"), or the author URN does not belong to this token.",
        422: "LinkedIn rejected the post content (HTTP 422).",
        426: "LinkedIn no longer accepts this API version (HTTP 426): set LINKEDIN_API_VERSION to a recent YYYYMM.",
        429: "LinkedIn rate limit reached (HTTP 429). Try again later.",
    }.get(code, f"LinkedIn refused the post (HTTP {code}).")


class DistributionService:
    def __init__(self, settings: Settings, drafts: MarketingRepository, deliveries: MarketingDeliveryRepository,
                 recheck: Callable[[str], list], channels: list[Channel]) -> None:
        self._dry_run = settings.marketing_delivery_mode != "live"
        self._drafts, self._deliveries, self._recheck = drafts, deliveries, recheck
        self._channels = {c.name: c for c in channels}

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def channels(self, platform: Platform | None = None) -> list[Channel]:
        """Channels the UI may offer for a draft. In dry-run mode they can be tried without credentials."""
        return [c for c in self._channels.values() if (self._dry_run or c.available())
                and (platform is None or c.accepts(platform))]

    def history(self, draft_id: str):
        return self._deliveries.list_for_draft(draft_id)

    def deliver(self, draft_id: str, channel_name: str, *, requested_by: str, confirmed: bool,
                recipients: str = "", allow_repeat: bool = False) -> DeliveryOutcome:
        if not requested_by or not requested_by.strip():
            raise DistributionError("Enter your name: every delivery is logged with the person who requested it.")
        if not confirmed:
            raise DistributionError("Tick the confirmation box first: this sends the text outside the academy.")
        channel = self._channels.get(channel_name)
        if channel is None:
            raise DistributionError(f"Unknown channel '{channel_name}'.")
        if not self._dry_run and not channel.available():
            raise DistributionError(f"{channel.label} is not configured (see .env.example).")
        record = self._drafts.get_draft(draft_id)
        if record is None:
            raise DistributionError(f"No draft with id {draft_id}.")
        if record.approval_status != DraftStatus.APPROVED.value:
            raise DistributionError(f"Only approved drafts can be sent (this one is {record.approval_status}).")
        issues = self._recheck(draft_id)
        if has_blocking(issues):
            kinds = ", ".join(sorted({i.kind for i in issues if i.severity == "block"}))
            raise DistributionError(f"Not sent: the text no longer matches today's approved data ({kinds}). "
                                    "Edit and re-approve it.")
        draft = DraftVariation.model_validate(record.content)
        if not channel.accepts(draft.platform):
            raise DistributionError(f"{channel.label} cannot send a {draft.platform.value} draft: e-mail drafts go "
                                    "by e-mail, LinkedIn drafts to LinkedIn.")
        target = channel.target_for(recipients)
        if not allow_repeat and self._deliveries.already_sent(draft_id, channel_name, target):
            raise DistributionError("This draft was already sent to this target. Tick 'send again' to repeat it.")
        who = requested_by.strip()

        if self._dry_run:
            self._deliveries.record(draft_id=draft_id, channel=channel_name, target=target,
                                    status=DeliveryStatus.DRY_RUN, requested_by=who)
            return DeliveryOutcome(DeliveryStatus.DRY_RUN, channel_name, target,
                                   "Dry run: all checks passed and the attempt is logged, but nothing was sent "
                                   "(MARKETING_DELIVERY_MODE=dry_run).")
        try:
            channel.deliver(draft, recipients)
        except DistributionError as exc:
            self._deliveries.record(draft_id=draft_id, channel=channel_name, target=target,
                                    status=DeliveryStatus.FAILED, requested_by=who, error=str(exc))
            logger.warning("Marketing delivery failed on %s", channel_name)
            raise
        self._deliveries.record(draft_id=draft_id, channel=channel_name, target=target,
                                status=DeliveryStatus.SENT, requested_by=who)
        logger.info("Marketing draft delivered on %s", channel_name)
        post_id = getattr(channel, "last_post_id", None)
        return DeliveryOutcome(DeliveryStatus.SENT, channel_name, target,
                               f"Sent via {channel.label}." + (f" Post id: {post_id}" if post_id else ""))
