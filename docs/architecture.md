# REX AI — System Architecture (Sprint 1)

## Component diagram

```mermaid
flowchart TB
    subgraph UI["Presentation (Role 3)"]
        APP["app.py<br/>Streamlit chat UI"]
    end
    subgraph CORE["Coordination (Role 3)"]
        BOOT["bootstrap.py<br/>composition root"]
        REG["AgentRegistry<br/>router"]
        CFG["config.py<br/>env settings"]
    end
    subgraph AGENTS["Agents"]
        REX["REX Assistant ✅<br/>(Role 1)"]
        RFP["RFP Research 🕓"]
        CUR["Curriculum 🕓"]
        DOC["Document Gen 🕓"]
        MKT["Marketing 🕓"]
    end
    subgraph WF["Workflows (Role 1)"]
        PROF["learner_profile.py<br/>needs extraction"]
        RECO["recommendation.py<br/>scoring + reasons"]
    end
    subgraph KB["Knowledge base (Role 2)"]
        RET["CourseRetriever<br/>KeywordRetriever"]
        LOAD["loader.py<br/>validation"]
        MODEL["Course model"]
        DATA[("data/courses.json<br/>SYNTHETIC demo")]
    end
    subgraph SVC["Services (Role 1)"]
        LLM["LLMService interface"]
        ANT["Anthropic provider"]
        NONE["None → deterministic mode"]
    end

    APP --> BOOT --> REG
    CFG --> BOOT
    REG --> REX
    REG -.planned.-> RFP & CUR & DOC & MKT
    REX --> PROF & RECO
    RECO --> RET
    REX --> RET
    RET --> MODEL
    LOAD --> MODEL
    LOAD --> DATA
    REX --> LLM
    LLM --> ANT
    LLM --> NONE
```

## Request flow (one chat turn)

```mermaid
sequenceDiagram
    participant U as Learner
    participant A as app.py
    participant R as AgentRegistry
    participant X as RexAssistant
    participant K as CourseRetriever
    participant L as LLMService
    U->>A: message (AR/EN)
    A->>R: dispatch("rex_assistant", AgentRequest(message, state))
    R->>X: handle(request)
    X->>X: detect language, refuse contact details, update profile
    alt course named / referenced
        X->>K: find_by_title / get_by_id
    else enough info to recommend
        X->>K: search(query, filters)
        K-->>X: RetrievalResult[] (+ source metadata)
    else missing info
        X-->>A: follow-up questions (deterministic)
    end
    opt LLM configured
        X->>L: generate(system rules, <course_data> as data)
        L-->>X: text, or LLMError → deterministic fallback
    end
    X-->>A: AgentResponse(text, mode, sources, state)
    A-->>U: answer + mode badge + sources
```

## Key design decisions

| Decision | Why | Upgrade path |
|---|---|---|
| Keyword retrieval with Arabic normalisation + bilingual synonyms | Small catalog, no infra, fully testable | Implement `CourseRetriever` with embeddings; assistant unchanged |
| Deterministic layer always computes the facts | Facts never depend on the LLM; app works without an API key | LLM only rewrites grounded facts |
| Structured match reasons (`Reason(code, facts)`) | Bilingual rendering, testable, safe LLM context | Add reason codes as rules grow |
| Topic follows the learner's latest statement | Avoids stale interests producing wrong recommendations | Add explicit "add to my interests" handling |
| State returned in `AgentResponse.state` | Agents stay stateless and testable | Persist state in SQLite for lead management |
| `ResponseMode` shown in UI | Never pass rule-based text off as LLM output | — |

## Out of scope for Sprint 1
Lead capture (needs a consent flow), embeddings/vector DB, the four other agents, deployment, CI.
