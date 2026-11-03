"""Sending APPROVED marketing drafts (src/marketing/distribution.py): e-mail and LinkedIn.

LinkedIn tests use a real local HTTP server that imitates POST /rest/posts (same path, required
headers, 201 + x-restli-id), so the request really goes over the network stack. The real LinkedIn
API cannot be called from tests. E-mail uses the EmailService's injectable transport.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.bootstrap import build_app
from src.config import load_settings
from src.db.models import DeliveryStatus
from src.db.repositories import MarketingDeliveryRepository
from src.marketing.distribution import (
    DistributionError, DistributionService, EmailChannel, LinkedInChannel, escape_little_text,
)
from src.marketing.models import CampaignBrief, CampaignObjective, ClaimIssue, Platform
from src.services.email_service import EmailService
from tests.conftest import OFFICIAL_DATASET


# ---------------------------------------------------------------- fixtures
class _Hook(BaseHTTPRequestHandler):
    """Imitates LinkedIn's Posts API."""
    received: list = []
    headers_seen: list = []
    status = 201

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", 0))
        _Hook.received.append(json.loads(self.rfile.read(length)))
        _Hook.headers_seen.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
        self.send_response(_Hook.status)
        if _Hook.status == 201:
            self.send_header("x-restli-id", "urn:li:share:7000000000000000001")
        self.end_headers()

    def log_message(self, *args):   # keep test output clean
        pass


@pytest.fixture
def linkedin_api():
    _Hook.received, _Hook.headers_seen, _Hook.status = [], [], 201
    server = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def ctx(test_env):
    return build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))))


def approved_draft(ctx, platform=Platform.LINKEDIN) -> str:
    brief = CampaignBrief(course_id="TRK-001", platforms=[platform], objective=CampaignObjective.ENROLLMENT,
                          variations=1)
    draft_id = ctx.registry.get("marketing").create_campaign(brief).data["draft_ids"][0]
    ctx.approvals.approve(draft_id, "Marketing Manager")
    return draft_id


def service(ctx, test_env, *, live=True, api=None, token="test-token", sent=None, recheck=None):
    settings = load_settings(test_env(MARKETING_DELIVERY_MODE="live" if live else "dry_run",
                                      SMTP_HOST="smtp.test", SMTP_FROM="academy@example.com"))
    channels = [EmailChannel(EmailService(settings, transport=(sent.append if sent is not None else None))),
                LinkedInChannel(token, "urn:li:person:abc123", "202609", api_base=api or "http://127.0.0.1:9")]
    return DistributionService(settings, ctx.marketing, MarketingDeliveryRepository(ctx.database),
                               recheck or ctx.approvals.recheck, channels)


# ---------------------------------------------------------------- safety rules
def test_default_mode_is_dry_run_and_sends_nothing(ctx, linkedin_api):
    assert ctx.distribution.dry_run is True                     # the app default
    draft_id = approved_draft(ctx)
    out = ctx.distribution.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    assert out.status == DeliveryStatus.DRY_RUN and _Hook.received == []
    assert [h.status for h in ctx.distribution.history(draft_id)] == ["dry_run"]


def test_only_approved_drafts_can_be_sent(ctx, test_env, linkedin_api):
    brief = CampaignBrief(course_id="TRK-001", platforms=[Platform.LINKEDIN], objective=CampaignObjective.ENROLLMENT,
                          variations=2)
    pending, rejected = ctx.registry.get("marketing").create_campaign(brief).data["draft_ids"][:2]
    ctx.approvals.reject(rejected, "Marketing Manager", "off-brand")
    svc = service(ctx, test_env, api=linkedin_api)
    for draft_id, status in ((pending, "pending_review"), (rejected, "rejected")):
        with pytest.raises(DistributionError, match=f"Only approved drafts.*{status}"):
            svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    assert _Hook.received == []


@pytest.mark.parametrize("who, confirmed, error", [("", True, "Enter your name"), ("Nadin", False, "confirmation")])
def test_name_and_confirmation_are_required(ctx, test_env, linkedin_api, who, confirmed, error):
    svc = service(ctx, test_env, api=linkedin_api)
    with pytest.raises(DistributionError, match=error):
        svc.deliver(approved_draft(ctx), "linkedin", requested_by=who, confirmed=confirmed)
    assert _Hook.received == []


def test_facts_are_rechecked_before_sending(ctx, test_env, linkedin_api):
    blocking = [ClaimIssue(kind="offer", span="20% off", severity="block", explanation="offer expired")]
    svc = service(ctx, test_env, api=linkedin_api, recheck=lambda _id: blocking)
    draft_id = approved_draft(ctx)
    with pytest.raises(DistributionError, match="today's approved data.*offer"):
        svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    assert _Hook.received == [] and svc.history(draft_id) == []


def test_live_mode_refuses_an_unconfigured_channel(ctx, test_env):
    svc = service(ctx, test_env, token=None)
    with pytest.raises(DistributionError, match="not configured"):
        svc.deliver(approved_draft(ctx), "linkedin", requested_by="Nadin", confirmed=True)


# ---------------------------------------------------------------- LinkedIn (local server imitating the API)
def test_linkedin_post_uses_the_posts_api_correctly(ctx, test_env, linkedin_api):
    svc = service(ctx, test_env, api=linkedin_api)
    draft_id = approved_draft(ctx)
    out = svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    assert out.status == DeliveryStatus.SENT and "urn:li:share:7000000000000000001" in out.message
    body, headers = _Hook.received[0], _Hook.headers_seen[0]
    assert headers["path"] == "/rest/posts"
    assert headers["authorization"] == "Bearer test-token"
    assert headers["linkedin-version"] == "202609" and headers["x-restli-protocol-version"] == "2.0.0"
    draft = ctx.marketing.get_draft(draft_id).content
    assert body["author"] == "urn:li:person:abc123" and body["visibility"] == "PUBLIC"
    assert body["lifecycleState"] == "PUBLISHED" and body["distribution"]["feedDistribution"] == "MAIN_FEED"
    assert escape_little_text(draft["headline"]) in body["commentary"]
    log = svc.history(draft_id)[0]
    assert (log.status, log.channel, log.target, log.requested_by) == ("sent", "linkedin", "LinkedIn profile", "Nadin")
    assert "test-token" not in log.target and "abc123" not in log.target      # secrets never logged


def test_reserved_characters_are_escaped_for_linkedin():
    assert escape_little_text("Join (now) #QA @academy 50% off_") == "Join \\(now\\) \\#QA \\@academy 50% off\\_"


def test_duplicate_send_is_blocked_unless_asked(ctx, test_env, linkedin_api):
    svc = service(ctx, test_env, api=linkedin_api)
    draft_id = approved_draft(ctx)
    svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    with pytest.raises(DistributionError, match="already sent"):
        svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True, allow_repeat=True)
    assert len(_Hook.received) == 2


@pytest.mark.parametrize("status, error", [(401, "wrong or expired"), (403, "w_member_social"),
                                           (426, "LINKEDIN_API_VERSION")])
def test_linkedin_errors_are_explained_and_logged(ctx, test_env, linkedin_api, status, error):
    _Hook.status = status
    svc = service(ctx, test_env, api=linkedin_api)
    draft_id = approved_draft(ctx)
    with pytest.raises(DistributionError, match=error):
        svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    log = svc.history(draft_id)[0]
    assert log.status == "failed" and f"HTTP {status}" in log.error


def test_unreachable_linkedin_is_logged_as_failed(ctx, test_env):
    svc = service(ctx, test_env)                      # api_base points at a closed port
    draft_id = approved_draft(ctx)
    with pytest.raises(DistributionError, match="could not be reached"):
        svc.deliver(draft_id, "linkedin", requested_by="Nadin", confirmed=True)
    assert svc.history(draft_id)[0].status == "failed"


def test_email_draft_cannot_go_to_linkedin(ctx, test_env, linkedin_api):
    svc = service(ctx, test_env, api=linkedin_api)
    with pytest.raises(DistributionError, match="cannot send a email draft"):
        svc.deliver(approved_draft(ctx, Platform.EMAIL), "linkedin", requested_by="Nadin", confirmed=True)
    assert _Hook.received == []


def test_ui_offers_only_the_matching_channel(ctx, test_env):
    svc = service(ctx, test_env, live=False)
    assert [c.name for c in svc.channels(Platform.LINKEDIN)] == ["linkedin"]
    assert [c.name for c in svc.channels(Platform.EMAIL)] == ["email"]
    assert svc.channels(Platform.INSTAGRAM) == []                     # no channel: copy by hand


# ---------------------------------------------------------------- e-mail (shared EmailService)
def test_email_draft_is_sent_and_recipients_are_masked_in_the_log(ctx, test_env):
    sent = []
    svc = service(ctx, test_env, sent=sent)
    draft_id = approved_draft(ctx, Platform.EMAIL)
    svc.deliver(draft_id, "email", requested_by="Nadin", confirmed=True,
                recipients="student.one@example.com, student.two@example.com")
    msg = sent[0]
    draft = ctx.marketing.get_draft(draft_id).content
    assert msg["Subject"] == draft["email_subject"] and draft["body"] in msg.get_content()
    log = svc.history(draft_id)[0]
    assert log.status == "sent" and log.target.startswith("2 recipient(s)")
    assert "student.one@example.com" not in log.target and "s***@example.com" in log.target


def test_social_post_cannot_be_sent_by_email(ctx, test_env):
    svc = service(ctx, test_env, sent=[])
    with pytest.raises(DistributionError, match="cannot send a linkedin draft"):
        svc.deliver(approved_draft(ctx), "email", requested_by="Nadin", confirmed=True, recipients="a@example.com")


def test_invalid_recipients_are_refused_before_sending(ctx, test_env):
    sent = []
    svc = service(ctx, test_env, sent=sent)
    with pytest.raises(DistributionError, match="not valid"):
        svc.deliver(approved_draft(ctx, Platform.EMAIL), "email", requested_by="Nadin", confirmed=True,
                    recipients="not-an-address")
    assert sent == []
