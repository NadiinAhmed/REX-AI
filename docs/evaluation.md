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

## Curriculum Agent (Agent 3) evaluation
The accreditation-alignment checker is evaluated separately (no retrieval, no LLM: free and deterministic) against
hand-labelled cases: `python -m evaluation.curriculum.run_alignment_eval`. Dataset, metrics and measured results:
see [`docs/curriculum_agent.md`](curriculum_agent.md#evaluation-of-the-alignment-checker).

## Marketing Agent evaluation
Agent 5 has its own reproducible evaluation (campaign quality and fact-checker precision/recall), with
measured results: see [`docs/marketing_agent.md` §9](marketing_agent.md#9-evaluation).

## Course-recommendation evaluation (`evaluation/courses/`)

RAGAS measures the PDF answers. Recommendations are a different task (pick the right course,
or none), so they have their own small, reproducible evaluation with no LLM and no API key.

- **Dataset** `evaluation/courses/queries.jsonl`: 24 team-written requests against the 5 official
  tracks: 13 paraphrases (no course words), 5 direct requests, 6 off-topic requests (cooking,
  nursing, law, carpentry), in English and Egyptian Arabic. Small: treat differences of one or two
  queries as noise.
- **Metrics:** hit@1, hit@3 (expected course in the first three), abstention on off-topic requests,
  and balanced = (hit@3 + abstention) / 2.
- **Runs** go to `evaluation/courses/runs/` (ignored by git); results worth keeping are copied
  to `evaluation/courses/results/`.

| Run | Retriever | hit@1 | hit@3 | hit@3 paraphrase | hit@3 direct | abstention |
|---|---|---|---|---|---|---|
| `20261102-012850_keyword-baseline` | keyword | 0.667 | 0.667 | 0.538 | 1.000 | 1.000 |
| `20261102-e5-small-scan` (min 0.84) | semantic, multilingual-e5-small | 0.778 | 0.778 | 0.692 | 1.000 | 1.000 |

Keyword search finds every direct request and never recommends a course for an off-topic one, but
misses half the paraphrases. Example: "I like breaking apps to find bugs" -> Flutter (the word
"apps" maps to mobile) instead of Software Testing.

**Semantic run (measured by the team on Windows, commit f26a842).** The threshold scan
(`scan.csv`) shows two separate jobs:

- *Ranking* is strong: at 0.70-0.80 every expected course is in the top 3 (hit@3 = 1.0, hit@1 0.944).
- *Deciding whether anything fits* is hard: E5 similarities are compressed into a narrow band.
  The best match of an off-topic request (cooking, nursing...) also reaches 0.80-0.84: 5 of 6 are
  rejected at 0.82, all 6 only at 0.84. Some genuine paraphrases score in that same band, so no
  threshold keeps every relevant request and rejects every off-topic one.

0.84 is the only value with full abstention and the best recall, so it is the default. The gain over
keyword is two queries out of 18 (en-08 chatbots/LLMs, ar-05 "teach the computer to predict"),
and 0.84 was chosen on the same 24 queries, so the result is promising, not proven: a held-out
query set is needed. Still wrong at 0.84: en-01 (Flutter, kept by the keyword safety net with
"apps" -> mobile), en-06, en-07, ar-01 (relevant, but below 0.84). Since this run, samples.jsonl
also records the raw similarity to every course, to diagnose such cases.

## Live LLM check (`evaluation/live_check.py`)

The test suite runs with `LLM_PROVIDER=none`, so it never shows how a real LLM phrases answers.
`python -m evaluation.live_check --label <name>` sends six fixed REX conversations (English and
Arabic recommendation, course question, follow-up, document question, unanswerable question)
through the provider in `.env` and saves mode, latency, sources and answers to
`evaluation/live/<timestamp>_<label>.json`. It uses a temporary database and costs one or two LLM
calls per case. Nothing is scored automatically: a person reads the answers. The first run
(Groq, `openai/gpt-oss-20b`, 2026-11-02) found four problems, fixed as L1-L4 in
`docs/agent1_audit.md`.

