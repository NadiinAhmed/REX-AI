# Shared Interfaces (Sprint 1 contract)

These signatures are agreed by all three roles. Changing them needs a PR approved by the
owner **and** the consumers.

## Agent contract — `src/agents/base.py` (owner: Role 3, consumers: Role 1)

```python
class AgentRequest(BaseModel):
    message: str                 # 1..4000 chars
    state: dict[str, Any] = {}   # value of the previous AgentResponse.state

class AgentResponse(BaseModel):
    agent_name: str
    text: str
    mode: ResponseMode           # "llm" | "deterministic" | "error"
    language: str                # "en" | "ar"
    sources: list[SourceReference]
    data: dict[str, Any]         # e.g. intent, recommendations, llm_status
    state: dict[str, Any]        # pass back in the next request

class Agent(ABC):
    name: str
    description: str
    def handle(self, request: AgentRequest) -> AgentResponse: ...
```

`AgentRegistry.register(agent)`, `.dispatch(name, request)`, `.list_agents()`.
Dispatching to a planned agent raises `AgentNotImplementedError`.

## Course schema — `src/models/course.py` (owner: Role 2, consumers: Role 1, Role 3)

`Course` fields: `course_id` (pattern `ABC-123`), `title`, `title_ar`, `department`, `description`,
`target_audience`, `skill_level`, `industry`, `duration_hours`, `duration_text`, `delivery_format`,
`prerequisites`, `learning_outcomes`, `modules`, `career_paths`, `keywords`, `fee {amount, currency}`,
`schedule`, `promotions`, `instructor`, `country`, `source {source_type, reference, last_updated, verified}`.

**Rule:** unknown = `None` / empty list. Never fill in guesses. `Course.missing_fields()` lists gaps.

## Retrieval — `src/knowledge_base/retriever.py` (owner: Role 2, consumer: Role 1)

```python
class CourseRetriever(Protocol):
    def search(self, query: str, top_k: int = 3, filters: CourseFilters | None = None) -> list[RetrievalResult]
    def get_by_id(self, course_id: str) -> Course | None
    def find_by_title(self, text: str) -> Course | None
    def all_courses(self) -> list[Course]

RetrievalResult: course, score, matched_terms
CourseFilters: skill_level, delivery_format, max_fee, currency   # UNKNOWN course values are never filtered out
```

`search` returns `[]` for no match (never raises for an unsuccessful search).
`load_catalog(path)` raises `KnowledgeBaseError` with a readable message.

## LLM — `src/services/llm_service.py` (owner: Role 1)

```python
class LLMService(Protocol):
    name: str
    def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str   # raises LLMError

create_llm_service(settings) -> tuple[LLMService | None, str]   # never raises for missing config
```

## Error conventions

| Error | Raised by | Handled by |
|---|---|---|
| `ConfigError` | `load_settings` | `app.py` shows a startup error |
| `KnowledgeBaseError` | `load_catalog` | `app.py` shows a startup error |
| `LLMError` | `LLMService.generate` | `RexAssistant` falls back to deterministic mode |
| `AgentNotImplementedError`, `UnknownAgentError` | `AgentRegistry` | caller |

Logs record intent, language, mode and course IDs. They never record message text, API keys or contact details.

## Sprint 2 changes (agreed shared-interface updates)
- `SourceReference` now describes either a course record or a document page:
  `source_type` ("course" | "document"), `course_id` (optional), `document_id`, `filename`, `page`, `score`,
  and `label()` for display. Existing course sources are unchanged.
- `Course` gained optional `tools`, `capstone`, `deliverables` (defaults keep old data valid).
- `RexAssistant(retriever, llm=None, dataset_notice="", top_k=3, passages=None, lead_service=None)`:
  the two new arguments are optional; old callers work unchanged.
- `CourseRetriever` now has three implementations: `KeywordRetriever` (direct) and `McpCourseRetriever` (over MCP).
