"""Compare two evaluation runs:  python -m evaluation.compare evaluation/runs/<A> evaluation/runs/<B>"""
import json
import sys
from pathlib import Path


def main(a: str, b: str) -> None:
    sa, sb = (json.loads((Path(p) / "summary.json").read_text(encoding="utf-8")) for p in (a, b))
    ca, cb = (json.loads((Path(p) / "config.json").read_text(encoding="utf-8")) for p in (a, b))
    print(f"A = {ca['label']} ({ca['git_commit']})   B = {cb['label']} ({cb['git_commit']})")
    changed = {k: (ca.get(k), cb.get(k)) for k in ca if k not in ("label", "git_commit") and ca.get(k) != cb.get(k)}
    print("config differences:", changed or "none")
    print(f"{'metric':45} {'A':>8} {'B':>8} {'B-A':>8}")
    for key in sorted(set(sa) | set(sb)):
        va = (sa.get(key) or {}).get("mean")
        vb = (sb.get(key) or {}).get("mean")
        delta = f"{vb - va:+.4f}" if va is not None and vb is not None else ""
        print(f"{key:45} {va if va is not None else '-':>8} {vb if vb is not None else '-':>8} {delta:>8}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
