# Testing

```bash
pip install -r requirements-dev.txt
python -m pytest              # whole suite (≈1 minute; the MCP tests start a real server process)
python -m pytest tests/test_mcp.py -v
```

| File | Covers |
|---|---|
| test_audit_regressions.py | the four audit bugs (B1–B4) reproduced and fixed |
| test_rex_assistant.py, test_learner_profile.py, test_recommendation.py | Sprint 1 conversation behaviour (unchanged, still passing on the LangGraph version) |
| test_rex_documents_and_leads.py | official catalog, document Q&A with page citations, "not found", catalog overview, consent flow, no PII in logs |
| test_ingestion.py | multiple PDFs, page metadata, pending vs approved, duplicates, non-PDF / corrupt / empty / text-less / oversized files, replace, remove, rollback on failure, the real portfolio PDF |
| test_database.py | Alembic migration, catalog replace, cascade delete, leads, official data has no invented values |
| test_llm_providers.py | Grok (ChatXAI) and Groq (ChatGroq) built via LangChain, missing keys, error normalisation, secrets not in repr |
| test_mcp.py | tool listing/annotations, schema validation, errors, a real stdio subprocess serving Agent 1, keys not forwarded, fallback |
| test_integration.py, test_app_ui.py | composition root and headless Streamlit chat |
| test_marketing_data.py | Agent 5 models, offer validation, migration 0003 up/down, offers survive reseed, atomic campaign save |
| test_marketing_rules.py | fact-checker EN/AR blocks and allows, brief parser, strategy, facts provider, curriculum interface, evaluation regressions |
| test_marketing_agent.py | structured output / JSON / malformed / LLM failure, every LangGraph route, retry then drop, approval gate, no publish code, evaluator, judge |
| test_marketing_mcp.py | `get_active_offers` tool, Agent 5 over a real stdio MCP process, invalid MCP data, unreachable server |
| test_marketing_ui.py, test_marketing_eval.py | headless Marketing/Offers pages (generate, approve, errors) and the evaluation pipeline |

Test PDFs are generated with reportlab inside the tests.

## Manual verification
1. `streamlit run app.py` and follow the demo script in the README.
2. Knowledge base page: upload a PDF without "approve" → ask about it → not used; approve → cited.
3. Upload the same PDF twice → duplicate message. Delete it → "Check consistency" stays green.
4. `COURSE_BACKEND=mcp` in `.env` → sidebar shows "Courses: 5 (MCP …)" and recommendations still work.
5. Set `LLM_PROVIDER=xai` + key → answers show "LLM-generated"; remove the key → labelled rule-based fallback.

6. Marketing: follow the manual check in `docs/marketing_agent.md` §10.

## Not covered by automated tests
Live Grok/Groq calls (including Agent 5 structured output), the semantic embedding model, Windows/macOS, browser rendering of Arabic RTL.
