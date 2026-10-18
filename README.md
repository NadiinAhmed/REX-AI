# REX AI — Smart Academy Multi-Agent System

Graduation project MVP for Andalusia Academy. REX AI is planned as five coordinated agents;
**Sprint 1 delivers the foundation and a working REX Assistant prototype.**

| Agent | Status |
|---|---|
| REX Assistant — bilingual course recommendations | ✅ Sprint 1 prototype |
| Educational RFP Research Agent | 🕓 Planned |
| Curriculum Research & Accreditation Agent | 🕓 Planned |
| Educational Content & Document Generation Agent | 🕓 Planned |
| AI Marketing Agent | 🕓 Planned |

> ⚠️ **The course catalog in `data/courses.json` is SYNTHETIC demo data** invented by the team.
> Courses, fees, schedules and instructors are not verified Andalusia Academy information.

## What the prototype does
- Chats in **Arabic and English** (replies in the language of each message).
- Asks follow-up questions when it doesn't yet know the learner's interests or goal.
- Extracts level, preferred format, budget and weekly hours from free text.
- Retrieves courses from the catalog and **explains each recommendation** (topic, career path,
  level, format, budget, prerequisites), flagging mismatches with ⚠.
- Answers questions about a course (fees, duration, prerequisites, instructor, schedule, content…)
  and says plainly when a value is **not available** instead of guessing.
- Says "no matching course" rather than suggesting something unrelated.
- Shows the response mode (LLM vs deterministic) and the source of every course mentioned.
- Refuses to store contact details (lead capture is deferred until a consent flow exists).

## Quick start

Requires Python 3.11+ (tested on 3.11 and 3.12).

```bash
git clone https://github.com/NadiinAhmed/REX-AI.git && cd REX-AI
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python -m pytest                   # run the tests
streamlit run app.py               # open http://localhost:8501
```

No API key is needed: with `LLM_PROVIDER=none` the assistant runs in **deterministic demo mode**
and every answer is labelled as rule-based.

### Optional: LLM mode
Set in `.env`:
```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key
```
The LLM only rewrites facts that were already retrieved from the catalog (course data is sent as
delimited data, with instructions to ignore any instructions inside it). If the call fails, the app
falls back to the deterministic answer and says so.

## Demo script (acceptance checklist)
Run `streamlit run app.py` and try, in one conversation:

| # | Type | Expected |
|---|---|---|
| 1 | `Hi` | Follow-up questions (interest, background, level, format) |
| 2 | `I'm a beginner and want to become a data analyst, online, budget 5000 EGP` | DS-101 with ✓ reasons, fee 4500 EGP, demo-data note |
| 3 | `how long is it?` | Duration of DS-101 |
| 4 | `Who is the instructor for DS-101?` | "not available in our current data" |
| 5 | `عايز اتعلم تسويق رقمي اونلاين` | Arabic answer recommending MKT-120 |
| 6 | `كم سعر أساسيات إدارة المشاريع؟` | Arabic: fee not available |
| 7 | `I want to learn cooking` | No matching course; asks for another field |
| 8 | Sidebar | Synthetic-data warning, 8 courses, 1 implemented + 4 planned agents |

## Project structure
```
app.py                       Streamlit UI (Role 3)
src/
  config.py                  env settings (Role 3)
  bootstrap.py               wires data + LLM + agents + registry (Role 3)
  agents/base.py             shared agent contract (Role 3)
  agents/registry.py         router; lists planned agents (Role 3)
  agents/rex_assistant.py    conversation flow (Role 1)
  agents/prompts.py          LLM prompts (Role 1)
  workflows/learner_profile.py   needs extraction AR/EN (Role 1)
  workflows/recommendation.py    scoring + explanations (Role 1)
  services/llm_service.py    provider-independent LLM interface (Role 1)
  models/course.py           validated course schema (Role 2)
  knowledge_base/loader.py   load + validate catalog (Role 2)
  knowledge_base/retriever.py    CourseRetriever + KeywordRetriever (Role 2)
  knowledge_base/text_utils.py   Arabic normalisation, synonyms (Role 2)
  utils/logging_config.py    logging (Role 3)
data/courses.json            SYNTHETIC demo catalog (Role 2)
tests/                       pytest suite, split by role
docs/                        architecture, interfaces, WBS, RACI, task board, git workflow, test report
```

## Branches
| Branch | Owner |
|---|---|
| `main` | Stable, reviewed code only |
| `feature/rex-assistant` | Role 1 — AI & Chatbot |
| `feature/knowledge-base` | Role 2 — Data & Knowledge Base |
| `feature/ui-architecture-qa` | Role 3 — UI, Architecture & QA |

No one commits to `main` directly: every change arrives through a reviewed pull request.


## Documentation
- [Architecture](docs/architecture.md) · [Shared interfaces](docs/interfaces.md)
- [WBS](docs/work_breakdown_structure.md) · [Responsibilities](docs/team_responsibilities.md) · [Task board](docs/task_board.md)
- [Git workflow & merge order](docs/git_workflow.md) · [Test report](docs/test_report.md)

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `REX_COURSES_PATH` | `data/courses.json` | Catalog path (relative to project root) |
| `LLM_PROVIDER` | `none` | `none` or `anthropic` |
| `LLM_MODEL` | `claude-sonnet-5-5` | Model name for the provider |
| `ANTHROPIC_API_KEY` | — | Only for `anthropic` |
| `LLM_TIMEOUT_SECONDS` | `30` | LLM request timeout |
| `LOG_LEVEL` | `INFO` | Logging level |

## Known limitations (Sprint 1)
- Synthetic catalog of 8 courses; must be replaced with official academy data.
- Keyword retrieval: understands listed synonyms, not arbitrary paraphrases.
- Profile extraction is rule-based; unusual phrasings may be missed (the assistant then asks).
- LLM mode verified only for its failure path (invalid key → labelled fallback); answer quality with a real key has not been evaluated yet.
- No lead capture, persistence, CI, or deployment yet.
