"""MCP: the rex-academy server's tools, validation and errors, and Agent 1 running over a real
stdio connection (separate process). No mocks of the MCP SDK."""
import asyncio

import pytest
from mcp import Client

from src.agents.base import AgentRequest
from src.agents.rex_assistant import RexAssistant
from src.bootstrap import build_app
from src.config import load_settings
from src.mcp_client.academy_client import AcademyMcpClient, McpCourseRetriever, McpError, McpToolError
from src.mcp_server.academy_server import create_server
from tests.conftest import OFFICIAL_DATASET

EXPECTED_TOOLS = {"list_courses", "search_courses", "get_course", "search_knowledge_base", "list_documents"}


@pytest.fixture
def env(test_env):
    e = test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))
    build_app(load_settings(e))          # seed the temp DB + index the academy PDF once
    return e


def run(coro):
    return asyncio.run(coro)


def test_tools_are_listed_read_only_with_schemas(env):
    async def go():
        async with Client(create_server(load_settings(env))) as c:     # in-process connection
            return (await c.list_tools()).tools
    tools = {t.name: t for t in run(go())}
    assert set(tools) == EXPECTED_TOOLS
    for t in tools.values():
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
    assert tools["get_course"].input_schema["properties"]["course_id"]["pattern"]


def test_tool_calls_and_errors(env):
    async def go():
        async with Client(create_server(load_settings(env))) as c:
            ok = await c.call_tool("get_course", {"course_id": "trk-002"})
            missing = await c.call_tool("get_course", {"course_id": "ZZZ-999"})
            injected = await c.call_tool("get_course", {"course_id": "1; DROP TABLE courses"})
            too_long = await c.call_tool("search_courses", {"query": "x" * 301})
            kb = await c.call_tool("search_knowledge_base", {"query": "contact email for partnerships"})
            res = await c.read_resource("course://TRK-005")
            return ok, missing, injected, too_long, kb, res
    ok, missing, injected, too_long, kb, res = run(go())
    assert ok.structured_content["title"] == "Flutter Development" and ok.structured_content["fee"]["amount"] is None
    assert missing.is_error and "No approved course with id ZZZ-999" in missing.content[0].text
    assert injected.is_error and too_long.is_error                    # rejected by the input schema
    assert kb.structured_content["found"] and kb.structured_content["passages"][0]["page"] == 9
    assert "Data Science" in res.contents[0].text


def test_agent1_runs_over_a_real_stdio_mcp_connection(env):
    settings = load_settings(env)
    retriever = McpCourseRetriever.connect(settings)          # spawns `python -m src.mcp_server.academy_server`
    try:
        assert [c.course_id for c in retriever.all_courses()] == ["TRK-001", "TRK-002", "TRK-003", "TRK-004", "TRK-005"]
        agent = RexAssistant(retriever)
        r = agent.handle(AgentRequest.from_text("I want to become a junior data engineer"))
        assert r.data["intent"] == "recommendation" and r.sources[0].course_id == "TRK-003"
        r = agent.handle(AgentRequest.from_text("how long is TRK-004?"))
        assert "140 h" in r.text
        with pytest.raises(McpToolError, match="No approved course"):
            retriever._client.call("get_course", {"course_id": "ZZZ-999"})
    finally:
        retriever.close()


def test_server_process_does_not_receive_api_keys(env):
    from src.mcp_client.academy_client import server_env
    forwarded = server_env(load_settings(env | {"XAI_API_KEY": "xai-secret", "GROQ_API_KEY": "gsk"}))
    assert not any("KEY" in k for k in forwarded) and "xai-secret" not in forwarded.values()


def test_unreachable_server_falls_back_to_direct_backend(env, monkeypatch):
    def broken(cls, settings):
        return AcademyMcpClient(["/nonexistent/python"], {}, timeout=2).connect()
    monkeypatch.setattr(McpCourseRetriever, "connect", classmethod(lambda cls, s: cls(broken(cls, s))))
    ctx = build_app(load_settings(env | {"COURSE_BACKEND": "mcp"}), enable_rag=False)
    assert ctx.course_backend_status.startswith("direct (MCP unavailable") and ctx.course_count == 5


def test_bootstrap_uses_mcp_backend_when_configured(env):
    ctx = build_app(load_settings(env | {"COURSE_BACKEND": "mcp"}), enable_rag=False)
    try:
        assert ctx.course_backend_status.startswith("MCP") and ctx.course_count == 5
        r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text("عايز اتعلم اختبار البرمجيات"))
        assert r.sources[0].course_id == "TRK-001"
    finally:
        ctx.registry.get("rex_assistant")._retriever.close()
