"""Command-line admin tools. Run `python -m src.cli --help`.

  seed-courses [--replace]        load REX_COURSES_PATH into the database
  ingest FILE... [--approve]      add PDFs to the knowledge base
  list-docs                       show documents, approval and index status
  approve ID | reject ID          change a document's approval status
  replace ID FILE                 replace a document with a new version
  remove ID --yes                 delete a document, its chunks and vectors
  reindex [ID]                    re-embed one or all documents
  check                           verify SQLite chunks and vector store agree
  ask "QUESTION"                  query the passage retriever and print diagnostics
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.bootstrap import build_app
from src.config import load_settings
from src.db.models import ApprovalStatus
from src.ingestion.pdf import DocumentError
from src.knowledge_base.loader import load_catalog
from src.utils.logging_config import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description="REX AI admin tools")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seed-courses"); p.add_argument("--replace", action="store_true")
    p = sub.add_parser("ingest"); p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--approve", action="store_true"); p.add_argument("--type", default="general")
    sub.add_parser("list-docs")
    for name in ("approve", "reject"):
        sub.add_parser(name).add_argument("id")
    p = sub.add_parser("replace"); p.add_argument("id"); p.add_argument("file", type=Path)
    p = sub.add_parser("remove"); p.add_argument("id"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("reindex"); p.add_argument("id", nargs="?")
    sub.add_parser("check")
    p = sub.add_parser("ask"); p.add_argument("question")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging("WARNING")
    ctx = build_app(settings, use_configured_llm=False)
    kb = ctx.knowledge
    try:
        if args.cmd == "seed-courses":
            if ctx.courses.count() and not args.replace:
                print("Courses already loaded. Use --replace to overwrite them."); return 1
            n = ctx.courses.replace_catalog(load_catalog(settings.courses_path), str(settings.courses_path))
            print(f"Loaded {n} courses from {settings.courses_path.name}")
        elif kb is None:
            print(f"Knowledge base unavailable: {ctx.rag_status}"); return 2
        elif args.cmd == "ingest":
            code = 0
            for f in args.files:
                try:
                    r = kb.ingestion.ingest(f, doc_type=args.type, approve=args.approve)
                    print(f"OK   {r.filename}: {r.page_count} pages, {r.chunk_count} chunks, "
                          f"{r.approval_status} (id {r.document_id})")
                    for w in r.warnings:
                        print(f"     warning: {w}")
                except DocumentError as exc:
                    print(f"FAIL {f.name}: {exc}"); code = 1
            return code
        elif args.cmd == "list-docs":
            for d in kb.documents.list():
                print(f"{d.id}  {d.approval_status:9} {d.index_status:8} {d.chunk_count:4} chunks  {d.filename}")
        elif args.cmd in ("approve", "reject"):
            kb.ingestion.set_approval(args.id, ApprovalStatus.APPROVED if args.cmd == "approve" else ApprovalStatus.REJECTED)
            print(f"{args.id}: {args.cmd}d")
        elif args.cmd == "replace":
            r = kb.ingestion.replace(args.id, args.file)
            print(f"Replaced with {r.filename} (new id {r.document_id}, {r.chunk_count} chunks)")
        elif args.cmd == "remove":
            if not args.yes:
                print("Removing deletes the document, its chunks and vectors. Re-run with --yes to confirm."); return 1
            kb.ingestion.remove(args.id); print(f"Removed {args.id}")
        elif args.cmd == "reindex":
            ids = [args.id] if args.id else [d.id for d in kb.documents.list()]
            for i in ids:
                print(f"{i}: {kb.ingestion.reindex(i)} chunks re-embedded")
        elif args.cmd == "check":
            problems = kb.ingestion.check_consistency()
            print("Consistent: SQLite chunks and vectors match." if not problems else "\n".join(problems))
            return 1 if problems else 0
        elif args.cmd == "ask":
            r = kb.passages.retrieve(args.question)
            print(r.diagnostics.model_dump_json(indent=1))
            for p in r.passages:
                print(f"\n[{p.citation}  score={p.score}]\n{p.text[:400]}")
    except DocumentError as exc:
        print(f"Error: {exc}"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
