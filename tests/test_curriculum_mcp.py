"""Agent 3 over MCP: the rex-academy server shares APPROVED curricula, and Marketing (Agent 5) reads them
over a real stdio connection. Drafts must never leave the app."""
import asyncio

import pytest
from mcp import Client

from src.bootstrap import build_app, build_curriculum_source
from src.config import load_settings
from src.curriculum.models import CurriculumRequest
from src.mcp_client.academy_client import AcademyMcpClient, McpCurriculumSource, McpError
from src.mcp_server.academy_server import create_server
from tests.conftest import OFFICIAL_DATASET


@pytest.fixture
def env(test_env):
    return test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))


def _approved(ctx, course_id="TRK-001"):
    svc = ctx.curriculum_service
    stored = svc.create(CurriculumRequest(course_id=course_id), use_llm=False)
    svc.submit(stored.id, "tester")
    return svc.approve(stored.id, "Dr. Hana")


def _call(env, tool, args=None):
    async def go():
        async with Client(create_server(load_settings(env))) as c:
            return await c.call_tool(tool, args or {})
    return asyncio.run(go())


def test_only_approved_curricula_are_shared(env):
    ctx = build_app(load_settings(env), enable_rag=False, use_configured_llm=False)
    draft = ctx.curriculum_service.create(CurriculumRequest(course_id="TRK-002"), use_llm=False)
    assert _call(env, "get_approved_curriculum", {"course_id": "TRK-002"}).structured_content["found"] is False
    approved = _approved(ctx)

    data = _call(env, "get_approved_curriculum", {"course_id": "trk-001"}).structured_content
    assert data["found"] and data["curriculum_id"] == approved.id and data["approved_by"] == "Dr. Hana"
    assert [m["title"] for m in data["modules"]] == [m.title for m in approved.curriculum.modules]
    assert all("not an accreditation" in data["note"] or "accredited" in a["disclaimer"] for a in data["alignment"])
    listing = _call(env, "list_approved_curricula").structured_content["curricula"]
    assert [x["course_id"] for x in listing] == ["TRK-001"] and draft.id not in str(listing)


def test_bad_input_is_a_tool_error_not_a_crash(env):
    build_app(load_settings(env), enable_rag=False, use_configured_llm=False)
    assert _call(env, "get_approved_curriculum", {"course_id": "ZZZ-999"}).is_error
    assert _call(env, "get_approved_curriculum", {"course_id": "1; DROP TABLE curricula"}).is_error


def test_marketing_reads_the_approved_curriculum_over_a_real_stdio_connection(env):
    settings = load_settings(env)
    ctx = build_app(settings, enable_rag=False, use_configured_llm=False)
    approved = _approved(ctx)
    client = AcademyMcpClient.for_settings(settings).connect()          # separate server process
    try:
        assert "get_approved_curriculum" in client.tool_names
        facts = McpCurriculumSource(client).approved_curriculum("TRK-001")
        assert facts.reference.startswith(f"{approved.id} rev {approved.revision}") and facts.reference.endswith("(via MCP)")
        assert facts.modules == [m.title for m in approved.curriculum.modules]
        assert McpCurriculumSource(client).approved_curriculum("TRK-003") is None      # nothing approved
    finally:
        client.close()


def test_mcp_failure_falls_back_to_the_direct_service(env):
    class Broken:
        tool_names = ["get_approved_curriculum"]

        def call(self, tool, args=None):
            raise McpError("server gone")

    ctx = build_app(load_settings(env), enable_rag=False, use_configured_llm=False)
    approved = _approved(ctx)
    retriever = type("R", (), {"client": Broken()})()
    source = build_curriculum_source(retriever, ctx.curriculum_service)
    assert isinstance(source, McpCurriculumSource)
    assert source.approved_curriculum("TRK-001").reference.startswith(approved.id)       # direct answer
    with pytest.raises(McpError):
        McpCurriculumSource(Broken()).approved_curriculum("TRK-001")                     # no fallback: explicit error


def test_marketing_uses_mcp_for_curricula_when_the_backend_is_mcp(env):
    ctx = build_app(load_settings(env | {"COURSE_BACKEND": "mcp"}), enable_rag=False, use_configured_llm=False)
    try:
        assert ctx.course_backend_status.startswith("MCP")
        source = ctx.registry.get("marketing")._facts._curriculum
        assert isinstance(source, McpCurriculumSource)
    finally:
        ctx.registry.get("rex_assistant")._retriever.close()
