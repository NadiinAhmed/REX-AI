"""Reproducible evaluation of Agent 3's accreditation-alignment checker.

    python -m evaluation.curriculum.run_alignment_eval --label my-change
    python -m evaluation.curriculum.run_alignment_eval --compare <run A dir> <run B dir>

What is measured: for each hand-labelled (case, framework area) pair in dataset.jsonl, does the checker
give the same status (covered / partial / missing) a human reviewer gave?

Why this is separate from RAGAS: the alignment checker is not RAG. It reads a curriculum and a
checklist; no retrieval and no LLM are involved, so the run is free, offline and deterministic.

Metrics (simple words)
  exact_accuracy     share of labelled areas where checker and human agree exactly
  over_claim_rate    share where the checker says MORE coverage than the human (e.g. covered vs partial).
                     This is the dangerous direction: it hides gaps from the reviewer.
  under_claim_rate   share where the checker says LESS coverage (it raises a gap that a human would not).
  precision / recall per status, and the same numbers split by language and by case kind.

Labels: written by a person reading what each text teaches, NOT derived from the keyword lists. Rule:
covered = 2+ distinct concepts of the area are taught, partial = exactly 1, missing = none.
Some cases (synonyms, Arabic) are included because the checker is EXPECTED to miss them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT
from src.curriculum import alignment
from src.curriculum.alignment import check_framework
from src.curriculum.frameworks import FRAMEWORKS_DIR, FrameworkLibrary
from src.curriculum.models import Curriculum, CurriculumModule, TracedText

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
RUNS = HERE / "runs"
RANK = {"missing": 0, "partial": 1, "covered": 2}


def load(path: Path = DATASET) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def curriculum_for(case: dict) -> Curriculum:
    modules = [CurriculumModule(number=i, title=m["title"], hours=1,
                                topics=[TracedText(text=t) for t in m["topics"]])
               for i, m in enumerate(case["modules"], 1)]
    return Curriculum(title=f"Evaluation case {case['id']}", modules=modules)


def evaluate(cases: list[dict], library: FrameworkLibrary) -> list[dict]:
    rows = []
    for case in cases:
        report = check_framework(curriculum_for(case), library.get(case["framework"]))
        by_area = {a.area_id: a for a in report.areas}
        for area_id, expected in case["expected"].items():
            got = by_area[area_id]
            rows.append({"case": case["id"], "lang": case["lang"], "kind": case["kind"],
                         "framework": case["framework"], "area": area_id, "area_title": got.title,
                         "expected": expected, "predicted": got.status.value,
                         "matched_terms": "|".join(got.matched_terms),
                         "correct": expected == got.status.value,
                         "direction": ("over" if RANK[got.status.value] > RANK[expected]
                                       else "under" if RANK[got.status.value] < RANK[expected] else "exact")})
    return rows


def _rates(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    d = Counter(r["direction"] for r in rows)
    return {"n": n, "exact_accuracy": round(d["exact"] / n, 4),
            "over_claim_rate": round(d["over"] / n, 4), "under_claim_rate": round(d["under"] / n, 4)}


def summarise(rows: list[dict]) -> dict:
    per_status = {}
    for s in RANK:
        tp = sum(r["expected"] == s and r["predicted"] == s for r in rows)
        pred = sum(r["predicted"] == s for r in rows)
        true = sum(r["expected"] == s for r in rows)
        per_status[s] = {"precision": round(tp / pred, 4) if pred else None,
                         "recall": round(tp / true, 4) if true else None, "support": true}
    confusion = {e: {p: sum(r["expected"] == e and r["predicted"] == p for r in rows) for p in RANK} for e in RANK}
    groups: dict[str, dict] = {}
    for key in ("lang", "kind"):
        buckets = defaultdict(list)
        for r in rows:
            buckets[r[key]].append(r)
        groups[key] = {k: _rates(v) for k, v in sorted(buckets.items())}
    return {"overall": _rates(rows), "cases": len({r["case"] for r in rows}), "per_status": per_status,
            "confusion_expected_x_predicted": confusion, "by": groups,
            "errors": [f"{r['case']} area {r['area']} ({r['area_title']}): expected {r['expected']}, "
                       f"got {r['predicted']} [{r['matched_terms'] or 'no terms'}]" for r in rows if not r["correct"]]}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def run(label: str) -> Path:
    cases = load()
    library = FrameworkLibrary.load()
    rows = evaluate(cases, library)
    summary = summarise(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RUNS / f"{stamp}_{label}"
    out.mkdir(parents=True, exist_ok=True)
    framework_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in sorted(FRAMEWORKS_DIR.glob("*.json"))}
    config = {"label": label, "created_at": stamp, "git_commit": _git("rev-parse", "--short", "HEAD"),
              "git_dirty": bool(_git("status", "--porcelain")), "dataset": str(DATASET.relative_to(PROJECT_ROOT)),
              "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest()[:12],
              "covered_min_hits": alignment.COVERED_MIN_HITS, "framework_files": framework_hashes,
              "llm": "none (the checker is deterministic)"}
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "scores.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"run": str(out.relative_to(PROJECT_ROOT)), **summary["overall"],
                      "errors": len(summary["errors"])}, indent=2))
    return out


def compare(a: Path, b: Path) -> None:
    sa, sb = (json.loads((p / "summary.json").read_text(encoding="utf-8")) for p in (a, b))
    print(f"{'metric':<20}{a.name[:28]:>30}{b.name[:28]:>30}{'change':>10}")
    for key in ("exact_accuracy", "over_claim_rate", "under_claim_rate"):
        va, vb = sa["overall"][key], sb["overall"][key]
        print(f"{key:<20}{va:>30.3f}{vb:>30.3f}{vb - va:>+10.3f}")
    for group in ("lang", "kind"):
        for k in sorted(set(sa["by"][group]) | set(sb["by"][group])):
            va = sa["by"][group].get(k, {}).get("exact_accuracy")
            vb = sb["by"][group].get(k, {}).get("exact_accuracy")
            if va is not None and vb is not None:
                print(f"{group + ':' + k:<20}{va:>30.3f}{vb:>30.3f}{vb - va:>+10.3f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--label", default="run")
    p.add_argument("--compare", nargs=2, type=Path, metavar=("RUN_A", "RUN_B"))
    args = p.parse_args()
    if args.compare:
        compare(*args.compare)
    else:
        run(args.label)


if __name__ == "__main__":
    main()
