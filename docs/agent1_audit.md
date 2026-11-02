# Agent 1 audit (2026-10-23) and what changed

## Confirmed bugs (all fixed, regression tests in `tests/test_audit_regressions.py`)
| # | Symptom | Cause | Fix |
|---|---|---|---|
| B1 | "get into tech… where to start" → Project Management | filler words counted as topics | stopwords |
| B2 | "build mobile apps" → 3 unrelated courses | one weak-field word was enough | `MIN_ABSOLUTE_SCORE = 2.0` |
| B3 | "make it cheaper than 3000 EGP" lost the topic | preference words replaced the topic | keep topic on preference-only turns |
| B4 | "ما هي المواعيد؟" → topic search | Arabic plural not recognised | patterns + attribute words aren't topics |
| B5 | "analyze sales numbers" no match | keyword retrieval limit | partly: synonyms; fully needs semantic embeddings |
| B6 | `.gitignore` lines in UTF-16 | encoding | rewritten as UTF-8 |
| — | `replace()` kept the old filename (found by new tests) | wrong default | uses the new file's name |
| B7 | after a recommendation, "how many hours?" / "بكام الكورس ده؟" → "not found in the documents" | leftover question words ("many", "كم", "ده") became a new topic and replaced the course | on attribute questions, leftover words that match no course are ignored (`tests/test_followup_regressions.py`) |

| B8 (2026-11-02) | "Who is the instructor for Flutter?" → recommendation list; "the data science track" not recognised | courses matched only by full title or ID | shared `CourseResolver` (`src/knowledge_base/course_resolver.py`): short names, Arabic prefixes, ambiguity check (`tests/test_course_resolver.py`) |
| B9 (2026-11-02) | "Is the software testing course online?" → raw PDF passage | "online" was not an attribute word | a format word in a question that names a course asks for the delivery format; "I prefer online" stays a preference |
| L1 (live, 2026-11-02) | Groq/gpt-oss-20b called the official catalog "demo values" | the LLM decided whether to add the demo note | the note is added by code from `is_synthetic`; the prompt forbids the LLM from adding one (`tests/test_live_findings.py`) |
| L2 (live) | "Does the academy offer scholarships…?" → "Which course? (e.g. DS-101)" | "offer" as a verb matched the discount pattern; example code hard-coded from the synthetic catalog | "offer" counts only as a noun ("any offer", "offers on TRK-001"); example code taken from the loaded catalog |
| L3 (live) | "Is the academy accredited?" began with "This isn't in our structured course catalog yet" | the not-in-catalog note was shown for every document fallback | note only when the message sounds like a program (a statement, or a question with "course/track/كورس…") |
| L4 (live) | "هل الأكاديمية معتمدة؟" → "Which course?" | "مده" (duration) matched inside "معتمده" | Arabic words in the duration/offer patterns must start a word (prefixes ال/و/ب/ل/ف allowed) |

Live checks are run with `python -m evaluation.live_check --label <name>` (see `docs/evaluation.md`).
| A1 (2026-11-02) | "How much does the Flutter Development track cost?" answered from the Flutter pages | the course name matched the pages; no page states a cost | a result needs an *evidence term*: a question word that is not a course-name word (`src/rag/retriever.py`). Abstention 4/6 → 6/6, held-out 2/6 → 6/6 |
| A2 | "international" matched "interview" | fixed 5-letter word stem | stem keeps all but the last 3 letters (min. 5) |
| A3 | a "not found" LLM answer still listed sources | the LLM wrote free text | the LLM replies `NOT_FOUND`; the code writes the message and shows no sources |
| A4 | glossary words with ئ/أ/ة never matched ("وظائف") | glossary not normalized like the question | glossary normalized at load; added ساعات → hours, وظائف → career roles |
| A5 | "مدرس" matched inside "مدرسة", "وظائف"/"ماذا سأتعلم" never matched | Arabic patterns without word boundaries or normalization | one helper `_ar()` for all Arabic attribute words (26 cases tested) |
| A6 | "QA", "mobile", "دوت نت" not recognised | nicknames not in titles | the resolver maps words through the shared search glossary |
| A7 | LLM promised "I'll check for updates", claimed "common in the job market" | no rule against it | two prompt rules (no promises, no claims outside the data) |

All A-fixes: `tests/test_agent1_final.py` (40 tests). Measurements: `docs/evaluation.md`.

## Still open
- Nicknames work only if they are in the search glossary (`src/knowledge_base/text_utils.py`).
- The evidence-term rule applies with the keyword gate (hashing embeddings). With semantic embeddings
  the `NOT_FOUND` rule (A3) is the safeguard; measure abstention there with `run_ragas` as well.
`docs/ROLE_3_ARCHITECTURE_INTEGRATION_QA_GUIDE.md` is still tracked although other role guides were
removed; README/RACI no longer use role ownership in new docs. Deleting the file is a team decision.
