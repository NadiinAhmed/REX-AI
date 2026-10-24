"""Admin: upload, approve, replace and remove PDFs, and test retrieval."""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from src.db.models import ApprovalStatus
from src.ingestion.pdf import DocumentError
from src.ingestion.service import IngestionError
from src.ui_context import get_context, require_admin

st.set_page_config(page_title="REX AI · Knowledge base", page_icon="📚")
st.title("📚 Knowledge base")
ctx = get_context()
if not require_admin(ctx):
    st.stop()
kb = ctx.knowledge
if kb is None:
    st.error(ctx.rag_status)
    st.stop()
st.caption(f"Embeddings: {kb.embedding_model}. Only **approved** documents are used by the assistant.")

with st.form("upload", clear_on_submit=True):
    files = st.file_uploader("Add PDF documents", type=["pdf"], accept_multiple_files=True)
    doc_type = st.selectbox("Document type", ["course_catalog", "faq", "admission", "policy", "general"])
    approve = st.checkbox("I reviewed these documents: approve them now")
    submitted = st.form_submit_button("Upload and index")
if submitted and files:
    for f in files:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(f.name).name
            path.write_bytes(f.getvalue())
            try:
                r = kb.ingestion.ingest(path, filename=f.name, doc_type=doc_type, approve=approve)
                st.success(f"{r.filename}: {r.page_count} pages, {r.chunk_count} chunks ({r.approval_status}).")
                for w in r.warnings:
                    st.warning(f"{r.filename}: {w}")
            except (DocumentError, IngestionError) as exc:
                st.error(str(exc))

st.subheader("Documents")
for d in kb.documents.list():
    with st.expander(f"{d.filename} — {d.approval_status} · {d.index_status} · {d.chunk_count} chunks"):
        st.write(f"ID `{d.id}` · {d.page_count} pages · type {d.doc_type} · uploaded {d.uploaded_at:%Y-%m-%d %H:%M}")
        if d.error:
            st.error(d.error)
        c1, c2, c3 = st.columns(3)
        if c1.button("Approve", key=f"a{d.id}", disabled=d.approval_status == "approved"):
            kb.ingestion.set_approval(d.id, ApprovalStatus.APPROVED); st.rerun()
        if c2.button("Reject", key=f"r{d.id}", disabled=d.approval_status == "rejected"):
            kb.ingestion.set_approval(d.id, ApprovalStatus.REJECTED); st.rerun()
        confirm = c3.checkbox("Confirm delete", key=f"c{d.id}")
        if c3.button("Delete", key=f"d{d.id}", disabled=not confirm):
            kb.ingestion.remove(d.id); st.rerun()
        new = st.file_uploader("Replace with a new version", type=["pdf"], key=f"u{d.id}")
        if new is not None and st.button("Replace", key=f"rp{d.id}"):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / Path(new.name).name
                path.write_bytes(new.getvalue())
                try:
                    kb.ingestion.replace(d.id, path, filename=new.name); st.rerun()
                except (DocumentError, IngestionError) as exc:
                    st.error(str(exc))

st.subheader("Diagnostics")
if st.button("Check SQLite ↔ vector store consistency"):
    problems = kb.ingestion.check_consistency()
    st.success("Consistent.") if not problems else st.error("\n".join(problems))
q = st.text_input("Test retrieval (shows what the assistant would read)")
if q:
    r = kb.passages.retrieve(q)
    st.json(r.diagnostics.model_dump())
    for p in r.passages:
        st.markdown(f"**{p.citation}** · score {p.score}\n\n> {p.text[:500]}")
