# Role 2 — Knowledge Base & Data Guide

**Branch:** `feature/knowledge-base` · **Your PR:** PR2 (after PR1 is merged) · **Repository:** https://github.com/NadiinAhmed/REX-AI

> **In one sentence:** you are the academy's librarian. You decide what a course record must contain, make sure no record is wrong or made up, and find the right courses when the chatbot asks.

---

## 1. Role overview and Sprint 1 goal

The chatbot (Role 1) is only allowed to say things that are in **your** data. If a fee is missing in your data, the chatbot must say "not available", never guess. Your Sprint 1 goal: a **validated, clearly labelled demo catalog** plus a **search** that finds the right courses in Arabic and English, merged into `main` through PR2.

**Key words used in this guide**

| Term | Meaning |
|---|---|
| **Knowledge base** | The stored information the chatbot is allowed to use: here, the course catalog. |
| **Schema** | The rules for what a record must look like: field names, types, allowed values. |
| **Validation** | Checking that data follows the schema before using it. |
| **Pydantic** | A Python library that turns a schema class into automatic validation. |
| **JSON** | A text format for structured data (`{"key": "value"}`). |
| **Retrieval / retriever** | Finding the records that match a question; the code that does it. |
| **Token** | One word after cleaning, e.g. `"البرمجة"` → `programming`. |
| **Normalization** | Making different spellings look the same (أ / إ / آ → ا). |
| **Synthetic data** | Invented demo data, not real academy information. |

## 2. Responsibilities and scope

**You do:**
- Understand and maintain the course schema, the demo dataset, the loader and the retriever.
- Make sure unknown values stay unknown (`null`), never guessed.
- Test valid searches, Arabic/English searches, missing data, no-match searches and broken files.
- Review Role 1's PR3: do they use your retriever only as agreed?

**You do NOT:**
- Write chatbot answers or decide how results are explained (Role 1: `rex_assistant.py`, `recommendation.py`).
- Change the UI or the agent contract (Role 3).
- Add real Andalusia Academy prices, schedules or instructors that nobody has confirmed with the academy.

## 3. Your files

| File | What it does |
|---|---|
| `data/courses.json` | The catalog: a `dataset` header (marked synthetic) and 8 demo courses. |
| `src/models/course.py` | The schema. `Course`, `Fee`, `SourceMetadata`, `CourseCatalog`, plus the choices `SkillLevel`, `DeliveryFormat`, `SourceType`. |
| `src/knowledge_base/loader.py` | `load_catalog(path)`: reads the JSON, validates it, raises `KnowledgeBaseError` with a readable message if anything is wrong. |
| `src/knowledge_base/text_utils.py` | Bilingual text cleaning: `normalize_arabic()`, `tokenize()`, `contains_arabic()`, the `STOPWORDS` set and the `SYNONYMS` map. |
| `src/knowledge_base/retriever.py` | Search. The `CourseRetriever` contract and the `KeywordRetriever` implementation, plus `CourseFilters` and `RetrievalResult`. |
| `tests/test_course_model.py` | 7 schema tests. |
| `tests/test_loader.py` | 5 loading/error tests. |
| `tests/test_retriever.py` | 11 search tests. |
| `src/models/__init__.py`, `src/knowledge_base/__init__.py` | Empty files that make the folders importable Python packages. |

Exact list: `docs/sprint1_handoff/pr2_role2_knowledge_base.txt`.

## 4. Knowledge-base data flow

```
data/courses.json
      │  load_catalog(path)                        loader.py
      ▼
CourseCatalog  (validated by Pydantic)             course.py
      │  KeywordRetriever(catalog.courses)          retriever.py
      │     builds an index: course → {token: weight}
      ▼
retriever.search("I want to become a data analyst")
      │  tokenize() → ["data", "analysis"]          text_utils.py
      │  score each course, apply filters, sort
      ▼
[RetrievalResult(course=DS-101, score=6.0, matched_terms=["analysis","data"]), ...]
      │
      ▼
Role 1's chatbot explains these results to the learner
```

The loading happens **once**, when the app starts (`src/bootstrap.py`, Role 3). Searching happens on every message.

## 5. The course schema

One real record from `data/courses.json`, shortened:

```json
{
  "course_id": "DS-101",
  "title": "Python for Data Analysis",
  "title_ar": "بايثون لتحليل البيانات",
  "department": "Data Science",
  "skill_level": "beginner",
  "duration_hours": 40,
  "duration_text": "8 weeks, 5 hours per week",
  "delivery_format": "online",
  "prerequisites": ["Basic computer skills"],
  "career_paths": ["Data analyst", "Business intelligence analyst"],
  "keywords": ["python", "data", "analysis", "pandas", "programming", "بيانات", "بايثون"],
  "fee": {"amount": 4500, "currency": "EGP"},
  "instructor": null,
  "source": {"source_type": "synthetic", "reference": "SYNTHETIC DEMO DATA - REX AI team",
             "last_updated": "2026-10-01", "verified": false}
}
```

**The rules the schema enforces** (`src/models/course.py`):

| Field | Rule |
|---|---|
| `course_id` | 2–5 capital letters, a dash, 3 digits: `DS-101` ✓, `ds101` ✗ |
| `title` | At least 3 characters |
| `skill_level` | One of `beginner`, `intermediate`, `advanced`, `all_levels`, `unknown` (default `unknown`) |
| `delivery_format` | One of `online`, `in_person`, `hybrid`, `unknown` (default `unknown`) |
| `duration_hours` | Greater than 0, or `null` |
| `fee.amount` | 0 or more, or `null` |
| `fee.currency` | A 3-letter code (`EGP`, `SAR`). Lowercase is auto-fixed to uppercase; `"pounds"` is rejected |
| `source` | **Required.** `source_type` is `synthetic`, `official` or `website` |
| Whole catalog | At least 1 course, no duplicate `course_id` |

**The golden rule: unknown = `null` (or `[]` for lists). Never guess.**

`Course.missing_fields()` lists what is unknown. For PM-150 *Project Management Fundamentals* it returns `['prerequisites', 'schedule', 'promotions', 'instructor', 'fee']`.

**Known ambiguity (your decision, see task KB-07):** an empty `prerequisites` list is counted as *missing* by `missing_fields()`, but the chatbot shows it to learners as *"none listed"*. Those mean different things. Today the data can't tell "no prerequisites" apart from "we don't know".

## 6. How data is loaded, validated, searched and returned

### Loading and validation — `load_catalog(path)`
1. File missing? → `KnowledgeBaseError: Course file not found: …. Check REX_COURSES_PATH.`
2. Not valid JSON? → the error gives the **line number**.
3. Breaks a schema rule? → the error names the **field**, e.g. `courses.0.fee.currency`.
4. Otherwise returns a `CourseCatalog` with `.dataset` (the header) and `.courses`.

### Cleaning text — `tokenize(text)`
1. Lowercase, then `normalize_arabic()`: removes diacritics; أ/إ/آ → ا; ة → ه; ى → ي.
2. Split into words; remove `ال`, `بال`, `لل`, `وال` from the start of Arabic words.
3. Remove a glued `و` ("and") **only** if the rest is a known word, so `وظيفة` (job) is not broken into `ظيفة`.
4. Drop `STOPWORDS` (filler words like *the*, *want*, عايز, ممكن).
5. Map `SYNONYMS` to one English word: برمجة → `programming`, analyst → `analysis`.

Real outputs (measured):
```
tokenize("I want to become a data analyst") → ['data', 'analysis']
tokenize("عايز اتعلم البرمجة")              → ['programming']
tokenize("أساسيات التسويق الرقمي")           → ['اساسيات', 'marketing', 'digital']
```
Because both languages become the same English tokens, an Arabic and an English question can find the same course.

### Searching — `KeywordRetriever.search(query, top_k=3, filters=None)`
Each token found in a course scores by **where** it was found (`FIELD_WEIGHTS`):

| Field | Weight |
|---|---|
| title (+ Arabic title) | 3.0 |
| keywords | 2.5 |
| career_paths | 2.0 |
| department, learning_outcomes | 1.5 |
| modules, description, industry | 1.0 |
| target_audience | 0.5 |

A token counts once per course, using its best field. Measured example for `"I want to become a data analyst"`:
```
DS-101  6.0  ['analysis', 'data']    (both in the title: 3 + 3)
AI-201  2.5  ['data']                (in keywords)
FIN-140 2.0  ['analysis']
```
Results are sorted by score, and only `top_k` are returned. **No match returns `[]`; it never raises an error.**

**Filters** (`CourseFilters`) remove courses that clearly don't fit. A course whose value is **unknown always passes**, because we can't rule it out:
- `skill_level`: a matching level, `all_levels` or `unknown` passes.
- `delivery_format`: a matching format, `hybrid` or `unknown` passes.
- `max_fee` + `currency`: removed only if the fee is known, in the same currency, and higher.

### Other lookups
- `get_by_id("ds-101")` → DS-101 (case doesn't matter), or `None`.
- `find_by_title("كم سعر أساسيات التسويق الرقمي؟")` → MKT-120 (finds an ID or a full English/Arabic title inside a sentence).
- `all_courses()` → every course.

---

## 7. Step-by-step tasks

### Two-day schedule (same table in all three guides)

| When | Who | Task | Priority | Depends on |
|---|---|---|---|---|
| Day 1, 0:00–0:45 | All | S-01 Set up the package and run its tests | **Must Do** | — |
| Day 1, 0:45–1:15 | Role 3 + repo owner | ARC-00 Invite collaborators, create branches, protect `main` | **Must Do** | — |
| Day 1, 1:15–1:30 | All | S-02 Clone repo, set Git identity, switch to own branch | **Must Do** | ARC-00 |
| Day 1, 1:30–2:45 | Role 3 | PR1: foundation | **Must Do** | S-02 |
| Day 1, 1:30–3:30 | **Role 2** & Role 1 | Study own code in the package | **Must Do** | S-01 |
| Day 1, 2:45–3:15 | Role 1 or **Role 2** reviews, Role 3 merges | PR1 review + merge | **Must Do** | PR1 open |
| Day 1, 3:15–3:30 | All | Interface check meeting (15 min) | **Must Do** | PR1 merged |
| Day 1, 3:30–4:30 | **Role 2** (Role 1 reviews) | PR2: knowledge base | **Must Do** | PR1 merged |
| Day 1, 3:30–5:00 | Role 3 | Study `bootstrap.py`, `app.py` | **Must Do** | — |
| Day 2, 0:00–1:30 | Role 1 (**Role 2** reviews) | PR3: REX Assistant | **Must Do** | PR2 merged |
| Day 2, 0:00–1:30 | **Role 2** | KB-08 manual edge-case testing; Should Do tasks | Should Do | — |
| Day 2, 1:30–2:30 | Role 3 | PR4: UI, integration, docs | **Must Do** | PR3 merged |
| Day 2, 2:30–3:30 | All | Fresh-clone verification on every laptop | **Must Do** | PR4 merged |
| Day 2, 3:30–5:00 | All | Demo rehearsal; each explains own files | **Must Do** | — |

### Folder layout

```
projects/
  .venv/              <- Python virtual environment (outside Git)
  rex-ai-sprint1/     <- the unzipped package: READ-ONLY reference
  REX-AI/             <- your Git clone: the only place you commit
```

### S-01 — Set up the package (**Must Do**, 45 min)

Unzip `rex-ai-sprint1.zip` into `projects/`, then:

**Windows (PowerShell):**
```powershell
cd projects
python --version                    # 3.11 or newer
python -m venv .venv
.venv\Scripts\Activate.ps1
cd rex-ai-sprint1
python -m pip install -r requirements.txt
python -m pytest
```
**macOS / Linux:**
```bash
cd projects
python3 -m venv .venv
source .venv/bin/activate
cd rex-ai-sprint1
python -m pip install -r requirements.txt
python -m pytest
```
**Expected result:** `63 passed`. A **virtual environment** (`.venv`) is a private copy of Python and packages for this project. Activate it in every new terminal.

### S-02 — Clone the repo and switch to your branch (**Must Do**, 15 min, after Role 3 says the branches exist)

```bash
cd projects
git clone https://github.com/NadiinAhmed/REX-AI.git
cd REX-AI
git config user.name "Your Name"
git config user.email "the-email-on-your-github-account@example.com"
git fetch origin
git switch feature/knowledge-base
git status
```
**Expected result:** `On branch feature/knowledge-base … up to date with 'origin/feature/knowledge-base'`.
**Why the email matters:** GitHub credits commits by email. Use your own account's email so your contribution is visible under your name.

### KB-01..05 — Study your code (**Must Do**, ~2 hours, in the package folder)

Read in this order. After each file, try the "experiment" in the Python **REPL** (the interactive prompt you get by typing `python`) from inside `rex-ai-sprint1/`:

**1. `data/courses.json`** (20 min). Read DS-101 and PM-150 completely. Find 3 `null` values.

**2. `src/models/course.py`** (25 min).
```python
>>> from src.models.course import Fee
>>> Fee(amount=10, currency="egp")
Fee(amount=10.0, currency='EGP')
>>> Fee(amount=10, currency="pounds")
```
The second command fails with a `ValidationError`. That is the schema protecting the data.

**3. `src/knowledge_base/loader.py`** (15 min).
```python
>>> from src.knowledge_base.loader import load_catalog
>>> cat = load_catalog("data/courses.json")
>>> len(cat.courses), cat.dataset.is_synthetic
(8, True)
>>> cat.courses[4].missing_fields()
```

**4. `src/knowledge_base/text_utils.py`** (25 min).
```python
>>> from src.knowledge_base.text_utils import tokenize
>>> tokenize("عايز اتعلم البرمجة")
['programming']
```
Try 5 of your own sentences. **Predict the answer before pressing Enter.**

**5. `src/knowledge_base/retriever.py`** (35 min).
```python
>>> from src.knowledge_base.retriever import KeywordRetriever, CourseFilters
>>> from src.models.course import SkillLevel
>>> r = KeywordRetriever(cat.courses)
>>> [(x.course.course_id, x.score, x.matched_terms) for x in r.search("I want to become a data analyst")]
>>> r.search("cooking")
[]
>>> [x.course.course_id for x in r.search("machine learning python", filters=CourseFilters(skill_level=SkillLevel.BEGINNER))]
```
The last line excludes AI-201, because it is intermediate. Type `exit()` to leave the REPL.

**Done when:** you can answer questions 1–6 in section 15 without looking.

### PR2 — Submit your work (**Must Do**, 60 min, only after PR1 is merged) · Tasks KB-01..06

```bash
cd projects/REX-AI
git fetch origin
git switch feature/knowledge-base
git merge origin/main               # brings in PR1 (Role 3's foundation)
python ../rex-ai-sprint1/docs/sprint1_handoff/copy_files.py pr2_role2_knowledge_base
python -m pytest
```
**Expected result:** every line says `copied` (no SKIPPED), then `30 passed` (7 from PR1 + your 23).

Check, then commit in meaningful steps:
```bash
git status                          # only your files; NO .venv/, .env, __pycache__/
git add src/models tests/test_course_model.py
git commit -m "Add validated course model with source metadata [KB-01]"
git add data/courses.json
git commit -m "Add clearly labelled synthetic demo catalog [KB-03]"
git add src/knowledge_base tests/test_loader.py tests/test_retriever.py docs
git commit -m "Add catalog loader, bilingual retriever, tests and Role 2 guide [KB-02, KB-04..06]"
git status                          # expect: working tree clean
git push
```
Open the PR: https://github.com/NadiinAhmed/REX-AI/compare/main...feature/knowledge-base
→ **Create pull request** → fill in the template → paste your real `pytest` output → add **Role 1** as reviewer (they use your retriever the most).

**Done when:** Role 1 approves and Role 3 merges it, and `main` shows `30 passed`.

### Should Do tasks (Day 2, in your own files only)

**KB-07 — Decide what an empty `prerequisites` list means** (30 min). Discuss with Role 1 for 5 minutes. Options: (a) `[]` = "none required" (then remove `prerequisites` from the list in `missing_fields()`), or (b) `[]` = unknown (then Role 1 changes the "none listed" wording). Make your part of the change on your branch with a test, and open a small PR. **Verify:** a test shows the chosen behaviour, and the full suite passes.

**KB-08 — Manual edge-case report** (45 min). Run the searches in section 9 in the REPL and the app. Write down anything surprising, and send it to Role 1 / the group. **Verify:** a short list exists in the group chat or as a PR comment.

**KB-09 — Add one synonym, with a test** (30 min). Example: Egyptian Arabic "داتا" for data. Today, `tokenize("عايز اتعلم داتا")` returns `['داتا']`, which matches nothing. In `text_utils.py`, add `"داتا": "data",` to `SYNONYMS`. In `tests/test_retriever.py`, add:
```python
def test_egyptian_data_synonym(retriever):
    assert retriever.search("عايز اتعلم داتا")[0].course.course_id == "DS-101"
```
**Verify:** `python -m pytest tests/test_retriever.py` passes. Commit with message `Add Egyptian Arabic synonym for data [KB-09]`, push, and open a PR.

### Can Wait (Sprint 2)
- Replace the demo catalog with real, confirmed academy data (section 8).
- Spelling tolerance: `"pyhton"` currently finds nothing (measured).
- An embedding-based retriever implementing the same `CourseRetriever` contract.

## 8. Adding or updating course records safely

1. **Never invent real-looking academy facts.** Demo courses keep `"source_type": "synthetic"` and say so in the reference. Demo instructors must be clearly fictional, e.g. `"Demo Instructor A (fictional)"`.
2. Unknown → `null` (or `[]`). Don't fill gaps "to look complete".
3. Use a new, unique `course_id` matching the pattern (`ABC-123`).
4. Fill `keywords` with English **and** Arabic words learners would type. That's what makes search work.
5. After editing, always run:
   ```bash
   python -c "from src.knowledge_base.loader import load_catalog; print(len(load_catalog('data/courses.json').courses), 'courses OK')"
   python -m pytest
   ```
6. When the academy provides real data: `"source_type": "official"`, `"reference"` = where it came from (document name or URL), `"last_updated"` = the date you received it, `"verified": true` only if the academy confirmed it, and change the `dataset` header (`is_synthetic`, `notice`). Some tests check the demo data is labelled synthetic (`test_demo_dataset_is_labelled_synthetic`), so they must be updated in the same PR.

**Template for a new demo course:**
```json
{
  "course_id": "DEMO-201",
  "title": "Example Course Title",
  "title_ar": null,
  "department": null,
  "description": null,
  "skill_level": "unknown",
  "delivery_format": "unknown",
  "keywords": ["example", "مثال"],
  "fee": {"amount": null, "currency": null},
  "source": {"source_type": "synthetic", "reference": "SYNTHETIC DEMO DATA - REX AI team",
             "last_updated": null, "verified": false}
}
```
Every field not listed takes its "unknown" default.

## 9. Test cases

**Automated (already written, in your 3 test files):**

| Area | Tests |
|---|---|
| Valid data | `test_loads_demo_dataset`, `test_minimal_course_defaults_to_unknown_values`, `test_demo_dataset_is_labelled_synthetic` |
| Malformed data | `test_invalid_course_fields_are_rejected`, `test_currency_is_normalised_and_validated`, `test_catalog_rejects_duplicate_ids`, `test_invalid_json_reports_line`, `test_schema_error_names_the_field`, `test_empty_course_list_is_rejected`, `test_missing_file_gives_clear_error` |
| Retrieval | `test_english_query_ranks_relevant_course_first`, `test_arabic_query_matches_same_course_as_english`, `test_top_k_is_respected`, `test_lookup_by_id_and_title`, `test_results_preserve_source_metadata`, `test_arabic_normalisation_and_conjunction_prefix` |
| No match | `test_unrelated_query_returns_nothing`, `test_empty_or_stopword_query_returns_nothing` |
| Filters | `test_level_filter_excludes_mismatches`, `test_budget_filter_keeps_unknown_fees`, `test_format_filter_accepts_hybrid` |

**Manual searches (REPL, with `r` built as in KB-01..05).** Results measured when this guide was written:

| Query | Expected top result |
|---|---|
| `r.search("I want to become a data analyst")` | DS-101, then AI-201, FIN-140 |
| `r.search("تسويق رقمي")` | MKT-120 |
| `r.search("digital marketing")` | MKT-120 (same as Arabic) |
| `r.search("cybersecurity")` | SEC-210 |
| `r.search("cooking")` | `[]` |
| `r.search("pyhton")` (typo) | `[]`: known limitation |
| `r.find_by_title("how much is ds-101?")` | DS-101 |

## 10. Running your tests

```bash
python -m pytest tests/test_course_model.py tests/test_loader.py tests/test_retriever.py
python -m pytest tests/test_retriever.py -v        # one line per test
python -m pytest -k arabic                          # only tests with "arabic" in the name
python -m pytest                                    # everything (do this before every push)
```
**Expected:** your 3 files → `23 passed`. The whole suite on your branch after PR2's copy → `30 passed`.

## 11. Common errors

These messages were reproduced on purpose while writing this guide:

| You see | Cause | Fix |
|---|---|---|
| `KnowledgeBaseError: Course file courses.json is not valid JSON (line 11): Expecting ',' delimiter` | A missing comma, often at the end of the previous line | Go to that line (or the one before it), add the comma |
| `… failed validation: courses.0.fee.currency: Value error, currency must be a 3-letter ISO code such as EGP or SAR` | `courses.0` = the first course (counting from 0) | Use `EGP` / `SAR` |
| `… failed validation: courses: Value error, duplicate course_id 'DS-101'` | Two courses share an ID | Give one a new ID |
| `KnowledgeBaseError: Course file not found: … Check REX_COURSES_PATH.` | Wrong path in `.env`, or wrong folder | Run from the project root; check `.env` |
| `ModuleNotFoundError: No module named 'src'` | You ran a file directly (`python src/…/retriever.py`), or you're not in the project root | Run from the root using `python -m pytest` or the REPL |
| Arabic search finds nothing | A word not in `keywords`/`SYNONYMS` | Check `tokenize("…")`; add a keyword or synonym (KB-09) |
| `copy_files.py` prints `SKIPPED` | A file already exists with different content | Ask Role 3 before using `--overwrite` |

## 12. Git workflow for `feature/knowledge-base`

**Before you start working, every time:**
```bash
cd projects/REX-AI
git status                          # which branch? anything uncommitted?
git fetch origin
git switch feature/knowledge-base
git merge origin/main               # get the latest merged work
python -m pytest
```

**Save and share your work:**
```bash
git status
git add <the files you changed>
git commit -m "Short description of what and why [task ID]"
git push
```

**Open a PR:** https://github.com/NadiinAhmed/REX-AI/compare/main...feature/knowledge-base

**Review Role 1's PR3** (safe: detached, so you can't commit to their branch by mistake):
```bash
git fetch origin
git switch --detach origin/feature/rex-assistant
python -m pytest                    # expect 58 passed
git switch feature/knowledge-base   # go back
```
On GitHub: PR → **Files changed** → **Review changes** → Approve or Request changes, with the test result in your comment.

**Merge conflict?** Stop. Run `git merge --abort` to undo the merge safely, then ask Role 3. Never use `git push --force` or `git reset --hard`.

## 13. Your contract with Role 1

Role 1's code depends on exactly these things. **Don't change them without Role 1's approval in a PR:**

```python
load_catalog(path) -> CourseCatalog                        # raises KnowledgeBaseError
KeywordRetriever(courses: list[Course])

class CourseRetriever(Protocol):
    def search(self, query: str, top_k: int = 3,
               filters: CourseFilters | None = None) -> list[RetrievalResult]   # [] if no match, never raises
    def get_by_id(self, course_id: str) -> Course | None
    def find_by_title(self, text: str) -> Course | None
    def all_courses(self) -> list[Course]

RetrievalResult: course, score, matched_terms
CourseFilters:   skill_level, delivery_format, max_fee, currency   # unknown values always pass
Course fields:   as in section 5; unknown = None / []
text_utils:      tokenize(), normalize_arabic(), contains_arabic(), STOPWORDS   # also used by Role 1
```

**Why a Protocol?** A `Protocol` describes *what methods must exist*, not how they work. In Sprint 2, a smarter `EmbeddingRetriever` can replace `KeywordRetriever` without any change to Role 1's code.

The full shared contract is in `docs/interfaces.md`.

## 14. Deliverables and definition of done

- [ ] S-01: package tests pass on your laptop (`63 passed`)
- [ ] I can explain every file in section 3
- [ ] PR2 opened with real test output, approved by Role 1, merged; `main` shows `30 passed`
- [ ] At least 2 meaningful commits under my own GitHub account
- [ ] PR3 reviewed (ran the tests, left a comment)
- [ ] Should Do: KB-07, KB-08, KB-09
- [ ] I can answer the questions below

**Done** means: merged through a reviewed PR, tests pass on `main`, demo data is clearly labelled, and you can explain it without reading it.

## 15. Questions you should be able to answer

1. **Why is an unknown value `null` instead of a reasonable guess?** → The chatbot repeats our data to learners. A guessed fee becomes a false promise; `null` makes it say "not available".
2. **How does the app know the data is synthetic?** → The `dataset.is_synthetic` header, `source_type: "synthetic"` on every course, and the UI warning.
3. **What happens if someone writes `"currency": "pounds"`?** → Validation fails at startup with an error naming the field; the app shows a clear error instead of running with bad data.
4. **How can an Arabic and an English question find the same course?** → Normalization plus `SYNONYMS` turn both into the same English tokens.
5. **Why is DS-101 ranked above AI-201 for "data analyst"?** → Its matches are in the title (weight 3 each = 6.0); AI-201 matches only a keyword (2.5).
6. **Why does a course with an unknown fee survive a budget filter?** → We can't prove it's too expensive, so we don't hide it; Role 1's chatbot flags "fee not available".
7. **What does `search` return when nothing matches, and why not an error?** → `[]`. "No match" is a normal answer, not a failure.
8. **What is a Protocol, and why is the retriever one?** → A contract of methods, so the search engine can be upgraded without touching the chatbot.
9. **What are the limits of keyword search?** → No spelling tolerance ("pyhton"), only listed synonyms, no real meaning understanding.
10. **What must change when real academy data arrives?** → Section 8, step 6.
