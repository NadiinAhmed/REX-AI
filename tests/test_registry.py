"""Role 3 — agent registry and router."""
import pytest

from src.agents.base import Agent, AgentRequest, AgentResponse, AgentStatus, ResponseMode
from src.agents.registry import AgentNotImplementedError, AgentRegistry, UnknownAgentError


class EchoAgent(Agent):
    name, description = "echo", "test agent"

    def handle(self, request):
        return AgentResponse(agent_name=self.name, text=request.message, mode=ResponseMode.DETERMINISTIC)


def test_dispatch_to_registered_agent():
    reg = AgentRegistry()
    reg.register(EchoAgent())
    assert reg.dispatch("echo", AgentRequest.from_text("hi")).text == "hi"


def test_planned_agents_are_listed_but_not_dispatchable():
    reg = AgentRegistry()
    planned = {a.name for a in reg.list_agents() if a.status == AgentStatus.PLANNED}
    assert planned == {"rfp_research", "curriculum", "document_generation", "marketing"}
    with pytest.raises(AgentNotImplementedError):
        reg.dispatch("marketing", AgentRequest.from_text("hi"))


def test_unknown_and_duplicate_agents():
    reg = AgentRegistry()
    reg.register(EchoAgent())
    with pytest.raises(ValueError):
        reg.register(EchoAgent())
    with pytest.raises(UnknownAgentError):
        reg.get("nope")


def test_request_validation():
    with pytest.raises(ValueError):
        AgentRequest(message="")
    with pytest.raises(ValueError):
        AgentRequest(message="x" * 5000)
