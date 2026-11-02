"""Agent 1 final fixes (2026-11-02), measured with evaluation/run_ragas.py before and after.

A1  Abstention: "How much does the Flutter Development track cost?" was answered from the Flutter
    pages because the course name matched. Now a result needs an "evidence term" (a question word
    that is not a course-name word). Correct abstention 4/6 -> 6/6 (held-out set 2/6 -> 6/6).
A2  Word stems: "international" matched "interview" (fixed 5-letter stem).
A3  A "not found" LLM answer showed citations. The LLM now replies NOT_FOUND and the code writes the
    message with no sources.
A4  Glossary entries spelled with ئ/أ/ة never matched normalized questions ("وظائف").
A5  Arabic attribute words matched inside other words ("مدرس" in "مدرسة") or never matched ("وظائف").
A6  Nicknames from the glossary find a course: "the QA track", "كورس الدوت نت".
A7  The prompt forbids promises REX cannot keep and claims that are not in the data.
"""
import re

import pytest

from src.agents import prompts
from src.agents.base import AgentRequest, ResponseMode
from src.agents.rex_assistant import _ATTRIBUTE_PATTERNS
from src.bootstrap import build_app
from src.config import load_settings
from src.knowledge_base.course_resolver import CourseResolver
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from src.knowledge_base.text_utils import normalize_arabic, tokenize
from src.rag.retriever import shares_key_term
from tests.conftest import OFFICIAL_DATASET
from tests.test_course_resolver import ask, ctx  # noqa: F401  (official catalog, hashing embeddings)


# ---------------------------------------------------------------- A1 / A2 retrieval abstention
@pytest.mark.parametrize("question", [
    "How much does the Flutter Development track cost?",
    "Who is the instructor of the Data Engineering track?",
    "Does the academy offer scholarships for international students?",
    "Can I pay the fees in installments?",
])
def test_questions_the_documents_do_not_answer_retrieve_nothing(ctx, question):  # noqa: F811
    result = ctx.knowledge.passages.retrieve(question)
    assert result.passages == []


@pytest.mark.parametrize("question", [
    "How many training hours is the Software Testing track?",
    "Which tools are covered in the Software Testing track?",
    "Is Kafka taught in the Data Engineering track?",
    "كم عدد ساعات مسار تطوير تطبيقات فلاتر؟",
    "ما هي الوظائف المتاحة بعد مسار اختبار البرمجيات؟",
])
def test_answerable_course_questions_still_find_passages(ctx, question):  # noqa: F811
    result = ctx.knowledge.passages.retrieve(question)
    assert result.passages, result.diagnostics


def test_diagnostics_show_evidence_terms(ctx):  # noqa: F811
    d = ctx.knowledge.passages.retrieve("How much does the Flutter Development track cost?").diagnostics
    assert d.evidence_terms == ["cost"] and "flutter" in d.key_terms and d.dropped_no_evidence > 0


def test_stem_does_not_join_unrelated_words():
    assert not shares_key_term({"international"}, "Prepare for your first interview.")
    assert shares_key_term({"certificate"}, "Graduates receive certificates.")
    assert shares_key_term({"partnership"}, "Contact us about partnerships.")


# ---------------------------------------------------------------- A3 NOT_FOUND from the LLM
class FakeLLM:
    name = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def generate(self, system, messages):
        return self.reply


def _doc_ctx(test_env, reply):
    return build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))), llm=FakeLLM(reply))


def test_llm_not_found_shows_no_sources(test_env):
    ctx = _doc_ctx(test_env, "NOT_FOUND")
    r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text("Who can I contact about partnerships?", {}))
    assert r.mode == ResponseMode.LLM and r.sources == []
    assert "couldn't find" in r.text and r.data["llm_abstained"] is True


def test_llm_answer_keeps_its_sources(test_env):
    ctx = _doc_ctx(test_env, "Email the academy [1].")
    r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text("Who can I contact about partnerships?", {}))
    assert r.text == "Email the academy [1]." and r.sources


def test_doc_prompt_asks_for_the_not_found_marker():
    assert prompts.DOC_QA_NOT_FOUND in prompts.DOC_QA_SYSTEM_PROMPT


# ---------------------------------------------------------------- A4 glossary normalization
def test_glossary_entries_with_hamza_and_ta_marbuta_match():
    assert "career" in tokenize("ما هي الوظائف المتاحة؟")
    assert "hours" in tokenize("كم عدد ساعات المسار؟")
    assert "على" not in tokenize("هل يوجد خصم على الكورس")          # stopword spelled with ى


# ---------------------------------------------------------------- A5 Arabic attribute patterns
@pytest.mark.parametrize("message, expected", [
    ("هل الأكاديمية معتمدة؟", []), ("ما مدة الكورس؟", ["duration"]),
    ("ما هي الوظائف بعد المسار؟", ["career_paths"]), ("ماذا سأتعلم في فلاتر؟", ["learning_outcomes"]),
    ("أنا خريج مدرسة ثانوية", []), ("من مدرس الكورس؟", ["instructor"]), ("مين المدربين؟", ["instructor"]),
    ("هل في شروط؟", ["prerequisites"]), ("قبول مشروط", []), ("نظام تشغيل", []), ("عايز شغل", ["career_paths"]),
    ("بكام الكورس؟", ["fee"]), ("امتى يبدأ الكورس؟", ["schedule"]), ("ما هي أدوات المسار؟", ["tools"]),
    ("هل في شهادة؟", ["deliverables"]), ("هل يوجد خصم؟", ["promotions"]), ("ما محتوى الكورس؟", ["modules"]),
    ("Who teaches the QA track?", ["instructor"]), ("Does the academy offer scholarships?", []),
])
def test_attribute_patterns(message, expected):
    text = normalize_arabic(message.lower())
    assert sorted(k for k, p in _ATTRIBUTE_PATTERNS.items() if re.search(p, text)) == sorted(expected)


# ---------------------------------------------------------------- A6 nicknames
@pytest.fixture(scope="module")
def resolver():
    return CourseResolver(KeywordRetriever(load_catalog(OFFICIAL_DATASET).courses))


@pytest.mark.parametrize("text, context, course_id", [
    ("the QA track", False, "TRK-001"),
    ("Who teaches the mobile course?", True, "TRK-002"),
    ("كورس الدوت نت بكام؟", True, "TRK-004"),
])
def test_glossary_nicknames(resolver, text, context, course_id):
    match = resolver.resolve(text, course_context=context)
    assert match.course is not None and match.course.course_id == course_id


def test_nickname_in_a_goal_is_still_a_goal(resolver):
    assert resolver.resolve("I want to learn mobile development").course is None


def test_rex_answers_instructor_question_by_nickname(ctx):  # noqa: F811
    r = ask(ctx, "rex_assistant", "Who teaches the QA track?")
    assert [s.course_id for s in r.sources] == ["TRK-001"] and "Instructor: not available" in r.text


# ---------------------------------------------------------------- A7 prompt rules
def test_prompt_forbids_promises_and_unsupported_claims():
    assert "Never promise actions you cannot take" in prompts.SYSTEM_PROMPT
    assert "job market" in prompts.SYSTEM_PROMPT
    assert "Never promise actions you cannot take" in prompts.DOC_QA_SYSTEM_PROMPT
