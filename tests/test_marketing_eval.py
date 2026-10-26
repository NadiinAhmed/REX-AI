"""The marketing evaluation pipeline runs end to end and reports what it measures (no API calls)."""
from src.bootstrap import build_app
from src.config import load_settings
from evaluation.marketing.run_marketing_eval import CHECKER, DATASET, load, run_campaigns, run_checker, summarize
from tests.conftest import OFFICIAL_DATASET


def test_evaluation_pipeline_runs_offline(test_env):
    ctx = build_app(load_settings(test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET))), enable_rag=False)
    rows = [r for r in load(DATASET) if r["id"] in ("en-05", "en-09", "ar-03", "en-04")]
    samples = run_campaigns(ctx, rows, judge=None, pause=0)
    checks = run_checker(ctx, [c for c in load(CHECKER) if c["id"] in ("c01", "c24", "c26")])
    summary = summarize(samples, checks)
    assert summary["campaigns"]["cases"] == 4 and summary["campaigns"]["outcome_accuracy"] == 1.0
    assert summary["campaigns"]["forbidden_leaks"] == {} and summary["campaigns"]["gate_violations_shown"] == 0
    assert {c["id"]: c["exact_match"] for c in checks} == {"c01": True, "c24": True, "c26": False}  # c26 = known miss
    assert ctx.offers.active_offers("TRK-002") == []          # evaluation offers are rejected after each case
