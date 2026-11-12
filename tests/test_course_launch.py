"""Course Launch workflow end to end, through the orchestrator like the chat UI.

One sentence -> curriculum in review; "approve LP-… as <name>" -> course in the catalog with fee,
offer active, e-mail drafted and shown; "send LP-…" -> e-mail approved, one delivery per recipient.
E-mail runs in dry-run mode (the default): every check runs, nothing leaves the system.
"""
from datetime import datetime, timezone

import pytest

from src.agents.base import AgentRequest
from src.agents.orchestrator import ALL_AGENTS, PUBLIC_AGENTS
from src.bootstrap import build_app
from src.config import load_settings
from tests.conftest import OFFICIAL_DATASET

SENTENCE = ('Launch a new course "Cloud Computing" covering Linux, Docker and AWS, for beginners, 60 hours, '
            'fee 4000 EGP, 20% early-bird until Dec 15, email it to ali@example.com and sara@example.com')


@pytest.fixture
def ctx(test_env):
    return build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))),
                     use_configured_llm=False, enable_rag=False)


class Chat:
    def __init__(self, ctx, staff=True):
        self.ctx, self.staff, self.state = ctx, staff, {}

    def say(self, text):
        r = self.ctx.orchestrator.handle(AgentRequest.from_text(text, self.state),
                                         allowed_agents=ALL_AGENTS if self.staff else PUBLIC_AGENTS)
        self.state = r.state
        return r


def launch(chat, text=SENTENCE):
    r = chat.say(text)
    assert r.agent_name == "course_launch" and r.data["intent"] == "launch_prepared", r.text
    return r.data["launch"]["plan_id"]


def add_lead(ctx, email, status=None):
    lead_id = ctx.leads.create(name="Lead", email=email, phone=None, language="en", profile={},
                               interested_course_ids=[], consent_given_at=datetime.now(timezone.utc),
                               consent_version="v1")
    if status:
        from src.db.models import LeadRecord
        with ctx.database.session() as s:
            s.get(LeadRecord, lead_id).status = status


# ------------------------------------------------------------------ the happy path
def test_full_launch_in_three_messages(ctx):
    chat = Chat(ctx)
    pid = launch(chat)
    assert ctx.courses.get("TRK-006") is None                       # nothing is live after message 1

    r = chat.say(f"approve {pid} as Nadin")
    assert r.data["intent"] == "launch_approved", r.text
    course = ctx.courses.get("TRK-006")
    assert course.title == "Cloud Computing" and (course.fee.amount, course.fee.currency) == (4000, "EGP")
    assert [o.discount_percent for o in ctx.offers.active_offers("TRK-006")] == [20]
    draft_id = r.data["launch"]["draft_id"]
    assert ctx.marketing.get_draft(draft_id).approval_status == "pending_review"   # shown, not yet approved
    assert "20%" in r.text and "send " + pid in r.text

    r = chat.say(f"send {pid}")
    assert r.data["intent"] == "launch_sent" and "dry run" in r.text, r.text
    assert ctx.marketing.get_draft(draft_id).approval_status == "approved"
    targets = [d.target for d in ctx.distribution.history(draft_id)]
    assert len(targets) == 2 and all(t.startswith("1 recipient") for t in targets)   # one e-mail per person


def test_students_see_the_new_course_fee_and_offer_immediately(ctx):
    chat = Chat(ctx)
    pid = launch(chat)
    chat.say(f"approve {pid} as Nadin")
    r = Chat(ctx, staff=False).say("Do you have a cloud computing course? how much is it?")
    assert r.agent_name == "rex_assistant" and "TRK-006" in r.text and "4000 EGP" in r.text and "20%" in r.text


def test_arabic_launch(ctx):
    chat = Chat(ctx)
    r = chat.say("أطلق كورس جديد اسمه الحوسبة السحابية للمبتدئين، المحاور: لينكس، دوكر و AWS، ٦٠ ساعة، "
                 "الرسوم ٤٠٠٠ جنيه، خصم ٢٠٪ حتى ١٥ ديسمبر، ابعت ايميل إلى ali@example.com")
    assert r.data["intent"] == "launch_prepared" and "خطة الإطلاق" in r.text, r.text
    pid = r.data["launch"]["plan_id"]
    r = chat.say(f"اعتمد {pid} باسم نادين")
    assert r.data["intent"] == "launch_approved" and r.data["launch"]["approved_by"] == "نادين", r.text
    assert chat.say(f"ارسل {pid}").data["intent"] == "launch_sent"


# ------------------------------------------------------------------ safety rules
def test_students_cannot_launch(ctx):
    r = Chat(ctx, staff=False).say(SENTENCE)
    assert r.agent_name != "course_launch" and ctx.courses.get("TRK-006") is None


def test_missing_details_are_asked_then_merged(ctx):
    chat = Chat(ctx)
    r = chat.say('Launch a new course "Cloud Computing" covering Linux and Docker')
    assert r.data["intent"] == "launch_needs_info" and "receives" in r.text
    r = chat.say("email our leads and ali@example.com")          # stays with the launch agent
    assert r.agent_name == "course_launch" and r.data["intent"] == "launch_prepared", r.text


def test_approval_needs_a_name_and_the_right_plan(ctx):
    chat = Chat(ctx)
    pid = launch(chat)
    assert chat.say(f"approve {pid}").data["intent"] == "approve_needs_name"
    assert chat.say("approve LP-000000 as Nadin").data["intent"] == "unknown_plan"
    assert ctx.courses.get("TRK-006") is None


def test_cannot_send_before_approval(ctx):
    chat = Chat(ctx)
    pid = launch(chat)
    r = chat.say(f"send {pid}")
    assert r.data["intent"] == "send_failed" and "Approve" in r.text


def test_consented_leads_are_included_and_closed_leads_are_not(ctx):
    add_lead(ctx, "mona@example.com")
    add_lead(ctx, "omar@example.com", status="closed")
    chat = Chat(ctx)
    pid = launch(chat, 'Launch a new course "Cloud Computing" covering Linux and Docker, email our leads')
    r = chat.say(f"approve {pid} as Nadin")
    assert "1 recipient" in r.text
    chat.say(f"send {pid}")
    assert len(ctx.distribution.history(r.data["launch"]["draft_id"])) == 1


def test_a_failed_step_is_resumed_without_creating_anything_twice(ctx, monkeypatch):
    chat = Chat(ctx)
    pid = launch(chat)
    real_approve = ctx.offers.approve
    monkeypatch.setattr(ctx.offers, "approve", lambda *a, **k: (_ for _ in ()).throw(ValueError("offer DB down")))
    r = chat.say(f"approve {pid} as Nadin")
    assert r.data["intent"] == "approve_failed" and "offer DB down" in r.text
    assert "curriculum_approved" in r.data["launch"]["completed"]
    monkeypatch.setattr(ctx.offers, "approve", real_approve)
    r = chat.say(f"approve {pid} as Nadin")
    assert r.data["intent"] == "launch_approved", r.text
    assert ctx.courses.get("TRK-007") is None                        # the course was not published twice
    assert len(ctx.offers.list_offers("TRK-006")) == 1              # the offer was not proposed twice


def test_sending_twice_does_not_send_again(ctx):
    chat = Chat(ctx)
    pid = launch(chat)
    draft_id = chat.say(f"approve {pid} as Nadin").data["launch"]["draft_id"]
    chat.say(f"send {pid}")
    chat.say(f"send {pid}")
    assert len(ctx.distribution.history(draft_id)) == 2


def test_a_new_launch_is_refused_while_a_plan_is_open_and_cancel_closes_it(ctx):
    chat = Chat(ctx)
    pid = launch(chat)
    assert chat.say('Launch a new course "Other" covering X, email ali@example.com').data["intent"] == "plan_open"
    assert chat.say(f"cancel {pid}").data["intent"] == "launch_cancelled"
    assert launch(chat, 'Launch a new course "Other Course" covering Python, email ali@example.com')


# ------------------------------------------------------------------ routing after a launch (bug 2026-11-12)
def test_an_unrelated_question_after_a_launch_goes_to_rex(ctx):
    """Found in the live app: after 'send LP-…', "I want to know the offers that academy has ?" stayed
    with the launch agent (no keyword matched, so the last agent kept it) and was read as a new launch."""
    chat = Chat(ctx)
    pid = launch(chat)
    chat.say(f"approve {pid} as Nadin")
    chat.say(f"send {pid}")
    r = chat.say("I want to know the offers that academy has ?")
    assert r.agent_name == "rex_assistant" and r.data["intent"] == "offers_overview", r.text
    assert "TRK-006" in r.text and "20%" in r.text


def test_a_question_between_approve_and_send_does_not_lose_the_plan(ctx):
    chat = Chat(ctx)
    pid = launch(chat)
    chat.say(f"approve {pid} as Nadin")
    assert chat.say("what discounts do you have?").agent_name == "rex_assistant"
    r = chat.say(f"send {pid}")                                       # the LP id routes back to the launch
    assert r.agent_name == "course_launch" and r.data["intent"] == "launch_sent", r.text


def test_missing_details_still_stay_with_the_launch(ctx):
    chat = Chat(ctx)
    chat.say('Launch a new course "Computer Networks" covering TCP/IP and Routing')
    r = chat.say("nadin@example.com")                                 # no keywords, but a reply is expected
    assert r.agent_name == "course_launch" and r.data["intent"] == "launch_prepared", r.text
