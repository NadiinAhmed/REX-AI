# Sprint 1 Test Report

Recorded when the Sprint 1 package was prepared (2026-10-16) in a Linux sandbox:
Python 3.12.3 and Python 3.11.15, pydantic 2.14.1, streamlit 1.66.0, pytest 9.1.1, anthropic 1.15.0.

**The team must re-run these checks on their own machines and on the merged `main`, and
record those results below.** Results from the preparation sandbox are not a substitute.

## Automated tests — `python -m pytest` → 63 passed (on both Python 3.11 and 3.12)

| File | Owner | Tests | What it covers |
|---|---|---|---|
| test_course_model.py | Role 2 | 7 | schema validation, unknown values, currency, duplicate IDs, synthetic labelling |
| test_loader.py | Role 2 | 5 | missing file, invalid JSON, schema errors, empty catalog |
| test_retriever.py | Role 2 | 11 | EN/AR ranking, no-match, filters, lookups, source metadata, Arabic normalisation |
| test_learner_profile.py | Role 1 | 6 | EN/AR extraction, background vs goal, contact details, profile merging |
| test_recommendation.py | Role 1 | 5 | explanations, weak-match removal, relaxed filters, unknown fees, no match |
| test_rex_assistant.py | Role 1 | 15 | intents, bilingual replies, unavailable info, ordinals, clarification, LLM grounding, LLM failure fallback |
| test_llm_service.py | Role 1 | 2 | disabled provider, missing key |
| test_config.py | Role 3 | 3 | defaults, invalid values, secret not in repr |
| test_registry.py | Role 3 | 4 | dispatch, planned agents, unknown/duplicate, request validation |
| test_integration.py | Role 3 | 3 | full bilingual conversation through the registry, bad data path |
| test_app_ui.py | Role 3 | 2 | headless Streamlit: app starts, synthetic warning, chat round trip |

## Merge-order verification
The exact commands in the three role guides were replayed against a copy of the real repository
(`NadiinAhmed/REX-AI`, initial commit `4008986` with `README.md` and empty `.gitignore` / `.env.example`).
Each PR was merged with a merge commit; tests on `main` after each merge:

| After | Copy-script result | Tests on `main` |
|---|---|---|
| PR1 Role 3 foundation | `.gitignore`, `.env.example` SKIPPED (empty on `main`) → `--overwrite` | 7 passed |
| PR2 Role 2 knowledge base | all copied | 30 passed |
| PR3 Role 1 REX Assistant | all copied | 58 passed |
| PR4 Role 3 integration | `README.md` SKIPPED (original on `main`) → `--overwrite` | 63 passed |

After every student's `git add` commands, `git status` was clean. A fresh clone of the final `main`
passed 63 tests and was identical to the package. This replay ran in a sandbox, not on GitHub itself.

## Manual / runtime checks
| Check | Result |
|---|---|
| `streamlit run app.py --server.headless true` | Server started; `/` returned HTTP 200; `/_stcore/health` returned `ok`; no errors in log |
| LLM provider with an invalid API key (real network call) | API returned `AuthenticationError`; assistant fell back, labelled the answer "rule-based"; key not logged |
| LLM answer quality with a valid key | **Not tested** (no key available) |
| Windows / macOS | **Not tested** (Linux sandbox only) |
| Test conversations in the role guides | Each message run through the registry; results match the guide tables |
| Manual demo script in README | To be performed by the team in a browser |

## Team verification log
| Date | Who | Branch / commit | `pytest` result | App demo OK? |
|---|---|---|---|---|
| | | | | |
