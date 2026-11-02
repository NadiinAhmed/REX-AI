"""Live check of Agent 3's optional LLM enrichment with the configured provider (e.g. Groq).

    python -m evaluation.curriculum.check_llm_enrichment                 # uses LLM_PROVIDER / LLM_MODEL from .env
    python -m evaluation.curriculum.check_llm_enrichment --courses TRK-001 TRK-003

For each course it builds the deterministic draft and the LLM-enriched draft and checks the rules the
LLM must follow: module titles, hours and tools unchanged; no accreditation claims; enriched items labelled
'llm'; and whether the agent fell back to the deterministic draft. Results go to
evaluation/curriculum/runs/<time>_llm-check/llm_check.json.

Cost: one LLM call per course (about 1-2k tokens each). Groq's free tier is rate-limited; no data is
written to the app database (a temporary one is used).
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from src.curriculum.generator import ACCREDITATION_CLAIM, DraftInputs, build_draft, enrich_with_llm
from src.curriculum.models import CurriculumRequest, Origin
from src.curriculum.topics import TrackTopics

HERE = Path(__file__).resolve().parent


def check_course(course, llm, topics: TrackTopics) -> dict:
    base = build_draft(DraftInputs(request=CurriculumRequest(course_id=course.course_id), course=course,
                                   detail=topics.for_course(course.course_id)))
    started = time.perf_counter()
    enriched = enrich_with_llm(base, llm)
    seconds = round(time.perf_counter() - started, 2)
    texts = ([o.text for o in enriched.objectives] + [o.text for o in enriched.learning_outcomes]
             + [e.text for m in enriched.modules for e in m.exercises])
    llm_items = sum(x.origin == Origin.LLM for x in [*enriched.objectives, *enriched.learning_outcomes,
                                                     *(e for m in enriched.modules for e in m.exercises)])
    checks = {
        "used_llm": enriched.generation_mode == "llm",
        "module_titles_unchanged": [m.title for m in enriched.modules] == [m.title for m in base.modules],
        "hours_unchanged": [m.hours for m in enriched.modules] == [m.hours for m in base.modules]
                           and enriched.total_hours == base.total_hours,
        "tools_unchanged": enriched.tools == base.tools,
        "no_accreditation_claims": not any(ACCREDITATION_CLAIM.search(t) for t in texts),
        "still_valid": enriched.validation_problems() == [],
    }
    return {"course_id": course.course_id, "seconds": seconds, "llm_items": llm_items, "checks": checks,
            "passed": all(checks.values()),
            "fallback_notice": next((n for n in enriched.notices if "LLM" in n and "deterministic" in n), None),
            "sample_outcomes": [o.text for o in enriched.learning_outcomes if o.origin == Origin.LLM][:3]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--courses", nargs="*", default=None, help="course ids (default: all)")
    args = p.parse_args()

    from src.bootstrap import build_app
    from src.config import load_settings
    from src.services.llm_service import create_llm_service

    tmp = Path(tempfile.mkdtemp())
    os.environ.update(DATABASE_URL=f"sqlite:///{tmp / 'llm.db'}", VECTOR_DIR=str(tmp / "vectors"), UPLOAD_DIR=str(tmp / "uploads"))
    settings = load_settings()
    llm, status = create_llm_service(settings)
    print("LLM:", status)
    if llm is None:
        raise SystemExit("No LLM configured: set LLM_PROVIDER (e.g. groq) and its API key in .env, then run again.")
    ctx = build_app(settings, llm=llm, enable_rag=False)
    courses = [c for c in ctx.courses.list_approved() if not args.courses or c.course_id in args.courses]
    results = [check_course(c, llm, TrackTopics.load()) for c in courses]

    out = HERE / "runs" / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}_llm-check"
    out.mkdir(parents=True, exist_ok=True)
    report = {"llm": status, "provider": settings.llm_provider, "model": settings.llm_model or "provider default",
              "courses": results, "passed": sum(r["passed"] for r in results), "total": len(results)}
    (out / "llm_check.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in results:
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"{r['course_id']}: {'PASS' if r['passed'] else 'FAIL ' + ', '.join(failed)} "
              f"({r['llm_items']} llm items, {r['seconds']} s){' — ' + r['fallback_notice'] if r['fallback_notice'] else ''}")
    print(f"{report['passed']}/{report['total']} passed · saved to {out}")


if __name__ == "__main__":
    main()
