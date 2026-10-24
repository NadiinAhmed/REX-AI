# MCP integration

## Architecture
```
Agent 1 (RexAssistant) ──CourseRetriever protocol──► McpCourseRetriever
                                                          │  AcademyMcpClient (1 persistent stdio session,
                                                          │  background event loop, timeouts)
                                                          ▼
                                              rex-academy MCP server  (separate process)
                                                          │  same repositories/retrievers as the app
                                                          ▼
                                       shared SQLite database + Chroma vector store
```
SDK: official `mcp` Python SDK **2.3.0**. Version 2 renamed `FastMCP` to `MCPServer`
(`from mcp.server.mcpserver import MCPServer`); most online examples are v1 and will not run.

## Server: `rex-academy` (`src/mcp_server/academy_server.py`)
| Kind | Name | Input (validated) | Output |
|---|---|---|---|
| tool | `list_courses` | — | dataset + course summaries |
| tool | `search_courses` | `query` 1–300 chars, optional level/format/max_fee/currency, `top_k` 1–10 | ranked courses + matched terms |
| tool | `get_course` | `course_id` matching `^[A-Za-z]{2,5}-\d{3}$` | full `Course` record |
| tool | `search_knowledge_base` | `query` 1–300 chars, `top_k` 1–8 | passages with filename, page, score |
| tool | `list_documents` | — | approved documents |
| resource | `course://{course_id}` | — | course JSON |

All tools are annotated `readOnlyHint=true, destructiveHint=false` and return **approved** data only.
Expected errors (unknown course) raise `ToolError` and their message reaches the client; unexpected
exceptions are hidden by the SDK so internal details never leak.

**Deliberately not exposed:** saving leads, uploading/deleting documents, publishing, sending
messages. Those are consequential and stay in the app behind consent or explicit confirmation.

## MCP vs ordinary calls
| Use MCP | Use a direct call |
|---|---|
| read-only academy data shared by several agents or external clients | transactions (lead save, ingestion), UI actions |
| tools the RFP agent will reuse from existing servers (web search/fetch) | performance-sensitive inner loops |

## Configuration
- `COURSE_BACKEND=mcp` makes Agent 1 read courses through the server; default `direct`.
- `MCP_TIMEOUT_SECONDS` (default 20).
- The client starts the server with only the data settings it needs. **API keys are never passed** (the server does not call an LLM).
- If the server cannot start, the app logs it, falls back to `direct` and shows "MCP unavailable" in the sidebar.

## Run and test
```bash
python -m src.mcp_server.academy_server                 # stdio server
npx @modelcontextprotocol/inspector python -m src.mcp_server.academy_server   # interactive inspector (needs Node)
python -m pytest tests/test_mcp.py                       # in-process + real subprocess tests
```
Claude Desktop / other MCP clients (`claude_desktop_config.json`):
```json
{"mcpServers": {"rex-academy": {"command": "/path/to/REX-AI/.venv/bin/python",
  "args": ["-m", "src.mcp_server.academy_server"], "cwd": "/path/to/REX-AI"}}}
```

## Reuse by the other agents
| Agent | Tools from rex-academy | Own / external tools (later) | Needs human confirmation |
|---|---|---|---|
| RFP Research | — | existing web search/fetch MCP servers; `save_opportunity` (write, app-side) | alerts |
| Curriculum | `get_course`, `search_courses`, `search_knowledge_base` | web research | curriculum approval |
| Document Generation | `get_course`, `search_knowledge_base` (templates) | file storage in a restricted folder | emailing documents |
| Marketing | `list_courses`, `get_course` (approved only) | content generation | any publish/send |

Each agent should connect with `AcademyMcpClient.for_settings(settings)` and call only the tools in its row.
