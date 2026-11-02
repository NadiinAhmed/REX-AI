"""Course-recommendation evaluation for REX Assistant (separate from the PDF/RAGAS evaluation).

    python -m evaluation.courses.run_course_eval --retriever keyword --label keyword-baseline
    python -m evaluation.courses.run_course_eval --retriever semantic --label e5-small --scan
    python -m evaluation.courses.run_course_eval --retriever semantic --label e5-0.84 --min-score 0.84

It runs every query in queries.jsonl through the REAL recommendation path (extract_profile ->
recommend_courses -> retriever) and measures:

  hit@1        the expected course is the FIRST recommendation
  hit@3        an expected course is among the first three (REX shows up to three)
  abstention   on off-topic requests (cooking, nursing, ...), REX recommends NOTHING
  balanced     (hit@3 + abstention) / 2: one number that punishes both missing the right course
               and recommending a course for a topic the academy does not teach

--scan (semantic only) repeats the run for a range of COURSE_SEMANTIC_MIN_SCORE values, so the
threshold is chosen from measurements instead of guessed. No LLM and no API key are used; the
semantic run needs `pip install sentence-transformers` and a one-time model download.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.embeddings import Embeddings

from src.config import PROJECT_ROOT, load_settings
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from src.workflows.learner_profile import extract_profile
from src.workflows.recommendation import recommend_courses

HERE = Path(__file__).parent
QUERIES = HERE / "queries.jsonl"
RUNS = HERE / "runs"
CATALOG = PROJECT_ROOT / "data" / "courses_official.json"
SCAN = [round(0.70 + i * 0.01, 2) for i in range(26)]      # 0.70 ... 0.95


class CachedEmbeddings(Embeddings):
    """Remembers query vectors so a threshold scan embeds each query once."""

    def __init__(self, inner: Embeddings) -> None:
        self._inner, self._cache = inner, {}
        self.model_id = getattr(inner, "model_id", type(inner).__name__)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = self._inner.embed_query(text)
        return self._cache[text]


def load_queries(path: Path = QUERIES) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(rows: list[dict], retriever) -> list[dict]:
    samples = []
    for row in rows:
        profile = extract_profile(row["query"])
        recs = recommend_courses(profile, retriever, top_k=3).recommendations
        got = [r.course.course_id for r in recs]
        sample = {**row, "recommended": got, "scores": [r.score for r in recs],
                  # raw similarity to EVERY course (semantic only): shows why a course was or was not kept
                  "similarities": retriever.similarities(profile.semantic_query())
                  if hasattr(retriever, "similarities") and profile.has_topic() else None,
                  "reasons": [r.reasons[0].code for r in recs], "topic_detected": profile.has_topic()}
        if row["expected"]:
            sample["hit@1"] = float(bool(got) and got[0] in row["expected"])
            sample["hit@3"] = float(any(c in row["expected"] for c in got[:3]))
        else:
            sample["abstained"] = float(not got)
        samples.append(sample)
    return samples


def summarize(samples: list[dict]) -> dict:
    def mean(key, rows):
        values = [s[key] for s in rows if key in s]
        return {"mean": round(sum(values) / len(values), 4), "n": len(values)} if values else None

    answerable = [s for s in samples if s["expected"]]
    out = {"hit@1": mean("hit@1", answerable), "hit@3": mean("hit@3", answerable),
           "abstention_off_topic": mean("abstained", samples)}
    for group in ("paraphrase", "direct"):
        out[f"hit@3_{group}"] = mean("hit@3", [s for s in answerable if s["type"] == group])
    for lang in ("en", "ar"):
        out[f"hit@3_{lang}"] = mean("hit@3", [s for s in answerable if s["lang"] == lang])
    hit3, abst = out["hit@3"], out["abstention_off_topic"]
    out["balanced"] = round((hit3["mean"] + abst["mean"]) / 2, 4) if hit3 and abst else None
    return out


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except Exception:
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retriever", choices=("keyword", "semantic"), required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--min-score", type=float, help="COURSE_SEMANTIC_MIN_SCORE for a single semantic run")
    ap.add_argument("--scan", action="store_true", help="semantic: try min-score 0.70..0.95")
    args = ap.parse_args()

    courses = load_catalog(CATALOG).courses
    rows = load_queries()
    config: dict = {"label": args.label, "git_commit": git_commit(), "retriever": args.retriever,
                    "catalog": str(CATALOG.relative_to(PROJECT_ROOT)), "queries": len(rows)}
    scan_rows: list[dict] = []
    if args.retriever == "keyword":
        samples = run(rows, KeywordRetriever(courses))
    else:
        from src.knowledge_base.semantic_retriever import SemanticCourseRetriever
        from src.rag.embeddings import EmbeddingError, create_embeddings
        settings = load_settings({**os.environ, "EMBEDDING_PROVIDER": "sentence_transformers"})
        try:
            embeddings = CachedEmbeddings(create_embeddings(settings))
        except EmbeddingError as exc:
            raise SystemExit(f"Semantic run needs the embedding model: {exc}") from exc
        min_score = args.min_score if args.min_score is not None else settings.course_semantic_min_score
        retriever = SemanticCourseRetriever(courses, embeddings, min_score)
        config.update(embedding_model=embeddings.model_id, min_score=min_score)
        if args.scan:
            for t in SCAN:
                retriever.min_score = t
                scan_rows.append({"min_score": t, **{k: (v["mean"] if isinstance(v, dict) else v)
                                                     for k, v in summarize(run(rows, retriever)).items()}})
            best = max(scan_rows, key=lambda r: (r["balanced"] or 0, r["hit@3"] or 0))
            retriever.min_score = best["min_score"]
            config.update(min_score=best["min_score"], chosen_by="max balanced, then hit@3")
        samples = run(rows, retriever)

    summary = summarize(samples)
    out = RUNS / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}_{args.label}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (out / "samples.jsonl").open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    if scan_rows:
        with (out / "scan.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(scan_rows[0]))
            writer.writeheader()
            writer.writerows(scan_rows)
    print(json.dumps({"config": config, "summary": summary}, indent=1, ensure_ascii=False))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
