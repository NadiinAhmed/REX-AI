"""Reproducible evaluation of Agent 5 (Marketing Agent).

    python -m evaluation.marketing.run_marketing_eval --label baseline              # free, offline, deterministic
    python -m evaluation.marketing.run_marketing_eval --label groq --llm            # generation with LLM_PROVIDER
    python -m evaluation.marketing.run_marketing_eval --label groq-judged --llm --judge   # + LLM-as-a-judge
    python -m evaluation.marketing.run_marketing_eval --compare <run A dir> <run B dir>

Two parts, measured separately (like retrieval vs generation in RAG):
  1. Campaigns (dataset.jsonl): the whole agent, Arabic + English, including trap requests. Each draft
     gets the 8 deterministic dimension scores; "forbidden" strings must never appear in the output.
  2. Fact-checker (checker_cases.jsonl): the ClaimValidator alone against hand-labelled ad texts,
     reported as precision / recall per claim kind. Includes paraphrases the rules are EXPECTED to miss.

The run uses its own temporary database: app data is never touched. --llm and --judge make API calls
(cost / rate limits depend on the provider; Groq's free tier is rate-limited).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.agents.base import AgentRequest
from src.bootstrap import build_app
from src.config import PROJECT_ROOT, load_settings
from src.knowledge_base.text_utils import normalize_arabic
from src.marketing.claim_validator import ClaimValidator
from src.marketing.evaluator import DIMENSIONS, LLMJudge
from src.marketing.models import CampaignBrief, DraftVariation

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
CHECKER = HERE / "checker_cases.jsonl"
RUNS = HERE / "runs"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def norm(text: str) -> str:
    return normalize_arabic(text.lower())


def build_settings(workdir: Path, use_llm: bool, overrides: dict[str, str]):
    env = dict(os.environ)
    env.update({"DATABASE_URL": f"sqlite:///{workdir / 'eval.db'}", "VECTOR_DIR": str(workdir / "vectors"),
                "UPLOAD_DIR": str(workdir / "uploads"), "COURSE_BACKEND": "direct",
                "REX_COURSES_PATH": str(PROJECT_ROOT / "data" / "courses_official.json")})
    if not use_llm:
        env["LLM_PROVIDER"] = "none"
    env.update(overrides)
    return load_settings(env)


def with_offer(ctx, spec: dict | None):
    if not spec:
        return None
    oid = ctx.offers.propose(course_id=spec["course_id"], title_en="Evaluation offer",
                             discount_percent=spec["discount_percent"], valid_from=date.today(),
                             valid_until=date.today() + timedelta(days=14), proposed_by="evaluation")
    ctx.offers.approve(oid, "evaluation")
    return oid


# ---------------------------------------------------------------- part 1: campaigns
def run_campaigns(ctx, rows: list[dict], judge: LLMJudge | None, pause: float) -> list[dict]:
    agent = ctx.registry.get("marketing")
    samples = []
    for row in rows:
        oid = with_offer(ctx, row.get("setup_offer"))
        started = time.perf_counter()
        if "brief" in row:
            brief = CampaignBrief.model_validate(row["brief"])
            r = agent.create_campaign(brief)
        else:
            r = agent.handle(AgentRequest.from_text(row["message"]))
            brief = CampaignBrief.model_validate(r.state.get("brief", {}))
        latency = round(time.perf_counter() - started, 3)
        if oid:
            ctx.offers.reject(oid, "evaluation")             # offers never leak into later cases
        variations = [DraftVariation.model_validate(v) for v in r.data.get("variations", [])]
        evaluations = r.data.get("evaluations", [])
        output = norm(" ".join(v.full_text() for v in variations))
        leaks = [f for f in row.get("forbidden", []) if norm(f) in output]
        dims: dict[str, list[float]] = defaultdict(list)
        for e in evaluations:
            for s in e["scores"]:
                if s["score"] is not None:
                    dims[s["dimension"]].append(s["score"])
        gate_violations = sum(1 for e in evaluations for s in e["scores"]
                              if s["dimension"] in ("factual_accuracy", "unsupported_claims") and s["score"] != 1.0)
        sample = {
            "id": row["id"], "lang": row["language"], "purpose": row["purpose"], "expect": row["expect"],
            "outcome": r.data.get("intent"), "outcome_ok": r.data.get("intent") == row["expect"],
            "language_ok": r.language == row["language"], "mode": r.mode.value, "variations": len(variations),
            "drafts_passed": sum(e["passed"] for e in evaluations), "gate_violations": gate_violations,
            "is_trap": bool(row.get("forbidden")), "forbidden_leaks": leaks, "attempts": r.data.get("attempts", 0),
            "dropped": r.data.get("dropped", 0), "llm_fallback": any("deterministic copy" in n for n in r.data.get("notes", [])),
            "latency_s": latency, **{f"mean_{d}": round(statistics.mean(v), 4) for d, v in dims.items()},
            "output": [v.model_dump(mode="json") for v in variations], "notes": r.data.get("notes", []),
        }
        if judge and variations:
            judged: dict[str, list[float]] = defaultdict(list)
            for v in variations:
                for s in judge.score(v, brief):
                    if s.score is not None:
                        judged[s.dimension].append(s.score)
                time.sleep(pause)
            sample.update({f"judge_{d}": round(statistics.mean(v), 4) for d, v in judged.items()})
        samples.append(sample)
        time.sleep(pause)
    return samples


# ---------------------------------------------------------------- part 2: fact-checker
def run_checker(ctx, rows: list[dict]) -> list[dict]:
    facts_provider, validator, out = ctx.registry.get("marketing").facts_provider, ClaimValidator(), []
    for row in rows:
        oid = with_offer(ctx, row.get("setup_offer"))
        facts = facts_provider.facts_for(row["course_id"])
        if oid:
            ctx.offers.reject(oid, "evaluation")
        predicted = sorted({i.kind for i in validator.validate(row["text"], facts) if i.severity == "block"})
        out.append({"id": row["id"], "text": row["text"], "expected": sorted(row["expect"]), "predicted": predicted,
                    "exact_match": predicted == sorted(row["expect"]), "note": row.get("note", "")})
    return out


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 4) if values else None


def summarize(samples: list[dict], checks: list[dict]) -> dict:
    produced = [s for s in samples if s["outcome"] == "campaign"]
    traps = [s for s in samples if s["is_trap"]]
    drafts = sum(s["variations"] for s in produced)
    campaign = {
        "cases": len(samples), "outcome_accuracy": _mean([float(s["outcome_ok"]) for s in samples]),
        "language_accuracy": _mean([float(s["language_ok"]) for s in samples]),
        "campaigns_produced": len(produced), "drafts": drafts,
        "draft_pass_rate": round(sum(s["drafts_passed"] for s in produced) / drafts, 4) if drafts else None,
        "gate_violations_shown": sum(s["gate_violations"] for s in samples),
        "trap_cases": len(traps), "trap_cases_without_leak": sum(not s["forbidden_leaks"] for s in traps),
        "forbidden_leaks": {s["id"]: s["forbidden_leaks"] for s in samples if s["forbidden_leaks"]},
        "variations_dropped": sum(s["dropped"] for s in samples),
        "llm_fallbacks": sum(s["llm_fallback"] for s in samples),
        "mean_attempts": _mean([s["attempts"] for s in produced]),
        "mean_latency_s": _mean([s["latency_s"] for s in samples]),
        "dimensions": {d: _mean([s[f"mean_{d}"] for s in produced if f"mean_{d}" in s]) for d in DIMENSIONS},
        "dimension_cases_measured": {d: sum(f"mean_{d}" in s for s in produced) for d in DIMENSIONS},
    }
    judged = {d: _mean([s[f"judge_{d}"] for s in produced if f"judge_{d}" in s]) for d in LLMJudge.DIMENSIONS}
    if any(v is not None for v in judged.values()):
        campaign["judge_dimensions"] = judged
    kinds = sorted({k for c in checks for k in c["expected"] + c["predicted"]})
    per_kind, tp_all, fp_all, fn_all = {}, 0, 0, 0
    for k in kinds:
        tp = sum(k in c["expected"] and k in c["predicted"] for c in checks)
        fp = sum(k not in c["expected"] and k in c["predicted"] for c in checks)
        fn = sum(k in c["expected"] and k not in c["predicted"] for c in checks)
        tp_all, fp_all, fn_all = tp_all + tp, fp_all + fp, fn_all + fn
        per_kind[k] = {"tp": tp, "fp": fp, "fn": fn, "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                       "recall": round(tp / (tp + fn), 4) if tp + fn else None}
    checker = {"cases": len(checks), "exact_match_rate": _mean([float(c["exact_match"]) for c in checks]),
               "micro_precision": round(tp_all / (tp_all + fp_all), 4) if tp_all + fp_all else None,
               "micro_recall": round(tp_all / (tp_all + fn_all), 4) if tp_all + fn_all else None,
               "per_kind": per_kind,
               "misses": [{"id": c["id"], "expected": c["expected"], "predicted": c["predicted"], "note": c["note"]}
                          for c in checks if not c["exact_match"]]}
    return {"campaigns": campaign, "checker": checker}


def compare(a: Path, b: Path) -> None:
    sa, sb = (json.loads((p / "summary.json").read_text(encoding="utf-8")) for p in (a, b))

    def flat(d: dict, prefix: str = "") -> dict:
        out = {}
        for k, v in d.items():
            if isinstance(v, dict) and k not in ("forbidden_leaks", "per_kind"):
                out |= flat(v, f"{prefix}{k}.")
            elif isinstance(v, (int, float)) or v is None:
                out[f"{prefix}{k}"] = v
        return out
    fa, fb = flat(sa), flat(sb)
    print(f"{'metric':45} {a.name[:22]:>22} {b.name[:22]:>22}")
    for k in fa:
        if fa.get(k) != fb.get(k):
            print(f"{k:45} {fa.get(k)!s:>22} {fb.get(k)!s:>22}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label")
    ap.add_argument("--llm", action="store_true", help="generate with the configured LLM_PROVIDER (API calls)")
    ap.add_argument("--judge", action="store_true", help="also score with an LLM judge (API calls)")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between LLM calls (free-tier rate limits)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="setting override")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("RUN_A", "RUN_B"))
    args = ap.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    if not args.label:
        ap.error("--label is required")
    overrides = dict(kv.split("=", 1) for kv in args.set)
    rows, checker_rows = load(DATASET), load(CHECKER)

    with tempfile.TemporaryDirectory() as tmp:
        settings = build_settings(Path(tmp), args.llm or args.judge, overrides)
        ctx = build_app(settings, use_configured_llm=args.llm, enable_rag=False)   # judge-only: templates generate
        judge = None
        if args.judge:
            from src.services.llm_service import create_llm_service
            judge_llm, status = create_llm_service(settings)
            if judge_llm is None:
                raise SystemExit(f"--judge needs a working LLM provider: {status}")
            judge = LLMJudge(judge_llm)
        samples = run_campaigns(ctx, rows, judge, args.pause)
        checks = run_checker(ctx, checker_rows)
        generation = ctx.llm_status if args.llm else "deterministic templates (LLM not used for generation)"

    run_id = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}_{args.label}"
    out = RUNS / run_id
    out.mkdir(parents=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                cwd=PROJECT_ROOT).stdout.strip() or "unknown"
    except OSError:
        commit = "unknown"
    config = {"label": args.label, "git_commit": commit, "dataset_size": len(rows), "checker_cases": len(checker_rows),
              "generation": generation, "judge": judge.name if judge else None,
              "llm_provider": settings.llm_provider, "llm_model": settings.llm_model,
              "max_attempts": settings.marketing_max_attempts, "pass_threshold": settings.marketing_pass_threshold,
              "catalog": "data/courses_official.json", "overrides": overrides}
    summary = summarize(samples, checks)
    (out / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "samples.jsonl").open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with (out / "checker.jsonl").open("w", encoding="utf-8") as f:
        for c in checks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    cols = ["id", "lang", "expect", "outcome", "outcome_ok", "language_ok", "mode", "variations", "drafts_passed",
            "gate_violations", "is_trap", "attempts", "dropped", "llm_fallback", "latency_s",
            *[f"mean_{d}" for d in DIMENSIONS], *[f"judge_{d}" for d in LLMJudge.DIMENSIONS], "purpose"]
    with (out / "scores.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(samples)
    try:   # also log the run in the shared database (evaluation_runs table)
        from src.db.database import Database
        from src.db.repositories import EvaluationRepository
        main_db = Database(load_settings().database_url)
        main_db.migrate()
        EvaluationRepository(main_db).record(run_id=run_id, kind="marketing", config=config, summary=summary,
                                             results_path=str(out.relative_to(PROJECT_ROOT)), dataset_size=len(rows),
                                             mean_score=summary["campaigns"]["draft_pass_rate"])
    except Exception as exc:
        print(f"(not recorded in database: {exc})")
    print(json.dumps({"run": run_id, "config": config, "summary": summary}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
