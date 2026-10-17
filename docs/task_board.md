# Sprint 1 Task Board

Status reflects code delivered in the Sprint 1 package. "Merged" is checked **only after the team merges the PR on GitHub.**

| ID | Task | Owner | Branch | Code ready | Tests | Merged |
|---|---|---|---|---|---|---|
| ARC-01 | Repo setup, .gitignore, .env.example | Role 3 | feature/ui-architecture-qa | ✅ | n/a | ☐ |
| ARC-02 | Config + logging | Role 3 | feature/ui-architecture-qa | ✅ | ✅ | ☐ |
| ARC-03 | PR template; protect `main` in GitHub settings | Role 3 | feature/ui-architecture-qa | ✅ template | n/a | ☐ |
| ARC-04 | Agent contract | Role 3 | feature/ui-architecture-qa | ✅ | ✅ | ☐ |
| ARC-05 | Agent registry/router | Role 3 | feature/ui-architecture-qa | ✅ | ✅ | ☐ |
| KB-01 | Course model | Role 2 | feature/knowledge-base | ✅ | ✅ | ☐ |
| KB-02 | Retriever interface | Role 2 | feature/knowledge-base | ✅ | ✅ | ☐ |
| KB-03 | Synthetic demo dataset (8 courses) | Role 2 | feature/knowledge-base | ✅ | ✅ | ☐ |
| KB-04 | Loader + error messages | Role 2 | feature/knowledge-base | ✅ | ✅ | ☐ |
| KB-05 | Bilingual keyword retrieval + filters | Role 2 | feature/knowledge-base | ✅ | ✅ | ☐ |
| KB-06 | Data/retrieval tests | Role 2 | feature/knowledge-base | ✅ | ✅ | ☐ |
| AI-01 | LLM interface + Anthropic provider | Role 1 | feature/rex-assistant | ✅ | ✅ (fake LLM + invalid-key check) | ☐ |
| AI-02 | Learner profile extraction | Role 1 | feature/rex-assistant | ✅ | ✅ | ☐ |
| AI-03 | Recommendation + explanations | Role 1 | feature/rex-assistant | ✅ | ✅ | ☐ |
| AI-04 | Conversation flow + fallback | Role 1 | feature/rex-assistant | ✅ | ✅ | ☐ |
| AI-05 | Assistant tests | Role 1 | feature/rex-assistant | ✅ | ✅ | ☐ |
| ARC-06 | Bootstrap + Streamlit UI | Role 3 | feature/ui-architecture-qa | ✅ | ✅ | ☐ |
| QA-01 | Integration + UI tests | Role 3 | feature/ui-architecture-qa | ✅ | ✅ | ☐ |
| QA-02 | Acceptance checklist + test report | Role 3 | feature/ui-architecture-qa | ✅ | n/a | ☐ |
| DOC-01..03 | README, architecture, WBS, RACI, board | Role 3 | feature/ui-architecture-qa | ✅ | n/a | ☐ |

## Backlog (Sprint 2+)
- Real Andalusia Academy course data (replace synthetic dataset; set `source_type: official`)
- Lead capture with an explicit consent step
- CI (GitHub Actions running `pytest`) and required status checks
- Live LLM evaluation with a real API key (prompt quality, Arabic tone)
- Embedding retriever implementing `CourseRetriever`
- Start of RFP Research Agent
