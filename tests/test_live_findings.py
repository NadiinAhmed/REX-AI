"""Problems found by the first live run with Groq (evaluation/live_check.py, 2026-11-02).

The offline test suite could not see them: two only appear when a real LLM writes the reply.
  L1  The LLM called the official (real) catalog "demo values" in 3 of 5 replies.
  L2  "Does the academy offer scholarships ...?" -> "Which course? (e.g. DS-101)": "offer" read as a
      discount question, and DS-101 is a code from the synthetic catalog, not the loaded one.
  L3  "Is the academy accredited?" got the note "This isn't in our structured course catalog yet".
  L4  "هل الأكاديمية معتمدة؟" -> "Which course?": "مده" (duration) matched inside "معتمده".
"""
import pytest

from src.agents.base import AgentRequest, ResponseMode
from src.agents.rex_assistant import RexAssistant
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from tests.conftest import OFFICIAL_DATASET
from tests.test_course_resolver import ask, ctx  # noqa: F401  (shared app fixture, official catalog)


class RecordingLLM:
    def __init__(self, reply: str = "Grounded answer.") -> None:
        self.reply, self.calls = reply, []

    def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        return self.reply


@pytest.fixture(scope="module")
def official():
    return load_catalog(OFFICIAL_DATASET)


def _ask_agent(agent, message):
    return agent.handle(AgentRequest.from_text(message, {}))


# ---------------------------------------------------------------- L1 demo notice decided by code
def test_llm_is_not_asked_to_write_a_demo_notice(official):
    llm = RecordingLLM()
    agent = RexAssistant(KeywordRetriever(official.courses), llm=llm, dataset_notice=official.dataset.notice)
    r = _ask_agent(agent, "I want to work in software testing")
    system, messages = llm.calls[0]
    assert "<dataset_notice>" not in messages[0].content
    assert "Do not add notes saying the data is demo" in system
    assert r.mode == ResponseMode.LLM and r.text == "Grounded answer."   # real catalog: no notice


def test_synthetic_catalog_still_gets_the_notice_from_code(retriever, catalog):
    agent = RexAssistant(retriever, llm=RecordingLLM(), dataset_notice=catalog.dataset.notice)
    r = _ask_agent(agent, "I want to become a data analyst")
    assert r.text.startswith("Grounded answer.") and "Demo data" in r.text


# ---------------------------------------------------------------- L2 "offer" as a verb, real example code
@pytest.mark.parametrize("message", [
    "Does the academy offer scholarships for international students?",
    "Do you offer evening classes?",
])
def test_offer_as_a_verb_is_not_a_discount_question(ctx, message):  # noqa: F811
    r = ask(ctx, "rex_assistant", message)
    assert r.data.get("intent") != "clarify_course"
    assert "Which course" not in r.text


@pytest.mark.parametrize("message", [
    "Is there any offer on TRK-001?", "any offers on TRK-001?", "Is there a discount on TRK-001?",
    "هل يوجد خصم على TRK-001؟", "في عروض على TRK-001؟",
])
def test_discount_questions_still_reach_the_offer_answer(ctx, message):  # noqa: F811
    r = ask(ctx, "rex_assistant", message)
    assert [s.course_id for s in r.sources] == ["TRK-001"]
    assert "no approved offer" in r.text or "لا يوجد عرض معتمد" in r.text


def test_which_course_example_comes_from_the_loaded_catalog(ctx):  # noqa: F811
    r = ask(ctx, "rex_assistant", "how much does it cost?")
    assert "TRK-001" in r.text and "DS-101" not in r.text


# ---------------------------------------------------------------- L3 note only for program-like topics
@pytest.mark.parametrize("message", ["Is the academy accredited?", "هل الأكاديمية معتمدة؟"])
def test_general_academy_question_has_no_catalog_note(ctx, message):  # noqa: F811
    r = ask(ctx, "rex_assistant", message)
    assert r.sources, "the portfolio PDF states the ITIDA accreditation"
    assert "structured course catalog" not in r.text and "كتالوج الدورات المنظم" not in r.text


def test_missing_program_still_gets_the_catalog_note(ctx):  # noqa: F811
    r = ask(ctx, "rex_assistant", "Do you have a cybersecurity course?")
    if r.sources:   # only when the documents mention it; otherwise the honest "no match" reply stands
        assert "structured course catalog" in r.text


# ---------------------------------------------------------------- L4 Arabic word boundaries
def test_arabic_accredited_is_not_a_duration_question(ctx):  # noqa: F811
    r = ask(ctx, "rex_assistant", "هل الأكاديمية معتمدة؟")
    assert r.data.get("intent") != "clarify_course"


@pytest.mark.parametrize("message", ["ما مدة كورس الفلاتر؟", "كام ساعة مدة كورس الفلاتر؟"])
def test_arabic_duration_question_still_works(ctx, message):  # noqa: F811
    r = ask(ctx, "rex_assistant", message)
    assert [s.course_id for s in r.sources] == ["TRK-002"] and "120" in r.text
