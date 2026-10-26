"""MCP client for the `rex-academy` server, plus an adapter that lets Agent 1 use it.

`McpCourseRetriever` implements the same `CourseRetriever` protocol as `KeywordRetriever`, so
REX Assistant works unchanged whether courses come from a direct call or over MCP
(COURSE_BACKEND=mcp). The other agents can reuse `AcademyMcpClient` for the same tools.

The MCP SDK is async; the app is synchronous. The client therefore runs one event loop in a
background thread and keeps ONE server connection open, instead of starting a process per call.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from typing import Any

from pydantic import ValidationError

from src.config import PROJECT_ROOT, Settings
from src.knowledge_base.retriever import CourseFilters, RetrievalResult
from src.knowledge_base.text_utils import normalize_arabic
from src.models.course import Course
from src.models.offer import Offer
from src.utils.logging_config import get_logger

logger = get_logger("mcp.client")


class McpError(Exception):
    """Base class: the MCP server is unavailable or misbehaved."""


class McpToolError(McpError):
    """The tool ran and reported an error (e.g. invalid arguments, unknown course)."""


class McpTimeoutError(McpError):
    pass


# Only what the server needs. API keys are NOT passed: the server never calls an LLM.
_FORWARDED = ("DATABASE_URL", "VECTOR_DIR", "UPLOAD_DIR", "REX_COURSES_PATH", "EMBEDDING_PROVIDER",
              "EMBEDDING_MODEL", "RETRIEVAL_TOP_K", "RETRIEVAL_MIN_SCORE", "CHUNK_SIZE", "CHUNK_OVERLAP")


def server_env(settings: Settings) -> dict[str, str]:
    values = {
        "DATABASE_URL": settings.database_url, "VECTOR_DIR": str(settings.vector_dir),
        "UPLOAD_DIR": str(settings.upload_dir), "REX_COURSES_PATH": str(settings.courses_path),
        "EMBEDDING_PROVIDER": settings.embedding_provider, "EMBEDDING_MODEL": settings.embedding_model,
        "RETRIEVAL_TOP_K": str(settings.retrieval_top_k), "RETRIEVAL_MIN_SCORE": str(settings.retrieval_min_score),
        "CHUNK_SIZE": str(settings.chunk_size), "CHUNK_OVERLAP": str(settings.chunk_overlap),
    }
    return {k: values[k] for k in _FORWARDED} | {"LLM_PROVIDER": "none", "LOG_LEVEL": "WARNING"}


class AcademyMcpClient:
    def __init__(self, command: list[str], env: dict[str, str], timeout: float = 20.0) -> None:
        self._command, self._env, self._timeout = command, env, timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="mcp-client")
        self._client: Any = None
        self._stop: asyncio.Event | None = None
        self._session_task: Any = None
        self.tool_names: list[str] = []

    @classmethod
    def for_settings(cls, settings: Settings) -> "AcademyMcpClient":
        return cls([sys.executable, "-m", "src.mcp_server.academy_server"], server_env(settings),
                   settings.mcp_timeout_seconds)

    # ---------------------------------------------------------------- lifecycle
    def connect(self) -> "AcademyMcpClient":
        self._thread.start()
        ready = asyncio.run_coroutine_threadsafe(self._open(), self._loop)
        try:
            self.tool_names = ready.result(timeout=self._timeout + 30)   # first start may build indexes
        except Exception as exc:
            self.close()
            raise McpError(f"Cannot start or reach the rex-academy MCP server ({type(exc).__name__}: {exc})") from exc
        logger.info("MCP connected, tools=%s", self.tool_names)
        return self

    async def _open(self) -> list[str]:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        params = StdioServerParameters(command=self._command[0], args=self._command[1:], env=self._env,
                                       cwd=str(PROJECT_ROOT))
        started: asyncio.Future = self._loop.create_future()
        self._stop = asyncio.Event()

        async def hold_session() -> None:
            try:
                async with Client(params, read_timeout_seconds=self._timeout) as client:
                    self._client = client
                    tools = await client.list_tools()
                    started.set_result([t.name for t in tools.tools])
                    await self._stop.wait()
            except Exception as exc:
                if not started.done():
                    started.set_exception(exc)

        self._session_task = self._loop.create_task(hold_session())
        return await started

    def close(self) -> None:
        if self._stop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop.set)
            if self._session_task is not None:
                try:
                    asyncio.run_coroutine_threadsafe(asyncio.wait_for(asyncio.shield(self._session_task), 5),
                                                     self._loop).result(timeout=6)
                except Exception:
                    pass
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ---------------------------------------------------------------- calls
    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._client is None:
            raise McpError("MCP client is not connected.")
        future = asyncio.run_coroutine_threadsafe(self._client.call_tool(tool, arguments or {}), self._loop)
        try:
            result = future.result(timeout=self._timeout)
        except TimeoutError as exc:
            future.cancel()
            raise McpTimeoutError(f"MCP tool '{tool}' timed out after {self._timeout:.0f}s") from exc
        except Exception as exc:
            raise McpError(f"MCP call '{tool}' failed ({type(exc).__name__})") from exc
        if result.is_error:
            message = " ".join(getattr(c, "text", "") for c in result.content).strip()
            raise McpToolError(message or f"Tool '{tool}' reported an error.")
        data = result.structured_content
        if not isinstance(data, dict):
            raise McpError(f"MCP tool '{tool}' returned no structured data.")
        return data


class McpCourseRetriever:
    """`CourseRetriever` backed by the rex-academy MCP server. Validates everything it receives."""

    def __init__(self, client: AcademyMcpClient) -> None:
        self._client = client
        listing = client.call("list_courses")
        self._courses = [self._course(client.call("get_course", {"course_id": c["course_id"]}))
                         for c in listing.get("courses", [])]
        self._by_id = {c.course_id: c for c in self._courses}

    @classmethod
    def connect(cls, settings: Settings) -> "McpCourseRetriever":
        return cls(AcademyMcpClient.for_settings(settings).connect())

    @staticmethod
    def _course(payload: Any) -> Course:
        try:
            return Course.model_validate(payload.get("result", payload) if isinstance(payload, dict) else payload)
        except ValidationError as exc:
            raise McpError(f"MCP server returned an invalid course record: {exc.errors()[0]['msg']}") from exc

    def search(self, query: str, top_k: int = 3, filters: CourseFilters | None = None) -> list[RetrievalResult]:
        args: dict[str, Any] = {"query": query[:300], "top_k": max(1, min(top_k, 10))}
        if filters:
            args |= {k: (v.value if hasattr(v, "value") else v)
                     for k, v in filters.model_dump().items() if v is not None}
        if not query.strip():
            return []
        data = self._client.call("search_courses", args)
        return [RetrievalResult(course=self._course(m["course"]), score=m["score"], matched_terms=m["matched_terms"])
                for m in data.get("results", [])]

    def get_by_id(self, course_id: str) -> Course | None:
        return self._by_id.get(course_id.strip().upper())

    def find_by_title(self, text: str) -> Course | None:
        upper = text.upper()
        for cid, course in self._by_id.items():
            if cid in upper:
                return course
        norm = normalize_arabic(text.lower())
        for course in self._courses:
            titles = [course.title.lower()] + ([normalize_arabic(course.title_ar)] if course.title_ar else [])
            if any(t in norm for t in titles):
                return course
        return None

    def all_courses(self) -> list[Course]:
        return list(self._courses)

    @property
    def client(self) -> AcademyMcpClient:
        """The open MCP connection, so other adapters (e.g. offers) reuse it instead of opening another."""
        return self._client

    def close(self) -> None:
        self._client.close()


class McpOfferReader:
    """`OfferReader` over the rex-academy MCP server (tool: get_active_offers). Validates every offer.

    The server decides what is active "today"; asking for another date is not supported over MCP.
    """

    def __init__(self, client: AcademyMcpClient) -> None:
        self._client = client

    def active_offers(self, course_id: str, on=None) -> list[Offer]:
        if on is not None:
            raise McpError("get_active_offers over MCP only supports today's date.")
        data = self._client.call("get_active_offers", {"course_id": course_id.strip().upper()})
        try:
            return [Offer(id=o["offer_id"], course_id=o["course_id"], title_en=o["title_en"], title_ar=o.get("title_ar"),
                          discount_percent=o.get("discount_percent"), price_amount=o.get("price_amount"),
                          currency=o.get("currency"), valid_from=o["valid_from"], valid_until=o["valid_until"],
                          approval_status="approved") for o in data.get("offers", [])]
        except (ValidationError, KeyError, TypeError) as exc:
            raise McpError(f"MCP server returned an invalid offer: {exc}") from exc
