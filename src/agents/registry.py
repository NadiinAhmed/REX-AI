"""Agent registry and router. Sprint 1 dispatches to REX Assistant only.

The four other agents are listed as PLANNED so the UI and docs reflect the
roadmap honestly; dispatching to them raises AgentNotImplementedError.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agents.base import Agent, AgentError, AgentRequest, AgentResponse, AgentStatus
from src.utils.logging_config import get_logger

logger = get_logger("registry")


class UnknownAgentError(AgentError):
    pass


class AgentNotImplementedError(AgentError):
    pass


@dataclass(frozen=True)
class AgentInfo:
    name: str
    description: str
    status: AgentStatus


PLANNED_AGENTS: tuple[AgentInfo, ...] = (
    AgentInfo("rfp_research", "Educational RFP Research Agent (Egypt & Saudi Arabia tenders)", AgentStatus.PLANNED),
    AgentInfo("curriculum", "Curriculum Research & Accreditation Agent", AgentStatus.PLANNED),
    AgentInfo("document_generation", "Educational Content & Document Generation Agent", AgentStatus.PLANNED),
    AgentInfo("marketing", "AI Marketing Agent (human approval required before publishing)", AgentStatus.PLANNED),
)


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._planned: dict[str, AgentInfo] = {a.name: a for a in PLANNED_AGENTS}

    def register(self, agent: Agent) -> None:
        if agent.name in self._agents:
            raise ValueError(f"Agent '{agent.name}' is already registered.")
        self._agents[agent.name] = agent
        self._planned.pop(agent.name, None)
        logger.info("Registered agent '%s'", agent.name)

    def list_agents(self) -> list[AgentInfo]:
        implemented = [
            AgentInfo(a.name, a.description, AgentStatus.IMPLEMENTED) for a in self._agents.values()
        ]
        return implemented + list(self._planned.values())

    def get(self, name: str) -> Agent:
        if name in self._agents:
            return self._agents[name]
        if name in self._planned:
            raise AgentNotImplementedError(f"Agent '{name}' is planned but not implemented yet.")
        raise UnknownAgentError(f"No agent named '{name}'.")

    def dispatch(self, name: str, request: AgentRequest) -> AgentResponse:
        agent = self.get(name)
        logger.info("Dispatching to '%s'", name)
        return agent.handle(request)
