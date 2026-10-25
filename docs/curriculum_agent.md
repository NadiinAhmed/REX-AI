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

Gaps are never filled silently. Unpublished prerequisites stay empty; an unpublished duration becomes a clearly
labelled proposal (5 h per module + capstone); both are listed under *Gaps and assumptions* and in the PDF.

## Research step
1. **Course record** — title, department, modules, tools, capstone, deliverables, career paths.
2. **Approved documents (RAG)** — top passages for the course, cited with page. Best effort: if retrieval fails the curriculum is still built.
3. **Topic detail** — the portfolio lists topics per track but not per module. The wording is transcribed
   (`data/curriculum/track_topics.json`); **the assignment of topics to modules is a team proposal** and says so in every curriculum.
4. **RFP signals (Agent 2 → Agent 3)** — open Egypt/Saudi tenders whose `matched_course_ids` include the course.
   Demo listings are labelled *synthetic*.

## Hours
If hours are published or requested: capstone = 15 %, the rest is split across modules by topic count, rounded to half
hours; the rounding remainder goes to the heaviest module so **modules + capstone = total exactly** (tested).

## Accreditation alignment
`data/frameworks/*.json` — one file per framework. Shipped: ISTQB CTFL 4.0, CRISP-DM, SWEBOK v3, data-engineering lifecycle.
**These are reference checklists assembled by the team** (`status: reference_checklist`, `verified: false`), not the
issuers' official text. An area is *covered* with ≥ 2 distinct keyword hits, *partial* with 1, *missing* with 0. The report
lists evidence (which module), what to add, and a disclaimer. To use a real standard, add a JSON file with
`status: official_text`; no code change is needed.

> No ITIDA/ITI requirement text is in the repository, so the agent makes **no** claim about ITIDA accreditation.
> The portfolio's own statements are the academy's claims, not something this agent verified.

## Review workflow
`draft → in_review → approved` or `→ changes_requested → (edit) → draft`. Any edit creates a new revision and returns the
curriculum to *draft*. Approval needs a reviewer name and a valid curriculum (hours add up, outcomes exist, weights = 100 %).
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

Chat commands: `create a curriculum for TRK-001 [80 hours] [beginner]` · `list` · `CUR-XXXXXX` · `align CUR-XXXXXX [framework-id]` ·
`submit CUR-XXXXXX` · `frameworks` (Arabic: `أنشئ منهج TRK-001` · `قائمة` · `المعايير`).

## Discovery questions (brief §10) → inputs
Course → `course_id`/`title` · audience → `target_audience` · level → `skill_level` · outcomes → `learning_outcomes` ·
hours → `duration_hours` · framework → `framework_ids` · projects → `practical_projects`.

## Limitations
- Alignment is keyword based: it finds *mentions*, not depth of teaching. Treat it as a first pass for the instructor.
- Framework checklists are unverified reference lists (see above); frameworks and keywords are English only.
- Curriculum text is English; Arabic is used for titles and for the PDF layout (Arabic documents are produced by the Document agent).
- Derived learning outcomes are templates until an instructor edits them; the catalog publishes none.
