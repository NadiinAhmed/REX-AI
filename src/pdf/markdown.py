"""DocumentContent <-> simple Markdown, so a person can review and edit generated material in a text box.

Supported syntax (anything else is kept as paragraph text, nothing is lost):
    # Title          ## Section heading        plain text = paragraph
    - item           1. item                   > note
    | a | b |        header row, then |---|---|, then data rows
Cover-page facts (`meta`) and the disclaimer are not edited here: they are kept from the original.
"""
from __future__ import annotations

import re

from src.pdf.content import DocumentContent, Section, bullets, note, numbered, para, table

_NUM = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ").strip()


def to_markdown(doc: DocumentContent) -> str:
    out = [f"# {doc.title}"]
    if doc.subtitle:
        out.append(f"_{doc.subtitle}_")
    for section in doc.sections:
        out += ["", f"## {section.heading}", ""]
        for block in section.blocks:
            if block.kind == "paragraph":
                out += [block.text, ""]
            elif block.kind == "note":
                out += [f"> {block.text}", ""]
            elif block.kind == "bullets":
                out += [f"- {i}" for i in block.items] + [""]
            elif block.kind == "numbered":
                out += [f"{n}. {i}" for n, i in enumerate(block.items, 1)] + [""]
            elif block.kind == "table":
                out.append("| " + " | ".join(_cell(h) for h in block.header) + " |")
                out.append("|" + "|".join("---" for _ in block.header) + "|")
                out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in block.rows] + [""]
    return "\n".join(out).rstrip() + "\n"


def from_markdown(text: str, original: DocumentContent) -> DocumentContent:
    """Parse edited Markdown. Title/subtitle/sections come from the text; meta + disclaimer from `original`."""
    title, subtitle = original.title, original.subtitle
    sections: list[Section] = []
    current: Section | None = None
    para_buf: list[str] = []
    list_kind: str | None = None
    list_items: list[str] = []
    table_rows: list[list[str]] = []

    def flush_para() -> None:
        if para_buf and current is not None:
            current.blocks.append(para(" ".join(para_buf)))
        para_buf.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_items and current is not None:
            current.blocks.append(bullets(list_items[:]) if list_kind == "b" else numbered(list_items[:]))
        list_items.clear()
        list_kind = None

    def flush_table() -> None:
        if table_rows and current is not None:
            header, *rows = table_rows
            width = len(header)
            current.blocks.append(table(header, [(r + [""] * width)[:width] for r in rows]))
        table_rows.clear()

    def flush_all() -> None:
        flush_para(); flush_list(); flush_table()

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            flush_all()
            current = Section(heading=stripped[3:].strip())
            sections.append(current)
        elif stripped.startswith("# ") and not stripped.startswith("## "):
            flush_all()
            title = stripped[2:].strip() or title
        elif not stripped:
            flush_all()
        elif current is None:
            if re.fullmatch(r"_.*_", stripped):          # subtitle line under the title
                subtitle = stripped.strip("_").strip()
            continue
        elif stripped.startswith("|"):
            flush_para(); flush_list()
            if _SEP.match(stripped):
                continue
            table_rows.append([c.strip() for c in stripped.strip("|").split("|")])
        elif stripped.startswith(">"):
            flush_all()
            current.blocks.append(note(stripped.lstrip("> ").strip()))
        elif stripped.startswith(("- ", "* ")):
            flush_para(); flush_table()
            if list_kind not in (None, "b"):
                flush_list()
            list_kind = "b"
            list_items.append(stripped[2:].strip())
        elif m := _NUM.match(stripped):
            flush_para(); flush_table()
            if list_kind not in (None, "n"):
                flush_list()
            list_kind = "n"
            list_items.append(m.group(1).strip())
        else:
            flush_list(); flush_table()
            para_buf.append(stripped)
    flush_all()
    return original.model_copy(update={"title": title, "subtitle": subtitle, "sections": sections})
