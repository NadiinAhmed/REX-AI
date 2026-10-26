"""Agent 5 over MCP: the get_active_offers tool and the full agent over a real stdio connection."""
import asyncio
from datetime import date, timedelta

import pytest
from mcp import Client

from src.agents.base import AgentRequest
from src.bootstrap import build_app
from src.config import load_settings
from src.mcp_client.academy_client import AcademyMcpClient, McpError, McpOfferReader, McpToolError
from src.mcp_server.academy_server import create_server
from tests.conftest import OFFICIAL_DATASET

TODAY = date.today()


@pytest.fixture
def env(test_env):
    e = test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))
    ctx = build_app(load_settings(e), enable_rag=False)          # seeds the temp DB
    kw = dict(course_id="TRK-001", proposed_by="nadin", valid_from=TODAY, valid_until=TODAY + timedelta(days=5))
    approved = ctx.offers.propose(title_en="Early bird", discount_percent=15, **kw)
    ctx.offers.approve(approved, "manager")
    ctx.offers.propose(title_en="Pending offer", discount_percent=50, **kw)        # never visible
    return e


def test_get_active_offers_tool(env):
    async def go():
        async with Client(create_server(load_settings(env))) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            ok = await c.call_tool("get_active_offers", {"course_id": "trk-001"})
            none = await c.call_tool("get_active_offers", {"course_id": "TRK-002"})
            missing = await c.call_tool("get_active_offers", {"course_id": "ZZZ-999"})
            injected = await c.call_tool("get_active_offers", {"course_id": "TRK-001'; DROP TABLE x"})
            return tools, ok, none, missing, injected
    tools, ok, none, missing, injected = asyncio.run(go())
    assert tools["get_active_offers"].annotations.read_only_hint is True
    offers = ok.structured_content["offers"]
    assert [o["discount_percent"] for o in offers] == [15.0]                 # the pending 50% offer is hidden
    assert none.structured_content["offers"] == [] and "No approved" in none.structured_content["note"]
    assert missing.is_error and "ZZZ-999" in missing.content[0].text
    assert injected.is_error                                                 # rejected by the input schema


def test_marketing_agent_over_real_stdio_mcp(env):
    """COURSE_BACKEND=mcp: courses AND offers reach Agent 5 through a separate MCP server process."""
    ctx = build_app(load_settings(env | {"COURSE_BACKEND": "mcp"}), enable_rag=False)
    try:
        assert ctx.course_backend_status.startswith("MCP")
        marketing = ctx.registry.get("marketing")
        assert isinstance(marketing._facts._offers, McpOfferReader)
        r = marketing.handle(AgentRequest.from_text("Software Testing on LinkedIn for registrations"))
        assert r.data["intent"] == "campaign" and len(r.data["draft_ids"]) == 3
        assert not any("No approved, currently valid offer" in n for n in r.data["notes"])   # offer seen via MCP
    finally:
        ctx.registry.get("marketing")._facts._courses.close()


def test_offer_reader_validates_and_reports_errors(env):
    class FakeClient:
        def __init__(self, reply):
            self.reply = reply

        def call(self, tool, args):
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
    bad = {"offers": [{"offer_id": "x", "course_id": "TRK-001", "title_en": "t", "discount_percent": 500,
                       "valid_from": "2026-01-01", "valid_until": "2026-01-02"}]}
    with pytest.raises(McpError, match="invalid offer"):
        McpOfferReader(FakeClient(bad)).active_offers("TRK-001")
    with pytest.raises(McpToolError):
        McpOfferReader(FakeClient(McpToolError("No approved course"))).active_offers("ZZZ-999")
    with pytest.raises(McpError, match="today"):
        McpOfferReader(FakeClient({})).active_offers("TRK-001", on=TODAY)


def test_unreachable_mcp_server_raises_clear_error():
    with pytest.raises(McpError, match="Cannot start or reach"):
        AcademyMcpClient(["/nonexistent/python"], {}, timeout=2).connect()
