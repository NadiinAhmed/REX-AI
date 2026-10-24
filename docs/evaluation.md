# RAG evaluation with RAGAS

## Metrics in simple words
| Metric | Question it answers | Needs an LLM? |
|---|---|---|
| **ID context recall** | Did we retrieve the page(s) that contain the answer? | no |
| **ID context precision** | Of the passages we retrieved, how many came from an answer page (ranked higher = better)? | no |
| **Non-LLM context recall / precision** | Same idea, measured by text similarity between retrieved chunks and the answer pages' text | no |
| **Faithfulness** | Is every claim in the answer supported by the retrieved passages (no invention)? | yes |
| **Answer relevancy** | Does the answer actually address the question? | yes (+ embeddings) |
| **Context precision / recall (LLM)** | Same as above, judged by an LLM against the reference answer | yes |
| **REX abstention rate** (custom, not RAGAS) | On questions the documents cannot answer, did we say so? | no |

Analogy: retrieval metrics grade the *librarian* (did they bring the right pages?); faithfulness
and relevancy grade the *writer* (did they answer from those pages, and answer the question?).

## Dataset — `evaluation/dataset.jsonl`
22 questions written against the Andalusia Academy portfolio PDF: 11 English and 5 Arabic
answerable questions (each with a reference answer and the page numbers that contain it), and
6 unanswerable ones (fees, instructor, start date, scholarships, online/offline, refunds — none
appear in the PDF).

## How to run
```bash
pip install -r requirements.txt -r requirements-eval.txt
python -m evaluation.run_ragas --label my-change                       # free, offline
python -m evaluation.run_ragas --label my-change --set CHUNK_SIZE=500  # try a setting
python -m evaluation.compare evaluation/results/20261023-055332_baseline-hybrid evaluation/runs/<new>
python -m evaluation.run_ragas --label grok --judge                    # LLM metrics, see costs below
```
Each run uses a temporary database/vector store, writes `config.json`, `summary.json`,
`scores.csv`, `samples.jsonl` to `evaluation/runs/<id>/`, and logs the run in `evaluation_runs`.

## Measured results (2026-10-23, commit 939837b)
Configuration: RAGAS 0.4.3 · embeddings `hashing-char3to5-2048` · generation: none (deterministic)
· chunk 800 / overlap 120 · top_k 4 · min_score 0.15 · 22 questions (16 answerable, 6 unanswerable).

| Metric | Vector only | **Baseline: + keyword gate** | Change |
|---|---|---|---|
| ID context recall (all, n=16) | 0.950 | **0.919** | −0.031 |
| ID context recall — English (n=11) | 0.927 | **0.927** | 0 |
| ID context recall — Arabic (n=5) | 1.000 | **0.900** | −0.100 |
| ID context precision | 0.578 | **0.547** | −0.031 |
| Non-LLM context precision | 0.901 | **0.911** | +0.010 |
| Non-LLM context recall | 0.648 | **0.633** | −0.016 |
| Answered rate on answerable | 1.000 | **1.000** | 0 |
| **Correct abstention on unanswerable** | 0.167 | **0.667** | **+0.500** |

Raw files: `evaluation/results/`.

### Interpretation
- The right page is retrieved for almost every answerable question, in both languages.
- Precision ≈ 0.55 because 4 passages are returned while most answers sit on one page.
- The keyword gate is a deliberate trade-off: much more honest "not found" behaviour for a small recall loss (one Arabic question lost its second reference page).
- The two unanswerable questions still retrieved passages ("Flutter cost", "Data Engineering instructor") because the course name matches; in the chat these route to the structured course record, which answers "not available". An LLM generator is also instructed to say so.
- `en10` (certificate) scores 0.2 partly by dataset design: any one of 5 pages answers it, but all 5 are listed as references.

### Not measured yet (needs an API key — never estimated)
Faithfulness, answer relevancy and LLM-judged context metrics. Run with `--judge` once
`LLM_PROVIDER` and its key are set. Cost: roughly 3–10 judge calls per answerable question
(≈50–160 calls per run); xAI is paid per token, Groq's free tier may rate-limit a run.

## Limitations
- One source document, 22 questions: enough to compare configurations, not to claim general quality.
- Reference answers and pages were written by the team, in this sandbox, from the PDF.
- Deterministic generation returns excerpts, so generation metrics are only meaningful with an LLM.
- The semantic embedding (`sentence_transformers`) was not evaluated: it requires a model download that was not possible in the build environment. Re-run the baseline with `--set EMBEDDING_PROVIDER=sentence_transformers` and re-calibrate `RETRIEVAL_MIN_SCORE`.
