"""Agent 5 behaviour: generators (fake LLMs), LangGraph transitions, approval gate, evaluator."""
import json

import pytest

from src.agents.base import AgentRequest, ResponseMode
from src.agents.marketing_agent import MarketingAgent
from src.db.database import Database
from src.db.models import DraftStatus
from src.db.repositories import CourseRepository, MarketingRepository
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from src.marketing.evaluator import LLMJudge, MarketingEvaluator
from src.marketing.facts import CourseFactsProvider
from src.marketing.generator import GenerationError, LLMContentGenerator, LLMDraftSet
from src.marketing.models import CampaignBrief, CampaignObjective, DraftVariation, Platform, StrategyPlan
from src.services import approval_service as approval_module
from src.services.approval_service import ApprovalError, ApprovalService
from src.services.llm_service import LLMError
from tests.conftest import OFFICIAL_DATASET

GOOD = {"variations": [
    {"angle": "career", "headline": "Become a Junior QA Engineer", "body": "Software Testing prepares final-year "
     "students for QA careers through a 100-hour program with a real capstone.", "cta": "Register your interest now",
     "hashtags": ["#SoftwareTesting", "QA"], "email_subject": None},
    {"angle": "project", "headline": "Build a real test suite", "body": "In Software Testing you build an "
     "automation framework for a real application.", "cta": "Request the details", "hashtags": []}]}
BAD = {"variations": [{"angle": "x", "headline": "20% off Software Testing!", "body": "Only 2000 EGP, starts on "
       "5 November, guaranteed job.", "cta": "Register now"}]}


class FakeLLM:
    """LLMService returning queued replies (str or Exception). No chat_model: the JSON path is used."""
    name = "fake"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def generate(self, system_prompt, messages):
        self.calls += 1
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


class FakeStructuredModel:
    """Stands in for a LangChain chat model that supports with_structured_output."""
    def __init__(self, result):
        self.result, self.schema = result, None

    def with_structured_output(self, schema):
        self.schema = schema
        model = self

        class Runnable:
            def invoke(self, messages):
                if isinstance(model.result, Exception):
                    raise model.result
                return model.result
        return Runnable()


class StructuredLLM(FakeLLM):
    def __init__(self, result, *json_replies):
        super().__init__(*(json_replies or [GOOD]))
        self.chat_model = FakeStructuredModel(result)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'a.db'}")
    database.migrate()
    CourseRepository(database).replace_catalog(load_catalog(OFFICIAL_DATASET))
    return database


@pytest.fixture
def facts():
    return CourseFactsProvider(KeywordRetriever(load_catalog(OFFICIAL_DATASET).courses))


def agent(facts, db=None, llm=None, **kw):
    return MarketingAgent(facts=facts, llm=llm, repository=MarketingRepository(db) if db else None, **kw)


def brief(**kw):
    return CampaignBrief(**({"course_id": "TRK-001", "platforms": [Platform.LINKEDIN],
                             "objective": CampaignObjective.ENROLLMENT, "variations": 2} | kw))


# ---------------------------------------------------------------- generators
def test_structured_output_path_is_used_first(facts):
    llm = StructuredLLM(LLMDraftSet.model_validate(GOOD))
    out = LLMContentGenerator(llm).generate(brief(), facts.facts_for("TRK-001"), StrategyPlan(platform_order=[]),
                                            Platform.LINKEDIN, "en", [])
    assert llm.chat_model.schema is LLMDraftSet and llm.calls == 0          # JSON path not needed
    assert [v.hashtags for v in out] == [["#SoftwareTesting", "#QA"], []]   # hashtags normalised


def test_structured_failure_falls_back_to_json_then_errors(facts):
    args = (brief(), facts.facts_for("TRK-001"), StrategyPlan(platform_order=[]), Platform.EMAIL, "en", [])
    out = LLMContentGenerator(StructuredLLM(RuntimeError("tool call failed"), GOOD)).generate(*args)
    assert out[0].email_subject == "Become a Junior QA Engineer"            # email gets a subject
    with pytest.raises(GenerationError, match="not valid JSON"):
        LLMContentGenerator(FakeLLM("{not json}")).generate(*args)
    with pytest.raises(GenerationError, match="schema"):
        LLMContentGenerator(FakeLLM({"variations": [{"headline": "only a headline"}]})).generate(*args)
    with pytest.raises(GenerationError):
        LLMContentGenerator(FakeLLM(LLMError("rate limited"))).generate(*args)


# ---------------------------------------------------------------- workflow transitions
def test_missing_information_asks_then_completes_over_turns(facts, db):
    a, state = agent(facts, db), {}
    r = a.handle(AgentRequest.from_text("I need a campaign", state))
    assert r.data["intent"] == "follow_up" and r.data["missing"] == ["course_id", "platforms", "objective"]
    assert "TRK-001" in r.text                                    # lists the real courses, nothing invented
    r = a.handle(AgentRequest.from_text("Software Testing on LinkedIn", r.state))
    assert r.data["missing"] == ["objective"]                     # does not ask again for course/platform
    r = a.handle(AgentRequest.from_text("to get registrations", r.state))
    assert r.data["intent"] == "campaign" and r.data["status"] == "pending_review"
    assert len(r.data["draft_ids"]) == 3 and r.sources[0].course_id == "TRK-001"


def test_unknown_course_reports_failure(facts):
    r = agent(facts).create_campaign(brief(course_id="ZZZ-999"))
    assert r.mode == ResponseMode.ERROR and "ZZZ-999" in r.text


def test_llm_campaign_multi_platform_saved_as_pending(facts, db):
    a = agent(facts, db, llm=FakeLLM(GOOD))
    r = a.create_campaign(brief(platforms=[Platform.LINKEDIN, Platform.FACEBOOK, Platform.EMAIL]))
    assert r.mode == ResponseMode.LLM and len(r.data["variations"]) == 6
    assert {v["platform"] for v in r.data["variations"]} == {"linkedin", "facebook", "email"}
    drafts = MarketingRepository(db).list_drafts(campaign_id=r.data["campaign_id"])
    assert len(drafts) == 6 and {d.approval_status for d in drafts} == {"pending_review"}
    snapshot = MarketingRepository(db).get_campaign(r.data["campaign_id"]).facts_snapshot
    assert snapshot["course_id"] == "TRK-001" and "fee" in snapshot["missing_fields"]


def test_invented_claims_are_retried_with_feedback_then_accepted(facts):
    llm = FakeLLM(BAD, GOOD)
    r = agent(facts, llm=llm).create_campaign(brief())
    assert llm.calls == 2 and r.data["attempts"] == 2 and r.data["dropped"] == 0
    assert "2000" not in r.text and "20%" not in r.text


def test_claims_that_persist_are_dropped_and_never_shown(facts):
    r = agent(facts, llm=FakeLLM(BAD), max_attempts=2).create_campaign(brief())
    assert r.mode == ResponseMode.ERROR
    assert "No usable variation" in r.text and "2000" not in r.text and r.data["intent"] == "error"


def test_llm_failure_falls_back_to_deterministic_copy(facts):
    r = agent(facts, llm=FakeLLM(LLMError("timeout"))).create_campaign(brief())
    assert r.mode == ResponseMode.DETERMINISTIC and len(r.data["variations"]) == 2
    assert any("deterministic copy" in n for n in r.data["notes"])


def test_arabic_campaign_and_unapproved_offer_request(facts):
    r = agent(facts).handle(AgentRequest.from_text(
        "حملة واتساب لبرنامج اختبار البرمجيات للتسجيل مع خصم 30%"))
    assert r.language == "ar" and r.data["intent"] == "campaign"
    assert "بانتظار المراجعة" in r.text and "30" not in "".join(v["body"] for v in r.data["variations"])
    assert any("NOT used" in n for n in r.data["notes"])


def test_reset_starts_a_new_brief(facts):
    r = agent(facts).handle(AgentRequest.from_text("Software Testing on LinkedIn"))
    r = agent(facts).handle(AgentRequest.from_text("new campaign", r.state))
    assert r.data["missing"] == ["course_id", "platforms", "objective"]


def test_database_error_still_returns_drafts(facts):
    class BrokenRepo:
        def create_campaign(self, **kw):
            raise RuntimeError("disk full")
    r = MarketingAgent(facts=facts, repository=BrokenRepo()).create_campaign(brief())
    assert len(r.data["variations"]) == 2 and any("could not be saved" in n for n in r.data["notes"])


def test_graph_diagram_shows_conditional_routes(facts):
    diagram = agent(facts).graph_diagram()
    assert "validate_claims -.-> generate" in diagram and "parse_brief -.-> ask_followup" in diagram


# ---------------------------------------------------------------- approval gate (requirement 42)
@pytest.fixture
def approvals(facts, db):
    return ApprovalService(MarketingRepository(db), facts)


def test_approval_requires_reviewer_and_records_it(facts, db, approvals):
    draft_id = agent(facts, db).create_campaign(brief()).data["draft_ids"][0]
    with pytest.raises(ApprovalError, match="reviewer name"):
        approvals.approve(draft_id, "")
    approvals.approve(draft_id, "Marketing Manager", "looks good")
    d = MarketingRepository(db).get_draft(draft_id)
    assert d.approval_status == "approved" and d.reviewed_by == "Marketing Manager" and d.reviewed_at
    with pytest.raises(ApprovalError, match="Only drafts pending review"):
        approvals.approve(draft_id, "someone")


def test_edit_with_invented_claim_cannot_be_approved(facts, db, approvals):
    draft_id = agent(facts, db).create_campaign(brief()).data["draft_ids"][0]
    edited = DraftVariation(platform=Platform.LINKEDIN, language="en", headline="Software Testing",
                            body="Software Testing now 30% off, only 1500 EGP!", cta="Register now")
    issues = approvals.edit(draft_id, edited, "editor")
    assert {"discount", "price"} <= {i.kind for i in issues}
    with pytest.raises(ApprovalError, match="does not support"):
        approvals.approve(draft_id, "manager")
    with pytest.raises(ApprovalError, match="platform"):
        approvals.edit(draft_id, edited.model_copy(update={"platform": Platform.EMAIL}), "editor")


def test_reject_and_unknown_draft(facts, db, approvals):
    draft_id = agent(facts, db).create_campaign(brief()).data["draft_ids"][0]
    approvals.reject(draft_id, "manager", "off-brand")
    assert MarketingRepository(db).get_draft(draft_id).approval_status == DraftStatus.REJECTED.value
    with pytest.raises(ApprovalError, match="No draft"):
        approvals.approve("missing", "manager")


def test_no_publishing_or_sending_code_exists():
    for cls in (ApprovalService, MarketingAgent, MarketingRepository):
        assert not [m for m in dir(cls) if any(w in m.lower() for w in ("publish", "send", "post_to", "distribute"))]
    source = open(approval_module.__file__, encoding="utf-8").read()
    assert "smtplib" not in source and "requests" not in source


# ---------------------------------------------------------------- evaluator
def test_evaluator_gates_and_dimensions(facts):
    f = facts.facts_for("TRK-001")
    good = DraftVariation(platform=Platform.LINKEDIN, language="en", headline="Software Testing for students",
                          body="Software Testing helps final-year university students start QA careers.",
                          cta="Register your interest now")
    e = MarketingEvaluator().evaluate(good, brief(), f)
    assert e.passed and e.score("factual_accuracy") == 1.0 and e.score("objective_alignment") == 1.0
    bad = good.model_copy(update={"body": "Software Testing: 20% off, guaranteed job!", "cta": "Follow us"})
    e = MarketingEvaluator().evaluate(bad, brief(), f)
    assert not e.passed and e.score("unsupported_claims") == 0.0 and e.score("objective_alignment") == 0.0
    arabic = good.model_copy(update={"language": "ar"})
    assert MarketingEvaluator().evaluate(arabic, brief(), f).score("tone_language") == 0.0   # English text, AR asked
    insta = good.model_copy(update={"platform": Platform.INSTAGRAM})
    assert MarketingEvaluator().evaluate(insta, brief(), f).score("platform_formatting") < 1.0  # needs hashtags


def test_llm_judge_parses_scores_and_survives_bad_output(facts):
    v = DraftVariation(platform=Platform.EMAIL, language="en", headline="h", body="b", cta="c", email_subject="s")
    ok = LLMJudge(FakeLLM({"audience_relevance": 0.8, "objective_alignment": 1, "cta_quality": 0.5,
                           "tone_language": 2, "comment": "fine"})).score(v, brief())
    assert [s.score for s in ok] == [0.8, 1.0, 0.5, 1.0] and all(s.method == "judge" for s in ok)
    broken = LLMJudge(FakeLLM("no json")).score(v, brief())
    assert all(s.score is None and "judge failed" in s.reason for s in broken)
