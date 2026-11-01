"""Agent 1 and offers: REX Assistant mentions ONLY approved, currently valid offers.

Same rule as the Marketing Agent, from the same shared OfferService, so the chatbot and the
ads can never disagree about a discount.
"""
import json
from datetime import date, timedelta

from src.agents.base import AgentRequest
from src.agents.rex_assistant import RexAssistant
from src.bootstrap import build_app, build_offer_reader
from src.config import PROJECT_ROOT, load_settings
from src.models.offer import Offer

TODAY = date.today()
OFFICIAL = str(PROJECT_ROOT / "data" / "courses_official.json")


class FakeOffers:
    def __init__(self, offers=(), fail=False):
        self.offers, self.fail = list(offers), fail

    def active_offers(self, course_id, on=None):
        if self.fail:
            raise ConnectionError("offer server down")
        return [o for o in self.offers if o.course_id == course_id]


class CapturingLLM:
    name = "fake"

    def __init__(self):
        self.calls = []

    def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        return "grounded answer"


def offer(course_id, pct=15):
    return Offer(id="o1", course_id=course_id, title_en="Early bird", title_ar="خصم الحجز المبكر",
                 discount_percent=pct, valid_from=TODAY, valid_until=TODAY + timedelta(days=10),
                 approval_status="approved", approved_by="Dr. Reviewer")


def ask(agent, text):
    return agent.handle(AgentRequest.from_text(text))


# ------------------------------------------------------------------ unit (fake offer source)
def test_offer_question_lists_the_approved_offer_in_english_and_arabic(retriever):
    agent = RexAssistant(retriever, offers=FakeOffers([offer("DS-101")]))
    en = ask(agent, "Is there any discount on DS-101?")
    assert "15%" in en.text and "Early bird" in en.text and str(TODAY + timedelta(days=10)) in en.text
    ar = ask(agent, "هل يوجد خصم على DS-101؟")
    assert "15%" in ar.text and "خصم الحجز المبكر" in ar.text


def test_free_text_promotion_is_never_presented_as_an_offer(retriever):
    """AI-201's record has a free-text '10% early registration' promotion that nobody approved."""
    r = ask(RexAssistant(retriever, offers=FakeOffers()), "Is there a discount on AI-201?")
    assert "10%" not in r.text and "no approved offer" in r.text


def test_without_an_offer_source_nothing_is_invented(retriever):
    r = ask(RexAssistant(retriever), "Is there a discount on AI-201?")
    assert "10%" not in r.text and "no approved offer" in r.text


def test_fee_answer_shows_the_active_offer_next_to_the_fee(retriever):
    r = ask(RexAssistant(retriever, offers=FakeOffers([offer("DS-101")])), "How much is DS-101?")
    assert "Fee" in r.text and "Current offer" in r.text and "15%" in r.text


def test_fee_answer_has_no_offer_line_when_there_is_none(retriever):
    r = ask(RexAssistant(retriever, offers=FakeOffers()), "How much is DS-101?")
    assert "Current offer" not in r.text


def test_recommendations_flag_courses_with_an_active_offer(retriever):
    agent = RexAssistant(retriever, offers=FakeOffers([offer("DS-101")]))
    r = ask(agent, "I want to become a data analyst")
    assert "DS-101" in r.text and "Current offer" in r.text and "15%" in r.text


def test_offer_source_failure_says_so_and_does_not_crash(retriever):
    r = ask(RexAssistant(retriever, offers=FakeOffers(fail=True)), "Is there a discount on DS-101?")
    assert "could not be checked" in r.text


def test_llm_gets_approved_offers_but_not_the_free_text_promotion(retriever):
    llm = CapturingLLM()
    agent = RexAssistant(retriever, llm=llm, offers=FakeOffers([offer("AI-201", pct=20)]))
    ask(agent, "Is there a discount on AI-201?")
    system, messages = llm.calls[0]
    content = messages[0].content
    data = json.loads(content.split("<course_data>")[1].split("</course_data>")[0])
    assert "promotions" not in data["course"]
    assert data["course"]["active_offers"][0]["discount_percent"] == 20
    assert "active_offers" in system


def test_shared_reader_uses_mcp_when_the_course_backend_does():
    class McpLikeRetriever:
        client = object()
    from src.mcp_client.academy_client import McpOfferReader
    assert isinstance(build_offer_reader(object(), McpLikeRetriever()), McpOfferReader)


# ------------------------------------------------------------------ end to end (real OfferService)
def test_end_to_end_only_approved_and_current_offers_reach_the_chatbot(test_env):
    ctx = build_app(load_settings(test_env(REX_COURSES_PATH=OFFICIAL)), use_configured_llm=False,
                    enable_rag=False)
    rex = ctx.registry.get("rex_assistant")
    question = "Is there any discount on TRK-001?"

    pending = ctx.offers.propose(course_id="TRK-001", title_en="Pending deal", discount_percent=50,
                                 valid_from=TODAY, valid_until=TODAY + timedelta(days=5), proposed_by="staff")
    expired = ctx.offers.propose(course_id="TRK-001", title_en="Old deal", discount_percent=30,
                                 valid_from=TODAY - timedelta(days=30), valid_until=TODAY - timedelta(days=1),
                                 proposed_by="staff")
    ctx.offers.approve(expired, "Dr. Reviewer")
    r = ask(rex, question)
    assert "50%" not in r.text and "30%" not in r.text and "no approved offer" in r.text

    ctx.offers.approve(pending, "Dr. Reviewer")
    r = ask(rex, question)
    assert "Pending deal: 50%" in r.text and "30%" not in r.text
