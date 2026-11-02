"""Agent Orchestrator / Router (brief section 4): routing, permissions, state isolation, failures."""
from __future__ import annotations

import logging

import pytest

from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode
from src.agents.orchestrator import ALL_AGENTS, PUBLIC_AGENTS, IntentRouter, OrchestratorAgent
from src.agents.registry import AgentRegistry


# ---------------------------------------------------------------- stub agents
class StubAgent(Agent):
    """Echoes what it received and counts its own turns in its private state."""

    def __init__(self, name: str, waits: bool = False, fails: bool = False) -> None:
        self.name, self.description = name, name
        self.calls: list[AgentRequest] = []
        self._waits, self._fails = waits, fails

    def handle(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(request)
        if self._fails:
            raise RuntimeError("boom")
        turns = request.state.get("turns", 0) + 1
        state = {"turns": turns, "waiting": self._waits}
        return AgentResponse(agent_name=self.name, text=f"{self.name}:{request.message}",
                             mode=ResponseMode.DETERMINISTIC, state=state)

    def expects_reply(self, state: dict) -> bool:
        return bool(state.get("waiting"))


def make(*, waits: str | None = None, fails: str | None = None, skip: str | None = None):
    registry, agents = AgentRegistry(), {}
    for name in sorted(ALL_AGENTS):
        if name == skip:
            continue
        agents[name] = StubAgent(name, waits=name == waits, fails=name == fails)
        registry.register(agents[name])
    return OrchestratorAgent(registry), agents


def chat(orch, *messages, staff: bool = True):
    state, r = {}, None
    for m in messages:
        r = orch.handle(AgentRequest.from_text(m, state), allowed_agents=ALL_AGENTS if staff else PUBLIC_AGENTS)
        state = r.state
    return r


def routed(r) -> tuple[str, str, str]:
    d = r.data["routing"]
    return d["action"], d["agent"], d["reason"]


# ---------------------------------------------------------------- keyword routing (EN + AR)
@pytest.mark.parametrize("message, agent", [
    ("Any education tenders in Egypt?", "rfp_research"),
    ("ابحث عن مناقصات تدريب في السعودية", "rfp_research"),
    ("RFP-1A2B3C", "rfp_research"),
    ("create a curriculum for TRK-001 80 hours", "curriculum"),
    ("أنشئ منهج TRK-001", "curriculum"),
    ("align CUR-1A2B3C with swebok-3", "curriculum"),
    ("generate a participant guide for TRK-001", "document_generation"),
    ("question bank for CUR-1A2B3C", "document_generation"),   # the curriculum ID is only the input
    ("اعمل دليل المشارك لـ TRK-001 بالعربية", "document_generation"),
    ("write a LinkedIn campaign for the Software Testing course", "marketing"),
    ("عايز حملة تسويق لكورس البايثون", "marketing"),
    ("I'm a final-year student and want to work in data", "rex_assistant"),        # default
    ("what are the fees of the data course?", "rex_assistant"),
    ("tell me about the digital marketing course", "rex_assistant"),              # a course, not a campaign
])
def test_staff_messages_reach_the_right_agent(message, agent):
    orch, agents = make()
    r = chat(orch, message)
    assert r.data["routing"]["agent"] == agent
    assert agents[agent].calls[-1].message == message           # forwarded unchanged


# ---------------------------------------------------------------- permissions
def test_students_only_ever_reach_rex():
    orch, agents = make()
    for message in ("Any tenders in Egypt?", "write a LinkedIn campaign", "create a curriculum for TRK-001"):
        assert routed(chat(orch, message, staff=False))[1] == "rex_assistant"
    assert all(not agents[a].calls for a in ALL_AGENTS - PUBLIC_AGENTS)


def test_student_asking_for_a_staff_tool_explicitly_is_refused():
    orch, agents = make()
    r = chat(orch, "/marketing make a post", staff=False)
    assert routed(r)[0] == "deny" and "staff" in r.text and not agents["marketing"].calls


def test_default_is_least_privilege():
    orch, agents = make()
    orch.handle(AgentRequest.from_text("Any tenders in Egypt?"))       # no allowed_agents given
    assert not agents["rfp_research"].calls and agents["rex_assistant"].calls


# ---------------------------------------------------------------- conversation behaviour
def test_follow_up_stays_with_the_active_agent():
    orch, agents = make()
    r = chat(orch, "write a LinkedIn campaign for TRK-001", "Arabic, for fresh graduates")
    assert routed(r) == ("dispatch", "marketing", "follow_up")
    assert len(agents["marketing"].calls) == 2


def test_each_agent_keeps_its_own_state_across_switches():
    orch, agents = make()
    r = chat(orch, "write a LinkedIn campaign", "Any tenders in Egypt?", "and for instagram too")
    assert routed(r)[1] == "rfp_research"      # instagram is a weak hint: stays with the active agent
    r = chat(orch, "write a LinkedIn campaign", "Any tenders in Egypt?", "/marketing and for instagram too")
    assert agents["marketing"].calls[-1].state == {"turns": 1, "waiting": False}    # marketing's own state back
    assert r.state["agents"]["rfp_research"]["turns"] == 1


def test_agent_waiting_for_a_reply_is_never_interrupted():
    orch, agents = make(waits="rex_assistant")
    r = chat(orch, "register", "Sara, sara@example.com, I work on marketing campaigns")
    assert routed(r) == ("dispatch", "rex_assistant", "awaiting_reply")
    assert not agents["marketing"].calls


def test_ambiguous_request_asks_then_uses_the_answer():
    orch, agents = make()
    r = chat(orch, "generate the curriculum PDF for TRK-001")
    assert routed(r)[0] == "clarify" and "1." in r.text and "2." in r.text
    options = r.state["pending"]["options"]
    r = orch.handle(AgentRequest.from_text("٢", r.state), allowed_agents=ALL_AGENTS)    # Arabic digit
    assert routed(r) == ("dispatch", options[1], "clarified")
    assert agents[options[1]].calls[-1].message == "generate the curriculum PDF for TRK-001"   # original text
    assert r.state["pending"] is None


def test_ignoring_a_clarifying_question_routes_the_new_message_normally():
    orch, _ = make()
    r = chat(orch, "generate the curriculum PDF for TRK-001", "Any tenders in Egypt?")
    assert routed(r)[1] == "rfp_research" and r.state["pending"] is None


# ---------------------------------------------------------------- explicit commands
@pytest.mark.parametrize("message, agent, forwarded", [
    ("/rfp egypt", "rfp_research", "egypt"),
    ("Switch to marketing: TRK-001 on LinkedIn", "marketing", "TRK-001 on LinkedIn"),
    ("/documents types", "document_generation", "types"),
])
def test_commands_choose_the_agent(message, agent, forwarded):
    orch, agents = make()
    r = chat(orch, message)
    assert routed(r) == ("dispatch", agent, "explicit") and agents[agent].calls[-1].message == forwarded


@pytest.mark.parametrize("message, agent", [("switch to the curriculum agent", "curriculum"), ("حولني للتسويق", "marketing")])
def test_switch_without_text_changes_the_active_agent(message, agent):
    orch, agents = make()
    r = chat(orch, message)
    assert routed(r)[0] == "switch" and r.state["active"] == agent and not agents[agent].calls
    r = orch.handle(AgentRequest.from_text("hello", r.state), allowed_agents=ALL_AGENTS)
    assert routed(r) == ("dispatch", agent, "follow_up")


def test_help_lists_only_allowed_and_available_agents():
    orch, _ = make(skip="document_generation")
    staff = chat(orch, "/help").text
    assert "/rfp" in staff and "/marketing" in staff and "/documents" not in staff
    assert "/rfp" not in chat(orch, "/help", staff=False).text


# ---------------------------------------------------------------- failures and logging
def test_failing_agent_returns_an_error_and_the_chat_continues():
    orch, _ = make(fails="rfp_research")
    r = chat(orch, "write a LinkedIn campaign", "Any tenders in Egypt?")
    assert r.mode == ResponseMode.ERROR and "went wrong" in r.text
    assert r.state["agents"]["marketing"]["turns"] == 1            # nothing lost
    r = orch.handle(AgentRequest.from_text("/marketing again", r.state), allowed_agents=ALL_AGENTS)
    assert r.mode == ResponseMode.DETERMINISTIC


def test_unavailable_agent_is_reported_not_crashed():
    orch, _ = make(skip="document_generation")
    r = chat(orch, "generate a participant guide for TRK-001")
    assert r.mode == ResponseMode.ERROR and "not available" in r.text


def test_routing_is_logged_without_the_message_text(caplog):
    caplog.set_level(logging.INFO)
    orch, _ = make()
    chat(orch, "my email is sara@example.com")
    assert "route action=dispatch agent=rex_assistant" in caplog.text
    assert "sara@example.com" not in caplog.text


def test_router_is_pure_and_side_effect_free():
    orch, agents = make()
    d = orch.decide("Any tenders in Egypt?", allowed_agents=ALL_AGENTS)
    assert (d.action, d.agent) == ("dispatch", "rfp_research") and not any(a.calls for a in agents.values())
    assert IntentRouter().explicit("I want to learn python") is None


# ---------------------------------------------------------------- real application (all five agents)
@pytest.fixture
def ctx(test_env):
    from src.bootstrap import build_app
    from src.config import load_settings
    from tests.conftest import OFFICIAL_DATASET
    return build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))), use_configured_llm=False,
                     enable_rag=False)


def test_one_chat_reaches_all_five_real_agents(ctx):
    state, seen = {}, []
    for message in ("I want to become a software tester",
                    "Any education tenders in Egypt?",
                    "create a curriculum for TRK-001",
                    "generate a participant guide for TRK-001",
                    "write a LinkedIn campaign for TRK-001 for registrations"):
        r = ctx.orchestrator.handle(AgentRequest.from_text(message, state), allowed_agents=ALL_AGENTS)
        assert r.mode != ResponseMode.ERROR, (message, r.text)
        seen.append(r.agent_name)
        state = r.state
    assert seen == ["rex_assistant", "rfp_research", "curriculum", "document_generation", "marketing"]
    assert ctx.curriculum_service.list()                     # the curriculum agent really stored a draft
    assert set(state["agents"]) == set(seen)


def test_lead_capture_is_not_hijacked_by_keywords(ctx):
    """While REX waits for contact details, a reply mentioning "marketing campaigns" still goes to REX."""
    state = {}
    for message in ("I want to become a data engineer", "register", "yes",
                    "Sara Ahmed, sara@example.com, I work in marketing campaigns"):
        r = ctx.orchestrator.handle(AgentRequest.from_text(message, state), allowed_agents=ALL_AGENTS)
        state = r.state
    assert (r.agent_name, r.data["routing"]["reason"]) == ("rex_assistant", "awaiting_reply")
    assert ctx.marketing.list_campaigns() == []                 # the marketing agent never ran
    r = ctx.orchestrator.handle(AgentRequest.from_text("Sara Ahmed, sara@example.com", state),
                                allowed_agents=ALL_AGENTS)
    assert r.agent_name == "rex_assistant" and ctx.leads.count() == 1
