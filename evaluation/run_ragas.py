"""Reproducible RAGAS evaluation of the REX AI document RAG pipeline.

    python -m evaluation.run_ragas --label baseline                    # retrieval metrics only (offline)
    python -m evaluation.run_ragas --label kw-gate-off --set RETRIEVAL_KEYWORD_GATE=off
    python -m evaluation.run_ragas --label grok --judge                # + LLM-judged metrics (needs a key)
    python -m evaluation.compare evaluation/runs/<A> evaluation/runs/<B>

What is measured
  Retrieval (no LLM, always): RAGAS IDBasedContextPrecision / IDBasedContextRecall (were the
  right PAGES retrieved?) and NonLLMContextPrecisionWithReference / NonLLMContextRecall (string
  similarity between retrieved chunks and the reference pages' text).
  Abstention (REX custom, not RAGAS): on unanswerable questions, did the system say "not found"?
  Generation (--judge, needs LLM_PROVIDER key): RAGAS Faithfulness, ContextRecall,
  ContextPrecisionWithReference and AnswerRelevancy (the last needs sentence-transformers).

The run uses its own temporary database and vector store, so it never touches app data. The
evaluation is separate from the chatbot request path: nothing here runs when users chat.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)

from src.bootstrap import build_app                     # noqa: E402
from src.config import PROJECT_ROOT, load_settings     # noqa: E402

DATASET = PROJECT_ROOT / "evaluation" / "dataset.jsonl"
RUNS = PROJECT_ROOT / "evaluation" / "runs"
JUDGE_BASE_URLS = {"xai": "https://api.x.ai/v1", "groq": "https://api.groq.com/openai/v1"}


def load_dataset(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_settings(overrides: dict[str, str], workdir: Path, judge: bool):
    env = dict(os.environ)
    env.update({"DATABASE_URL": f"sqlite:///{workdir / 'eval.db'}", "VECTOR_DIR": str(workdir / "vectors"),
                "UPLOAD_DIR": str(workdir / "uploads"),
                "REX_COURSES_PATH": str(PROJECT_ROOT / "data" / "courses_official.json")})
    if not judge:
        env["LLM_PROVIDER"] = "none"      # retrieval-only runs are free and deterministic
    env.update(overrides)
    return load_settings(env)


def generate(settings, rows: list[dict]) -> tuple[list[dict], dict]:
    ctx = build_app(settings)
    agent = ctx.registry.get("rex_assistant")
    docs = ctx.knowledge.documents
    page_text: dict[int, list[str]] = {}
    for d in docs.list():
        for c in docs.chunks(d.id):
            page_text.setdefault(c.page, []).append(c.text)
    samples = []
    for row in rows:
        started = time.perf_counter()
        r = agent.answer_from_documents(row["question"])
        contexts = r.data.get("contexts", [])
        samples.append({
            **row,
            "response": r.text, "response_mode": r.mode.value,
            "retrieved_contexts": [c["text"] for c in contexts],
            "retrieved_context_ids": [f"p{c['page']}" for c in contexts],
            "reference_context_ids": [f"p{p}" for p in row["reference_pages"]],
            "reference_contexts": [t for p in row["reference_pages"] for t in page_text.get(p, [])],
            "abstained": not contexts,
            "top_score": r.data["retrieval"]["top_score"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        })
    meta = {"llm_status": ctx.llm_status, "rag_status": ctx.rag_status,
            "embedding_model": ctx.knowledge.embedding_model}
    return samples, meta


async def score_retrieval(samples: list[dict]) -> None:
    from ragas import SingleTurnSample
    from ragas.metrics import (IDBasedContextPrecision, IDBasedContextRecall,
                               NonLLMContextPrecisionWithReference, NonLLMContextRecall)
    metrics = {"id_context_precision": IDBasedContextPrecision(), "id_context_recall": IDBasedContextRecall(),
               "nonllm_context_precision": NonLLMContextPrecisionWithReference(),
               "nonllm_context_recall": NonLLMContextRecall()}
    for s in samples:
        if not s["answerable"]:
            continue                       # context metrics are undefined without reference pages
        if not s["retrieved_contexts"]:
            for name in metrics:           # nothing retrieved for an answerable question: scored 0
                s[name] = 0.0
            continue
        sample = SingleTurnSample(user_input=s["question"], retrieved_contexts=s["retrieved_contexts"],
                                  reference_contexts=s["reference_contexts"],
                                  retrieved_context_ids=s["retrieved_context_ids"],
                                  reference_context_ids=s["reference_context_ids"])
        for name, metric in metrics.items():
            s[name] = round(float(await metric.single_turn_ascore(sample)), 4)


async def score_generation(samples: list[dict], settings) -> str:
    """LLM-judged RAGAS metrics. Costs API calls: roughly 3-10 judge calls per question."""
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import ContextPrecisionWithReference, ContextRecall, Faithfulness

    provider = settings.llm_provider
    if provider not in JUDGE_BASE_URLS or not settings.api_key_for(provider):
        raise SystemExit("--judge needs LLM_PROVIDER=xai or groq and its API key in .env")
    client = AsyncOpenAI(api_key=settings.api_key_for(provider), base_url=JUDGE_BASE_URLS[provider])
    llm = llm_factory(settings.llm_model, provider="openai", client=client)
    faith, recall, precision = Faithfulness(llm=llm), ContextRecall(llm=llm), ContextPrecisionWithReference(llm=llm)
    relevancy = None
    try:
        from ragas.embeddings import embedding_factory
        from ragas.metrics.collections import AnswerRelevancy
        relevancy = AnswerRelevancy(llm=llm, embeddings=embedding_factory(
            "huggingface", model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"))
    except Exception as exc:
        print(f"answer_relevancy skipped: {exc}")
    for s in samples:
        if not (s["answerable"] and s["retrieved_contexts"]):
            continue
        ctxs = s["retrieved_contexts"]
        s["faithfulness"] = round((await faith.ascore(user_input=s["question"], response=s["response"],
                                                      retrieved_contexts=ctxs)).value, 4)
        s["llm_context_recall"] = round((await recall.ascore(user_input=s["question"], retrieved_contexts=ctxs,
                                                             reference=s["reference"])).value, 4)
        s["llm_context_precision"] = round((await precision.ascore(user_input=s["question"], reference=s["reference"],
                                                                   retrieved_contexts=ctxs)).value, 4)
        if relevancy is not None:
            s["answer_relevancy"] = round((await relevancy.ascore(user_input=s["question"],
                                                                  response=s["response"])).value, 4)
    return f"{provider}/{settings.llm_model}"


METRICS = ["id_context_precision", "id_context_recall", "nonllm_context_precision", "nonllm_context_recall",
           "faithfulness", "answer_relevancy", "llm_context_precision", "llm_context_recall"]


def summarize(samples: list[dict]) -> dict:
    def mean(key, rows):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return {"mean": round(statistics.mean(vals), 4), "n": len(vals)} if vals else None
    answerable = [s for s in samples if s["answerable"]]
    unanswerable = [s for s in samples if not s["answerable"]]
    out = {m: mean(m, samples) for m in METRICS if mean(m, samples)}
    for lang in ("en", "ar"):
        sub = [s for s in answerable if s["lang"] == lang]
        out[f"id_context_recall_{lang}"] = mean("id_context_recall", sub)
    out["rex_answered_rate_on_answerable"] = {
        "mean": round(sum(not s["abstained"] for s in answerable) / len(answerable), 4), "n": len(answerable)}
    out["rex_correct_abstention_rate_on_unanswerable"] = {
        "mean": round(sum(s["abstained"] for s in unanswerable) / len(unanswerable), 4), "n": len(unanswerable)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="setting override")
    ap.add_argument("--judge", action="store_true", help="also run LLM-judged metrics (paid API calls)")
    ap.add_argument("--dataset", type=Path, default=DATASET)
    args = ap.parse_args()
    overrides = dict(kv.split("=", 1) for kv in args.set)
    rows = load_dataset(args.dataset)

    import ragas
    with tempfile.TemporaryDirectory() as tmp:
        settings = build_settings(overrides, Path(tmp), args.judge)
        samples, meta = generate(settings, rows)
        asyncio.run(score_retrieval(samples))
        judge = asyncio.run(score_generation(samples, settings)) if args.judge else None

    run_id = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}_{args.label}"
    out = RUNS / run_id
    out.mkdir(parents=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                cwd=PROJECT_ROOT).stdout.strip()
    except OSError:
        commit = "unknown"
    config = {
        "label": args.label, "git_commit": commit, "ragas_version": ragas.__version__,
        "dataset": str(args.dataset.relative_to(PROJECT_ROOT)), "dataset_size": len(rows),
        "answerable": sum(r["answerable"] for r in rows), "unanswerable": sum(not r["answerable"] for r in rows),
        "embedding_model": meta["embedding_model"], "generation": meta["llm_status"], "judge": judge,
        "chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap,
        "top_k": settings.retrieval_top_k, "min_score": settings.retrieval_min_score,
        "keyword_gate": settings.keyword_gate_enabled, "overrides": overrides,
    }
    summary = summarize(samples)
    (out / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "samples.jsonl").open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with (out / "scores.csv").open("w", newline="", encoding="utf-8") as f:
        cols = ["id", "lang", "answerable", "abstained", "top_score", *[m for m in METRICS if any(m in s for s in samples)],
                "retrieved_context_ids", "reference_context_ids", "question"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in samples:
            w.writerow({**s, "retrieved_context_ids": " ".join(s["retrieved_context_ids"]),
                        "reference_context_ids": " ".join(s["reference_context_ids"])})
    try:   # also log the run in the shared database (evaluation_runs table)
        from src.db.database import Database
        from src.db.repositories import EvaluationRepository
        main_db = Database(load_settings().database_url); main_db.migrate()
        recall = summary.get("id_context_recall", {})
        EvaluationRepository(main_db).record(run_id=run_id, kind="ragas", config=config, summary=summary,
                                             results_path=str(out.relative_to(PROJECT_ROOT)),
                                             dataset_size=len(rows), mean_score=recall.get("mean"))
    except Exception as exc:
        print(f"(not recorded in database: {exc})")
    print(json.dumps({"run": run_id, "config": config, "summary": summary}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
