"""Copy one PR's files from the Sprint 1 package into the current Git checkout.

Run from inside your clone (works on Windows, macOS and Linux):
    python ../rex-ai-sprint1/docs/sprint1_handoff/copy_files.py pr2_role2_knowledge_base

Only the files listed in docs/sprint1_handoff/<name>.txt are copied. Files that already exist
and differ are NOT overwritten unless you pass --overwrite, so existing work is never lost silently.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pr", help="manifest name, e.g. pr1_role3_foundation")
    parser.add_argument("--overwrite", action="store_true", help="replace existing files that differ")
    args = parser.parse_args()

    manifest = PACKAGE / "docs" / "sprint1_handoff" / f"{args.pr.removesuffix('.txt')}.txt"
    if not manifest.exists():
        print(f"Manifest not found: {manifest}")
        return 1
    target = Path.cwd()
    if target.resolve() == PACKAGE:
        print("Run this from inside your Git clone, not from the package folder.")
        return 1

    conflicts = []
    for rel in manifest.read_text(encoding="utf-8").split():
        src, dst = PACKAGE / rel, target / rel
        if dst.exists() and not filecmp.cmp(src, dst, shallow=False) and not args.overwrite:
            conflicts.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"copied  {rel}")
    for rel in conflicts:
        print(f"SKIPPED {rel} (exists with different content - compare manually or use --overwrite)")
    return 2 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
