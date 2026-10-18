# Team Responsibility Matrix (RACI) — Sprint 1

R = Responsible (does it) · A = Accountable (approves) · C = Consulted · I = Informed

| Deliverable | Role 1 AI & Chatbot | Role 2 Data & KB | Role 3 Arch, UI & QA |
|---|---|---|---|
| Repo, branches, config, logging | I | I | **R/A** |
| Agent contract (`base.py`) | C | I | **R/A** |
| Course schema (`course.py`) | C | **R/A** | C |
| Demo dataset (`courses.json`) | I | **R/A** | I |
| Loader + retriever | C | **R/A** | I |
| LLM service + prompts | **R/A** | I | C |
| Learner profile + recommendation | **R/A** | C | I |
| REX Assistant conversation flow | **R/A** | C | C |
| Registry, bootstrap, Streamlit UI | C | I | **R/A** |
| Unit tests (own modules) | R (assistant) | R (data) | R (config/registry) |
| Integration + UI tests | C | C | **R/A** |
| README, architecture, WBS, board | C | C | **R/A** |
| Code review of each PR | reviews Role 2 | reviews Role 1 | reviews all, integration |




