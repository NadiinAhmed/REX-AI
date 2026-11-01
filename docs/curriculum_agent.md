# Agent 3 — Curriculum Research & Accreditation Alignment

Designs a structured curriculum from **approved academy data**, compares it with a competency framework,
lets an instructor review and edit it, and exports a PDF. It proposes alignment; it **never** says a course
is accredited.

```
request ──▶ research ──▶ draft (deterministic) ──▶ [LLM enrichment, optional] ──▶ alignment ──▶ review ──▶ approved
              │                                                                                    │
   course record · approved PDFs · RFP signals · topic detail                      only APPROVED curricula are used by other agents
```

## What it produces (brief §3, Agent 3)
Objectives · target audience · prerequisites · learning outcomes (LO1…) · modules and topics · training hours ·
practical exercises per module · assessment scheme (weights = 100%) · capstone project · accreditation-alignment
report · PDF · review log.

## Where each fact comes from (traceability)
Every topic, outcome, prerequisite and exercise carries an `origin`:

| origin | meaning |
|---|---|
| `catalog` | the approved course record (`courses` table) |
| `document` | the approved portfolio PDF, with page (`data/curriculum/track_topics.json`) |
| `framework` | outlined from a framework area (new courses only) |
| `user` | typed by the requester or a reviewer |
| `template` | produced by a deterministic template: a **proposal**, not evidence |
| `llm` | written by the language model: a **proposal** that must be reviewed |
| `market` | a skill named in matching tenders (Agent 2): a **suggestion** listed for the reviewer, never added to the curriculum |

Gaps are never filled silently. Unpublished prerequisites stay empty; an unpublished duration becomes a clearly
labelled proposal (5 h per module + capstone); both are listed under *Gaps and assumptions* and in the PDF.

## Research step
1. **Course record** — title, department, modules, tools, capstone, deliverables, career paths.
2. **Approved documents (RAG)** — top passages for the course, cited with page. Best effort: if retrieval fails the curriculum is still built.
3. **Topic detail** — the portfolio lists topics per track but not per module. The wording is transcribed
   (`data/curriculum/track_topics.json`); **the assignment of topics to modules is a team proposal** and says so in every curriculum.
4. **RFP signals (Agent 2 → Agent 3)** — open Egypt/Saudi tenders whose `matched_course_ids` include the course.
   Demo listings are labelled *synthetic*. Two outputs, neither of which changes the curriculum's content:
   - *Market signal*: one sentence with the number of matching tenders per country.
   - *Market suggestions*: skills the matching tenders name that the curriculum does not mention (e.g. TRK-001 +
     the demo feed → *Selenium*, *test automation*). The skill vocabulary is every framework keyword plus every tool in
     the approved catalog, so no skill list is invented. Each suggestion cites the tender ids. The reviewer adds one with
     *Add a topic*; it then disappears from the list. Shown in chat, on the page and in the PDF.

   **Why tenders are context, not content:** a few tenders are one organisation's needs, matching is keyword-based and
   the current feed is synthetic. They help a reviewer decide what to prioritise or add; a person decides.

## Hours
If hours are published or requested: capstone = 15 %, the rest is split across modules by topic count, rounded to half
hours; the rounding remainder goes to the heaviest module so **modules + capstone = total exactly** (tested).

## Accreditation alignment
`data/frameworks/*.json` — one file per framework. Shipped: ISTQB CTFL 4.0, CRISP-DM, SWEBOK v3, data-engineering lifecycle.
**These are reference checklists assembled by the team** (`status: reference_checklist`, `verified: false`), not the
issuers' official text. An area is *covered* with ≥ 2 distinct keyword hits, *partial* with 1, *missing* with 0. **One
piece of text counts once:** longer keywords are matched first and the words they used cannot be reused by a shorter
keyword of the same area, so "code review" is not also "review" and "trade-offs" is not also "trade-off". The report
lists evidence (which module), what to add, and a disclaimer. To use a real standard, add a JSON file with
`status: official_text`; no code change is needed.

> No ITIDA/ITI requirement text is in the repository, so the agent makes **no** claim about ITIDA accreditation.
> The portfolio's own statements are the academy's claims, not something this agent verified.

## Evaluation of the alignment checker
`evaluation/curriculum/dataset.jsonl`: 24 cases, 61 hand-labelled (case, area) pairs, English and Arabic, across all four
frameworks. Labels describe what the text teaches (covered = 2+ distinct concepts, partial = 1, missing = 0) and were
written without looking at the keyword lists; they were drafted with an AI assistant and **should be reviewed by the team**.
Case kinds include traps the checker is expected to fail (synonyms, Arabic-only text). Free, offline, deterministic:

```bash
python -m evaluation.curriculum.run_alignment_eval --label my-change
python -m evaluation.curriculum.run_alignment_eval --compare evaluation/curriculum/results/<A> evaluation/curriculum/runs/<B>
```

| Metric (61 labelled areas) | Before overlap fix | **After** | Change |
|---|---|---|---|
| Exact accuracy | 0.770 | **0.885** | +0.115 |
| Over-claim rate (shows coverage a human would not: hides gaps) | 0.148 | **0.033** | −0.115 |
| Under-claim rate (flags a gap a human would not) | 0.082 | 0.082 | 0 |
| English (n=52) accuracy | 0.808 | 0.942 | +0.135 |
| Arabic (n=9) accuracy | 0.556 | 0.556 | 0 |

Measured 2026-11-01, results in `evaluation/curriculum/results/`. Remaining errors (7): Arabic-only topics and English
synonyms are missed (walkthrough/inspection, تنظيف البيانات); "test planning … test plan" in one sentence still counts
twice (two separate words, one concept); a generic keyword ("business review meeting" → *review*) gives a false
*partial*. These are limits of keyword matching, listed below.

**Live LLM check** (cannot run in the build sandbox: no network to the providers):
`python -m evaluation.curriculum.check_llm_enrichment` builds the deterministic and the LLM-enriched draft per course
and checks that titles, hours and tools are unchanged, no accreditation claims appear and the result is still valid.

## Review workflow
`draft → in_review → approved` or `→ changes_requested → (edit) → draft`. Any edit creates a new revision and returns the
curriculum to *draft*. Removing a module moves every learning-outcome link with its module (old number → new number),
drops the template outcome written only for the removed module, and keeps catalog/reviewer outcomes (unlinked) for the
reviewer to decide; an outcome cannot be linked to a module that does not exist. Approval needs a reviewer name and a valid curriculum (hours add up, outcomes exist, weights = 100 %).
Chat can create, show, align and submit; **approval and change requests are on the Curriculum page only** (named reviewer, admin password).

## LLM use (optional)
With `LLM_PROVIDER` set, the LLM may rewrite objectives, learning outcomes and exercises and propose topics for modules
that have none. It cannot change module titles, hours or tools; text claiming accreditation is dropped; invalid JSON or an
LLM error falls back to the deterministic draft with a notice. Without an LLM everything above still works.
*Live LLM calls have not been tested in the build environment (no network to the providers); behaviour is covered with a fake LLM.*

## Interfaces
| What | Where |
|---|---|
| Agent (chat) | `src/agents/curriculum.py` — registered as `curriculum` |
| Workflow | `src/curriculum/service.py` — `create`, `align`, `submit`, `approve`, `request_changes`, `apply_edits`, `approved_for_course` |
| Table | `curricula` (migration `0003_curriculum`) via `src/db/curriculum_repository.py` |
| Page | `pages/5_Curriculum.py` |
| PDF | `src/curriculum/export.py` + shared kit `src/pdf/` |
| **For other agents** | `CurriculumService.approved_for_course(course_id)` returns only an **approved** curriculum |
| **Over MCP** | `get_approved_curriculum(course_id)`, `list_approved_curricula()` on the `rex-academy` server (read-only, approved only). Marketing uses it when `COURSE_BACKEND=mcp`, falling back to the direct service if a call fails ([docs/mcp.md](mcp.md)) |

Chat commands: `create a curriculum for TRK-001 [80 hours] [beginner]` · `list` · `CUR-XXXXXX` · `align CUR-XXXXXX [framework-id]` ·
`submit CUR-XXXXXX` · `frameworks` (Arabic: `أنشئ منهج TRK-001` · `قائمة` · `المعايير`).

## Discovery questions (brief §10) → inputs
Course → `course_id`/`title` · audience → `target_audience` · level → `skill_level` · outcomes → `learning_outcomes` ·
hours → `duration_hours` · framework → `framework_ids` · projects → `practical_projects`.

## Limitations
- Alignment is keyword based: it finds *mentions*, not depth of teaching. Treat it as a first pass for the instructor.
  Measured accuracy 0.885 on the labelled set; synonyms and generic words still cause errors (see Evaluation).
- Framework checklists are unverified reference lists (see above); frameworks and keywords are English only, so a
  curriculum written in Arabic is under-counted (Arabic accuracy 0.556; English tool names inside Arabic text do match).
- Market suggestions use the same keyword matching: synonyms produce noise (e.g. "quality assurance" suggested for a
  course that says "QA"). They are suggestions only.
- Agent 3 itself reads the catalog and documents directly (in-process database), not over MCP: MCP is used where another
  agent or an external client needs Agent 3's output.
- No `country` is set in the official catalog, so location-specific requirements are never applied.
- Curriculum text is English; Arabic is used for titles and for the PDF layout (Arabic documents are produced by the Document agent).
- Derived learning outcomes are templates until an instructor edits them; the catalog publishes none.
