"""Agent 4 page: generate course documents, review and edit them, approve, download or e-mail the PDF.

Documents are built from the APPROVED curriculum when one exists (see the Curriculum page), otherwise from
the course record. Every document is a draft until a named reviewer approves it.
"""
from __future__ import annotations

import streamlit as st

from src.agents.base import AgentRequest
from src.documents.context import DocumentError
from src.documents.models import DocStatus, DocType, DocumentRequest
from src.documents.service import DocumentService
from src.pdf.markdown import to_markdown
from src.pdf.renderer import PdfRenderError
from src.services.email_service import EmailError
from src.ui_context import get_context, require_admin

AGENT = "document_generation"
st.set_page_config(page_title="REX AI · Documents", page_icon="📄", layout="wide")
st.title("📄 Document generation")
st.caption("Prepares course documents from approved data, in English or Arabic. Fees, schedules and instructors appear only when the "
           "academy has published them. Everything is a draft until a reviewer approves it.")

ctx = get_context()
service: DocumentService | None = ctx.document_service
if service is None:
    st.error("The document generation agent is not available. See the warnings on the main page.")
    st.stop()
if not require_admin(ctx):
    st.stop()

curricula = ctx.curriculum_service
create_tab, library_tab, chat_tab = st.tabs(["➕ Generate", "📋 Library & review", "💬 Ask the agent"])

# ------------------------------------------------------------------ generate
with create_tab:
    doc_type = st.selectbox("Document", list(DocType), format_func=lambda d: d.label)
    source = st.radio("Build from", ["Catalog course", "Stored curriculum"], horizontal=True)
    approved = ctx.courses.list_approved() if ctx.courses else []
    course_id = curriculum_id = None
    if source == "Catalog course":
        course_id = st.selectbox("Course", [c.course_id for c in approved],
                                 format_func=lambda i: f"{i} · {next(c.title for c in approved if c.course_id == i)}")
        has_approved = course_id and curricula.approved_for_course(course_id)
        st.caption("✅ An approved curriculum exists: the document will be built from it." if has_approved else
                   "ℹ️ No approved curriculum for this course: the document will use the course record and be marked accordingly.")
    else:
        rows = curricula.list()
        if rows:
            curriculum_id = st.selectbox("Curriculum", [r.id for r in rows],
                                         format_func=lambda i: next(f"{r.id} · {r.curriculum.title} · {r.status.label}" for r in rows if r.id == i))
        else:
            st.info("No curricula yet: create one on the Curriculum page.")
    c1, c2 = st.columns(2)
    language = c1.radio("Language", ["en", "ar"], horizontal=True, format_func=lambda x: "English" if x == "en" else "العربية")
    audience = c2.text_input("Intended audience (optional)", help="Shown on the cover and in the text. Default: the course's target audience.")
    strict = st.checkbox("Only use an approved curriculum", value=False)
    use_llm = st.checkbox("Let the LLM translate to Arabic / write multiple-choice questions", value=True,
                          disabled=not ctx.settings or not ctx.settings.llm_enabled,
                          help="Needs LLM_PROVIDER and a key in .env. Without it, templates and the course data are used as stored.")
    actor = st.text_input("Your name (recorded in the log)", value="admin")
    if st.button("Generate document", type="primary", disabled=not (course_id or curriculum_id)):
        try:
            with st.spinner("Building the document…"):
                doc = service.generate(DocumentRequest(doc_type=doc_type, course_id=course_id, curriculum_id=curriculum_id, language=language,
                                                       audience=audience.strip() or None, require_approved_curriculum=strict),
                                       actor=actor or "admin", use_llm=use_llm)
            st.session_state.selected_document = doc.id
            st.success(f"Created {doc.id}: {doc.title}. Open it in the Library tab to review.")
            for n in doc.notices:
                st.info(n)
        except (DocumentError, ValueError) as exc:
            st.error(str(exc))

# ------------------------------------------------------------------ library & review
with library_tab:
    f = st.columns(4)
    kind = f[0].selectbox("Type", ["All"] + [d.value for d in DocType], format_func=lambda v: v if v == "All" else DocType(v).label)
    status = f[1].selectbox("Status", ["All", "draft", "approved"])
    lang = f[2].selectbox("Language", ["All", "en", "ar"])
    course_filter = f[3].selectbox("Course", ["All"] + sorted({d.course_id for d in service.list() if d.course_id}))
    rows = service.list(**{k: v for k, v in dict(doc_type=kind, status=status, language=lang, course_id=course_filter).items() if v != "All"})
    st.caption(f"{len(rows)} document(s)")
    st.dataframe([{"id": d.id, "type": d.doc_type.label, "course": d.course_id, "language": d.language, "status": d.status.value,
                   "revision": d.revision, "built from": d.basis} for d in rows], use_container_width=True, hide_index=True)
    if rows:
        ids = [d.id for d in rows]
        default = ids.index(st.session_state.selected_document) if st.session_state.get("selected_document") in ids else 0
        sel = st.selectbox("Open document", ids, index=default, format_func=lambda i: f"{i} · {next(d.title for d in rows if d.id == i)}")
        doc = service.get(sel)
        st.subheader(f"{doc.title} — {doc.status.value} (revision {doc.revision})")
        if doc.status == DocStatus.APPROVED:
            st.success(f"Approved by {doc.approved_by}.")
        else:
            st.warning("Draft: not approved. The PDF carries a DRAFT banner until you approve it.")
        st.caption(f"Built from: {doc.basis} · template v{doc.template_version} · mode: {doc.generation_mode}")
        with st.expander("Notes for the reviewer", expanded=bool(doc.notices)):
            for n in doc.notices:
                st.markdown(f"- {n}")

        # download
        d1, d2 = st.columns(2)
        try:
            d1.download_button("⬇️ Download PDF", service.pdf(doc.id), file_name=f"{doc.id}.pdf", mime="application/pdf")
        except PdfRenderError as exc:
            d1.error(str(exc))
        d2.download_button("⬇️ Download Markdown", to_markdown(doc.content), file_name=f"{doc.id}.md", mime="text/markdown")

        # edit
        with st.expander("✏️ Edit the text (saving creates a new revision that must be approved again)"):
            st.caption("Syntax: `## Heading`, `- bullet`, `1. numbered`, `> note`, and `| a | b |` tables. Cover details and the disclaimer are kept.")
            edited = st.text_area("Document text", value=to_markdown(doc.content), height=420, key=f"md_{doc.id}_{doc.revision}")
            who = st.text_input("Your name", key=f"who_{doc.id}", value="admin")
            if st.button("Save changes"):
                try:
                    service.edit_markdown(doc.id, edited, who or "admin"); st.rerun()
                except DocumentError as exc:
                    st.error(str(exc))
        with st.expander("👁️ Preview"):
            direction = "rtl" if doc.language == "ar" else "ltr"
            st.markdown(f'<div dir="{direction}">\n\n{to_markdown(doc.content)}\n\n</div>', unsafe_allow_html=True)

        # approve
        st.markdown("**Approval**")
        reviewer = st.text_input("Reviewer name", key=f"rev_{doc.id}")
        if st.button("✅ Approve", disabled=doc.status == DocStatus.APPROVED):
            try:
                service.approve(doc.id, reviewer); st.rerun()
            except DocumentError as exc:
                st.error(str(exc))

        # e-mail
        with st.expander("✉️ E-mail the approved PDF"):
            if not service.email_enabled:
                st.info("E-mail is not configured. Set SMTP_HOST and SMTP_FROM in .env to enable it.")
            elif doc.status != DocStatus.APPROVED:
                st.info("Only approved documents can be e-mailed.")
            else:
                to = st.text_input("Recipients (comma separated, max 10)", key=f"to_{doc.id}")
                msg = st.text_area("Message (optional)", key=f"msg_{doc.id}", height=70)
                if st.button("Send"):
                    try:
                        st.success(f"Sent to {service.email(doc.id, to, reviewer or 'admin', msg)} recipient(s).")
                    except (DocumentError, EmailError) as exc:
                        st.error(str(exc))
        with st.expander("Log"):
            st.dataframe(doc.log, use_container_width=True, hide_index=True)

with chat_tab:
    st.caption("Try: types · generate a participant guide for TRK-001 · list · DOC-XXXXXX · اعمل دليل المدرب لـ TRK-002 بالعربية")
    if prompt := st.chat_input("e.g. generate a question bank for TRK-001"):
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            st.markdown(ctx.registry.dispatch(AGENT, AgentRequest.from_text(prompt)).text)
