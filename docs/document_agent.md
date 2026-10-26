# Agent 4 — Educational Content & Document Generation

Turns an **approved curriculum** (or, failing that, the course record) into ready-to-review course documents in English
or Arabic. Every document is a **draft** until a named reviewer approves it.

```
Agent 3: curriculum ──approved──▶ Agent 4: template ──▶ [optional LLM: Arabic translation, MCQs] ──▶ DRAFT ──edit──▶ approve ──▶ PDF / Markdown / e-mail
                         ▲
           course record (when no approved curriculum exists; the document says so)
```

## The ten documents (brief §3, Agent 4)
Course description · Course proposal · Curriculum PDF · Participant guide · Facilitator/instructor guide ·
Presentation outline · Question bank · Assessment rubrics · Capstone project brief · Learning outcomes & assessment plan.
Templates live in `src/documents/templates.py` (`TEMPLATE_VERSION` is printed on every document).

## What it will and will not write
- **From data only.** Text is rearranged from the curriculum / course record. A fee, schedule, instructor or delivery format that the
  academy has not published prints as **"Not published"** (tested: no currency amounts appear).
- **Proposals are labelled.** Teaching-time split, capstone milestones, rubric descriptors and question wording are template proposals,
  and say so in the document.
- **No accreditation claims.** Every document carries a disclaimer; a test scans all 10 types × 2 languages for affirmative claims.
- **Basis is printed.** The cover says "Built from: Approved curriculum CUR-… (revision n)" or "Course record … (no approved curriculum)".

## Languages
`language = en | ar`. Arabic documents are laid out right-to-left with shaped letters, mirrored tables and correct brackets
(`src/pdf/renderer.py`, font DejaVu Sans in `assets/fonts`). Headings and fixed text are written in Arabic; the catalog's Arabic course title is used.
**Course content (module titles, topics) is stored in English by the academy.** With an LLM configured it is translated
(only sentences of ≥ 3 words; tool names, IDs and numbers stay English) and flagged *"translated by an AI model: have an Arabic speaker review it"*.
Without an LLM the Arabic document keeps that content in English and says so in the reviewer notes.
The *Curriculum PDF* type is English-only (Agent 3's export) and falls back to English with a notice.

## Optional LLM use
1. **Translation to Arabic** (above). 2. **Multiple-choice questions** for the question bank (2 per module). Output is strictly validated
(4 distinct options, one valid answer index, no accreditation wording); invalid items are dropped; anything unusable falls back to the
deterministic document. MCQs are marked *AI-generated: an instructor must verify every answer*.
Without an LLM the question bank still has short-answer and practical questions whose answer guidance quotes the academy's own topic text.
*Live LLM calls were not tested in the build environment; the behaviour is covered with a fake LLM (good, malformed and failing replies).*

## Review, edit, approve
`generate → draft → edit → draft (revision+1) → approve → approved`. An edit to an approved document returns it to draft.
Editing uses Markdown (`## Heading`, `- bullet`, `1. item`, `> note`, `| table |`); cover details and the disclaimer are kept.
The printed PDF shows a **DRAFT banner and the reviewer notes** until the document is approved.
Approval needs a reviewer name. Chat can create and show documents; **approval and e-mail are on the Documents page only**.

## Downloads and e-mail
PDF and Markdown are rendered on demand from the stored text (the file can never disagree with what was reviewed).
E-mail is **off unless `SMTP_HOST` and `SMTP_FROM` are set** (see `.env.example`). Only **approved** documents can be sent (max 10 recipients,
addresses validated and de-duplicated); the PDF is attached; the log records the **count only**, never addresses. SMTP errors show a generic message.
*The SMTP path was tested with an injected transport, not against a real mail server.*

## Interfaces
| What | Where |
|---|---|
| Agent (chat) | `src/agents/document_generation.py` — registered as `document_generation` |
| Workflow | `src/documents/service.py` — `generate`, `edit_markdown`, `approve`, `pdf`, `markdown`, `email`, `list`, `get` |
| Templates | `src/documents/templates.py` (`build_document`) |
| LLM helpers | `src/documents/llm_helpers.py` |
| Table | `generated_documents` (migration `0004_documents`) |
| Page | `pages/6_Documents.py` |
| Reads from Agent 3 | `CurriculumService.approved_for_course(course_id)` / `.get(curriculum_id)` / `.draft_for_course(course_id)` |

Chat: `generate a participant guide for TRK-001` · `question bank for CUR-XXXXXX` · `types` · `list` · `DOC-XXXXXX` · `pdf DOC-XXXXXX`
(Arabic: `اعمل دليل المدرب لـ TRK-002 بالعربية` · `أنواع المستندات`).

## Discovery questions (brief §10) → inputs
Document type → `doc_type` · course/curriculum → `course_id`/`curriculum_id` · audience → `audience` · language → `language` ·
template → fixed per type (`TEMPLATE_VERSION`) · "approved sources only" → `require_approved_curriculum`.

## Limitations
- The presentation type is an **outline** (slide titles, bullets, speaker notes), not a .pptx file.
- Arabic content is only as good as the LLM translation, which a human must review.
- Rubric descriptors and milestones are generic; instructors adapt them.
- A new Arabic template sentence is a code change (`templates.py`); there is no template editor UI.
