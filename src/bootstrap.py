"""Composition root: builds the knowledge base, LLM service, agents and registry.

Used by app.py and the integration tests so both exercise the same wiring.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agents.registry import AgentRegistry
from src.agents.rex_assistant import RexAssistant
from src.config import Settings
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from src.models.course import DatasetInfo
from src.services.llm_service import LLMService, create_llm_service


@dataclass
class AppContext:
    registry: AgentRegistry
    dataset: DatasetInfo
    course_count: int
    llm_status: str


def build_app(settings: Settings, llm: LLMService | None = None, use_configured_llm: bool = True) -> AppContext:
    """Raises KnowledgeBaseError if the course data cannot be loaded.

    Pass `llm` to inject a service (tests). Otherwise the configured provider is used.
    """
    catalog = load_catalog(settings.courses_path)
    if llm is not None:
        status = f"LLM injected: {llm.name}"
    elif use_configured_llm:
        llm, status = create_llm_service(settings)
    else:
        status = "LLM disabled. Running in deterministic demo mode."

    registry = AgentRegistry()
    registry.register(RexAssistant(
        retriever=KeywordRetriever(catalog.courses), llm=llm, dataset_notice=catalog.dataset.notice,
    ))
    return AppContext(registry=registry, dataset=catalog.dataset,
                      course_count=len(catalog.courses), llm_status=status)
