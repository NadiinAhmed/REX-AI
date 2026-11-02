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

## Still open
- Short names come from the course titles. Nicknames that are not in a title ("QA", "mobile", "دوت نت") are
  not matched yet; the learner gets a recommendation or is asked which course instead.
`docs/ROLE_3_ARCHITECTURE_INTEGRATION_QA_GUIDE.md` is still tracked although other role guides were
removed; README/RACI no longer use role ownership in new docs. Deleting the file is a team decision.
