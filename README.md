# REX AI — Smart Academy Multi-Agent System

Graduation project MVP for Andalusia Academy. Five planned agents share one database, one course
catalog and one knowledge base. **Agent 1 (REX Assistant), Agent 2 (RFP Research) and Agent 5 (AI Marketing) are implemented.**

| Agent | Status |
|---|---|
| REX Assistant — bilingual course advisor with document RAG, MCP and lead capture | ✅ implemented |
| Educational RFP Research Agent — Egypt & Saudi training tenders: strict market filter, expiry, duplicates, fit, dashboard | ✅ implemented ([docs](docs/rfp_agent.md)) |
| Curriculum Research & Accreditation Agent | 🕓 planned |
| Educational Content & Document Generation Agent | 🕓 planned |
| AI Marketing Agent — campaigns from approved data, fact-checked, human approval, MCP | ✅ implemented |

## What REX Assistant does
- Chats in **Arabic and English**, asks only missing follow-up questions, recommends tracks from the
  **official academy catalog** (`data/courses_official.json`, transcribed from the 2026–2027 portfolio PDF)
  and explains each match.
- Answers course facts (hours, modules, tools, capstone, deliverables, careers) from approved records and
  says **"not available"** for anything the academy has not published (fees, schedules, instructors, format).
- Answers other questions from **approved PDFs** with **page citations**, and says when the documents don't contain the answer.
- Saves a lead **only after explicit consent** (type "register" / "سجلني").
- Flow built with **LangGraph**; LLM via **LangChain** (xAI **Grok** or Groq); works with no API key.
- Course data available to any agent or MCP client through the read-only **`rex-academy` MCP server**.
- RAG quality measured with **RAGAS** (`docs/evaluation.md`).

## What the Marketing Agent does
- Builds campaigns for **social captions, Facebook/Instagram ads, LinkedIn, email and WhatsApp**:
  headlines, CTAs, multiple variations and a simple strategy, in **Arabic or English**.
- Uses **only approved course data and approved, dated offers**. A fact-checker blocks invented prices,
  discounts, dates, formats, accreditation and guarantees; the LLM retries, then failing drafts are dropped.
- Every draft waits for a **named reviewer** (Marketing page). Nothing is published or sent by the system.
- LangGraph workflow, LangChain structured output, offers over the `rex-academy` MCP server, and a
  reproducible evaluation (`docs/marketing_agent.md`).

## Quick start (Python 3.11+)
```bash
git clone https://github.com/NadiinAhmed/REX-AI.git && cd REX-AI
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # Windows: copy .env.example .env
python -m pytest                                       # run the tests
streamlit run app.py                                   # http://localhost:8501
```
First start creates `data/rex.db`, loads the official catalog and indexes `data/documents/*.pdf`.

### Use Grok (xAI) or Groq
```
LLM_PROVIDER=xai        # or groq
XAI_API_KEY=xai-...     # or GROQ_API_KEY=gsk_...
LLM_MODEL=              # empty = default; check the provider console for current model names
```
The LLM only rewrites facts already retrieved; on any failure the answer falls back to the
deterministic one and is labelled. **Live calls were not tested in the build environment** (no network to the providers).

### Better Arabic/semantic retrieval (optional)
`pip install sentence-transformers`, then `EMBEDDING_PROVIDER=sentence_transformers` (downloads
`intfloat/multilingual-e5-small` once). Documents are re-indexed automatically. Re-run the evaluation.

## Managing documents
- UI: sidebar → **Knowledge base**: upload PDFs, approve/reject, replace, delete (with confirmation), test retrieval.
- CLI: `python -m src.cli ingest file.pdf --approve`, `list-docs`, `approve ID`, `replace ID new.pdf`,
  `remove ID --yes`, `reindex`, `check`, `ask "question"`.
- New uploads are **pending** and never used until approved. Duplicates are rejected.
- Set `ADMIN_PASSWORD` before sharing a demo (the Leads page shows personal data).

## Demo script
| # | Type | Expected |
|---|---|---|
| 1 | `Hi` | follow-up questions |
| 2 | `I'm a final-year student and want to become a QA automation engineer` | Software Testing (TRK-001) with reasons |
| 3 | `how long is it?` | 100 h |
| 4 | `How much is the Flutter Development track?` | fee **not available** |
| 5 | `What tools does Data Engineering cover?` | Airflow, Spark, Kafka, dbt… |
| 6 | `Who can I contact about partnerships?` | answer cited to **p.9** |
| 7 | `Do you offer scholarships?` | not in the academy documents |
| 8 | `عايز اشتغل مطور فلاتر` | Arabic answer, Flutter (TRK-002) |
| 9 | `What programs do you offer?` | all 5 tracks |
| 10 | `register` → `yes` → `Sara Ahmed, sara@example.com` | consent, then saved (see Leads page) |
| 11 | Marketing page → TRK-001, LinkedIn, enrollment → Generate | 3 drafts, strategy, "pending review" |
| 12 | Review tab → change the body to "only 1500 EGP" → Save & re-check | price blocked, Approve disabled |
| 13 | Offers page → 15% for TRK-002 → approve → WhatsApp campaign for TRK-002 | "Offer: … 15% (until …)" |

## RFP Research (Agent 2)
Sidebar → **RFP Opportunities** → *Run scan*. Egypt tenders are flagged for immediate review, Saudi
tenders go to the monitored pipeline, everything else is excluded with a reason. The default feed
(`data/rfp/sample_tenders.json`) is **synthetic** demo data. Chat commands: `scan`, `egypt`, `saudi`,
`RFP-XXXXXX` (Arabic: `ابحث`, `مصر`, `السعودية`). Details: [docs/rfp_agent.md](docs/rfp_agent.md).

## Documentation
[Architecture](docs/architecture.md) · [RAG workflow](docs/rag_workflow.md) ·
[Database schema](docs/database_schema.md) · [MCP](docs/mcp.md) · [Evaluation](docs/evaluation.md) ·
[Testing](docs/testing.md) · [Marketing Agent](docs/marketing_agent.md) · [Agent contract](docs/interfaces.md) · [Agent 1 audit](docs/agent1_audit.md) ·
[Git workflow](docs/git_workflow.md)

## Known limitations
- Catalog fields not published in the portfolio (fees, schedules, instructors, delivery format, level,
  Data Engineering hours) are empty until the academy provides them.
- Default embeddings match spelling, not meaning; the semantic model is supported but not yet evaluated.
- LLM answers (Grok/Groq) and LLM-judged RAGAS metrics are implemented but not yet measured.
- Scanned PDFs (no text layer) are rejected: no OCR.
- Marketing: claim detection is rule-based and misses paraphrases (measured recall 0.77 on the checker set);
  LLM-generated campaigns are implemented but not yet measured (needs `GROQ_API_KEY`).
- Admin pages use a shared password, not user accounts. SQLite + local Chroma suit one app process.
