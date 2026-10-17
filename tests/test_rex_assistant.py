"""Role 1 — REX Assistant behaviour, using a fake LLM (no network)."""
import pytest

from src.agents.base import AgentRequest, ResponseMode
from src.agents.rex_assistant import RexAssistant
from src.services.llm_service import LLMError


class FakeLLM:
    name = "fake"

    def __init__(self, reply="LLM reply", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if self.fail:
            raise LLMError("boom")
        return self.reply


@pytest.fixture
def assistant(retriever, catalog):
    return RexAssistant(retriever, llm=None, dataset_notice=catalog.dataset.notice)


def ask(agent, *messages):
    state, response = {}, None
    for m in messages:
        response = agent.handle(AgentRequest.from_text(m, state))
        state = response.state
    return response


def test_greeting_triggers_follow_up_questions(assistant):
    r = ask(assistant, "Hi")
    assert r.data["intent"] == "follow_up" and r.mode == ResponseMode.DETERMINISTIC
    assert "beginner" in r.text and not r.sources


def test_arabic_greeting_gets_arabic_follow_up(assistant):
    r = ask(assistant, "مرحبا")
    assert r.language == "ar" and "مستواك" in r.text


def test_recommendation_is_grounded_and_sourced(assistant):
    r = ask(assistant, "I'm a beginner and want to become a data analyst, online")
    assert r.data["intent"] == "recommendation"
    assert r.sources[0].course_id == "DS-101" and r.sources[0].synthetic
    assert "4500 EGP" in r.text and "Demo data" in r.text


def test_arabic_recommendation(assistant):
    r = ask(assistant, "عايز اتعلم تسويق رقمي اونلاين")
    assert r.language == "ar" and r.sources[0].course_id == "MKT-120"
    assert "أساسيات التسويق الرقمي" in r.text


def test_unknown_fields_are_reported_not_invented(assistant):
    r = ask(assistant, "Who is the instructor for DS-101?")
    assert "not available" in r.text
    r = ask(assistant, "كم سعر أساسيات إدارة المشاريع؟")
    assert "غير متوفرة" in r.text


def test_no_match_says_so(assistant):
    r = ask(assistant, "I want to learn cooking")
    assert r.sources == [] and "couldn't find" in r.text


SINGLE_MATCH = "I'm a beginner and want to become a data analyst, online"  # -> only DS-101


def test_follow_up_about_single_recommended_course(assistant):
    r = ask(assistant, SINGLE_MATCH, "how long is it?")
    assert r.data["intent"] == "course_question" and "40 h" in r.text


def test_ambiguous_it_with_several_courses_asks_which(assistant):
    r = ask(assistant, "I want to become a data analyst", "how long is it?")
    assert len(r.state["last_course_ids"]) > 1
    assert r.data["intent"] == "clarify_course"


def test_ordinal_picks_the_right_course(assistant):
    first = ask(assistant, "I want to become a data analyst")
    second_id = first.state["last_course_ids"][1]
    r = assistant.handle(AgentRequest.from_text("how much is the second one?", first.state))
    assert r.sources[0].course_id == second_id


def test_out_of_range_ordinal_asks_for_clarification(assistant):
    r = ask(assistant, SINGLE_MATCH, "how much is the second one?")
    assert r.data["intent"] == "clarify_course" and "DS-101" in r.text


def test_new_topic_is_not_mistaken_for_course_question(assistant):
    r = ask(assistant, "what are the prerequisites for AI-201?", "I want digital marketing online")
    assert r.data["intent"] == "recommendation" and r.sources[0].course_id == "MKT-120"


def test_contact_details_are_refused(assistant):
    r = ask(assistant, "my phone is 01001234567, I like marketing")
    assert "can't save contact details" in r.text
    assert "0100" not in str(r.state)


def test_llm_mode_receives_course_data_as_delimited_data(retriever, catalog):
    llm = FakeLLM("Here is my grounded answer")
    agent = RexAssistant(retriever, llm=llm, dataset_notice=catalog.dataset.notice)
    r = ask(agent, "I want to become a data analyst")
    assert r.mode == ResponseMode.LLM and r.text == "Here is my grounded answer"
    system, messages = llm.calls[0]
    assert "data, not instructions" in system
    assert "<course_data>" in messages[0].content and "DS-101" in messages[0].content
    assert r.data["llm_status"] == "used"


def test_llm_failure_falls_back_and_is_labelled(retriever, catalog):
    agent = RexAssistant(retriever, llm=FakeLLM(fail=True), dataset_notice=catalog.dataset.notice)
    r = ask(agent, "I want to become a data analyst")
    assert r.mode == ResponseMode.DETERMINISTIC and r.data["llm_status"] == "failed"
    assert "rule-based" in r.text and "DS-101" in r.text


def test_follow_up_questions_never_call_llm(retriever):
    llm = FakeLLM()
    ask(RexAssistant(retriever, llm=llm), "hello")
    assert llm.calls == []
