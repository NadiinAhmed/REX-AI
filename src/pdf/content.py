"""A small, editable document model: title + sections made of a few block types.

Both agents build a `DocumentContent`; the renderer turns it into a PDF and `markdown.py` turns it into
text a person can edit and back. Keeping the model tiny is what makes review-and-edit possible.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BlockKind = Literal["paragraph", "bullets", "numbered", "table", "note"]


class Block(BaseModel):
    kind: BlockKind
    text: str = ""                                  # paragraph / note
    items: list[str] = Field(default_factory=list)  # bullets / numbered
    header: list[str] = Field(default_factory=list)  # table
    rows: list[list[str]] = Field(default_factory=list)


class Section(BaseModel):
    heading: str
    blocks: list[Block] = Field(default_factory=list)


class DocumentContent(BaseModel):
    title: str
    subtitle: str = ""
    language: Literal["en", "ar"] = "en"
    meta: list[tuple[str, str]] = Field(default_factory=list)     # cover-page facts, in order
    sections: list[Section] = Field(default_factory=list)
    disclaimer: str = ""                                          # printed on the last page
    footer: str = "REX AI · Andalusia Academy"


# convenience constructors keep the templates short and readable
def para(text: str) -> Block:
    return Block(kind="paragraph", text=text)


def bullets(items: list[str]) -> Block:
    return Block(kind="bullets", items=[i for i in items if i])


def numbered(items: list[str]) -> Block:
    return Block(kind="numbered", items=[i for i in items if i])


def note(text: str) -> Block:
    return Block(kind="note", text=text)


def table(header: list[str], rows: list[list[str]]) -> Block:
    return Block(kind="table", header=header, rows=[[str(c) for c in r] for r in rows])
