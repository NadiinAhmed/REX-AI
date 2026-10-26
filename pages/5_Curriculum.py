"""Agent 3 page: design a curriculum, check it against competency frameworks, review it, export the PDF.

Flow: Create -> (edit) -> Submit for review -> Approve / Request changes -> approved curricula feed the
Document Generation agent. Nothing is approved without a named reviewer.
"""
from __future__ import annotations

import streamlit as st

from src.agents.base import AgentRequest
from src.curriculum.export import curriculum_pdf
from src.curriculum.generator import CurriculumError
from src.curriculum.models import AreaStatus, CurriculumRequest, CurriculumStatus, Origin
from src.curriculum.service import CurriculumService
from src.pdf.renderer import PdfRenderError
from src.ui_context import get_context, require_admin

AGENT = "curriculum"
ICON = {AreaStatus.COVERED: "✅", AreaStatus.PARTIAL: "🟡", AreaStatus.MISSING: "❌"}
st.set_page_config(page_title="REX AI · Curriculum", page_icon="📚", layout="wide")
st.title("📚 Curriculum research & accreditation alignment")
st.caption("Designs a curriculum from the approved course catalog and documents, compares it with competency frameworks "
           "and prepares a PDF for review. It proposes alignment: it **never** states that a course is accredited.")

ctx = get_context()
service: CurriculumService | None = ctx.curriculum_service
if service is None:
    st.error("The curriculum agent is not available. See the warnings on the main page.")
    st.stop()
if not require_admin(ctx):
    st.stop()

lines = lambda text: [l.strip() for l in (text or "").splitlines() if l.strip()]   # noqa: E731
create_tab, library_tab, chat_tab = st.tabs(["➕ Create", "📋 Library & review", "💬 Ask the agent"])

# ------------------------------------------------------------------ create
with create_tab:
    approved = ctx.courses.list_approved() if ctx.courses else []
    mode = st.radio("Base", ["Catalog course", "New course"], horizontal=True)
    request_kwargs: dict = {}
    if mode == "Catalog course":
        f1, f2 = st.columns(2)
        dept = f1.selectbox("Department filter", ["All"] + sorted({c.department for c in approved if c.department}))
        level = f2.selectbox("Skill level filter", ["All", "beginner", "intermediate", "advanced", "unknown"])
        pool = [c for c in approved if (dept == "All" or c.department == dept) and (level == "All" or c.skill_level.value == level)]
        if not pool:
            st.info("No catalog course matches these filters.")
        chosen = st.selectbox("Course", [c.course_id for c in pool], format_func=lambda i: f"{i} · {next(c.title for c in pool if c.course_id == i)}") if pool else None
        request_kwargs["course_id"] = chosen
    else:
        request_kwargs["title"] = st.text_input("Course title")
        request_kwargs["topics"] = lines(st.text_area("Topics (one per line)", help="Each topic becomes a module. Leave empty to outline from a framework."))
    c1, c2, c3 = st.columns(3)
    hours = c1.number_input("Training hours (0 = use the catalog value)", min_value=0.0, max_value=2000.0, value=0.0, step=5.0)
    skill = c2.selectbox("Skill level", ["(from catalog)", "beginner", "intermediate", "advanced"])
    audience = c3.text_input("Target audience (comma separated)")
    frameworks = st.multiselect("Compare with frameworks (empty = suggested for the department)",
                                [f.framework_id for f in service.frameworks.all()],
                                format_func=lambda i: service.frameworks.get(i).name)
    with st.expander("More inputs: outcomes, projects, prerequisites, extra topics"):
        outcomes = lines(st.text_area("Expected learning outcomes (one per line)"))
        projects = lines(st.text_area("Practical projects (one per line)"))
        prereqs = lines(st.text_area("Prerequisites (one per line)"))
        if mode == "Catalog course":
            request_kwargs["topics"] = lines(st.text_area("Extra topics to include (one per line)"))
    use_llm = st.checkbox("Let the LLM improve objectives, outcomes and exercises", value=True,
                          disabled=not ctx.settings or not ctx.settings.llm_enabled,
                          help="Needs LLM_PROVIDER and a key in .env. Without it the deterministic draft is used.")
    actor = st.text_input("Your name (recorded in the review log)", value="admin")
    if st.button("Generate curriculum", type="primary"):
        try:
            req = CurriculumRequest(
                **request_kwargs, duration_hours=hours or None, skill_level=None if skill.startswith("(") else skill,
                target_audience=[a.strip() for a in audience.split(",") if a.strip()], framework_ids=frameworks,
                learning_outcomes=outcomes, practical_projects=projects, prerequisites=prereqs)
            with st.spinner("Researching sources and building the curriculum…"):
                stored = service.create(req, actor=actor or "admin", use_llm=use_llm)
            st.session_state.selected_curriculum = stored.id
            st.success(f"Created {stored.id} ({stored.curriculum.generation_mode} mode). Open it in the Library tab to review.")
        except (CurriculumError, ValueError) as exc:
            st.error(str(exc))

# ------------------------------------------------------------------ library & review
with library_tab:
    f = st.columns(5)
    all_rows = service.list()
    opts = lambda getter: ["All"] + sorted({getter(r) for r in all_rows if getter(r)})   # noqa: E731
    filters = dict(
        department=f[0].selectbox("Department", opts(lambda r: r.curriculum.department)),
        skill_level=f[1].selectbox("Level", opts(lambda r: r.curriculum.skill_level)),
        industry=f[2].selectbox("Industry", opts(lambda r: r.curriculum.industry)),
        country=f[3].selectbox("Country", opts(lambda r: r.curriculum.country)),
        status=f[4].selectbox("Status", ["All"] + [s.value for s in CurriculumStatus]))
    rows = service.list(**{k: v for k, v in filters.items() if v != "All"})
    st.caption(f"{len(rows)} curriculum(s)")
    st.dataframe([{"id": r.id, "title": r.curriculum.title, "department": r.curriculum.department, "level": r.curriculum.skill_level,
                   "hours": r.curriculum.total_hours, "status": r.status.label, "revision": r.revision,
                   "frameworks": ", ".join(r.curriculum.framework_ids)} for r in rows], use_container_width=True, hide_index=True)
    if rows:
        ids = [r.id for r in rows]
        default = ids.index(st.session_state.selected_curriculum) if st.session_state.get("selected_curriculum") in ids else 0
        sel_id = st.selectbox("Open curriculum", ids, index=default,
                              format_func=lambda i: f"{i} · {next(r.curriculum.title for r in rows if r.id == i)}")
        stored = service.get(sel_id)
        cur = stored.curriculum
        st.subheader(f"{cur.title} — {stored.status.label} (revision {stored.revision})")
        if stored.status == CurriculumStatus.APPROVED:
            st.success(f"Approved by {stored.approved_by}. Document Generation and Marketing may use this version.")
        else:
            st.warning("Not approved yet: other agents do not use this curriculum.")
        problems = cur.validation_problems()
        for p in problems:
            st.error(p)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total hours", f"{cur.total_hours:g}", help=f"basis: {cur.hours_origin.value}")
        m2.metric("Modules", len(cur.modules))
        m3.metric("Learning outcomes", len(cur.learning_outcomes))
        m4.metric("Generation", cur.generation_mode)

        with st.expander("Reviewer notes: gaps and assumptions", expanded=bool(cur.notices)):
            for n in cur.notices:
                st.markdown(f"- {n}")
        st.markdown("**Modules**")
        st.dataframe([{"#": str(m.number), "module": m.title, "hours": m.hours, "topics": len(m.topics), "basis": m.origin.value} for m in cur.modules]
                     + ([{"#": "", "module": "Capstone project", "hours": cur.capstone.hours, "topics": "", "basis": cur.capstone.description.origin.value}] if cur.capstone else []),
                     use_container_width=True, hide_index=True)
        with st.expander("Topics, exercises and learning outcomes"):
            for m in cur.modules:
                st.markdown(f"**{m.number}. {m.title}** ({m.hours:g} h)")
                st.markdown("\n".join(f"- {t.text} _({t.origin.value})_" for t in m.topics) or "_no topics published_")
            st.markdown("**Learning outcomes**")
            st.markdown("\n".join(f"- **{o.id}** {o.text} _({o.origin.value})_" for o in cur.learning_outcomes))

        st.markdown("**Accreditation alignment (proposal)**")
        for rep in stored.alignment:
            with st.expander(f"{rep.framework_name} — {rep.coverage_percent:g}% ({rep.covered} covered · {rep.partial} partial · {rep.missing} missing)"):
                st.dataframe([{"": ICON[a.status], "area": a.title, "result": a.status.value, "where": ", ".join(a.evidence[:4]),
                               "what to add": a.recommendation} for a in rep.areas], use_container_width=True, hide_index=True)
                st.caption(rep.disclaimer)
        if not stored.alignment:
            st.info("No framework selected. Choose one below to get an alignment report.")
        fw_pick = st.multiselect("Re-check against frameworks", [x.framework_id for x in service.frameworks.all()],
                                 default=cur.framework_ids, key=f"fw_{stored.id}")
        if st.button("Update alignment report") and fw_pick:
            try:
                service.align(stored.id, fw_pick); st.rerun()
            except CurriculumError as exc:
                st.error(str(exc))

        try:
            pdf = curriculum_pdf(cur, stored.alignment, curriculum_id=stored.id, status=stored.status,
                                 revision=stored.revision, approved_by=stored.approved_by)
            st.download_button("⬇️ Download PDF", pdf, file_name=f"{stored.id}_curriculum.pdf", mime="application/pdf")
        except PdfRenderError as exc:
            st.error(str(exc))

        # ---- human edits
        with st.expander("✏️ Edit this curriculum (creates a new revision that must be reviewed again)"):
            reviewer = st.text_input("Your name", key=f"who_{stored.id}", value="admin")
            op = st.selectbox("Change", ["Add a topic to a module", "Add a learning outcome", "Set total hours",
                                         "Rename a module", "Add a module", "Remove a module"], key=f"op_{stored.id}")
            nums = [m.number for m in cur.modules]
            edit: dict | None = None
            if op == "Add a topic to a module":
                n, t = st.selectbox("Module", nums, key=f"n_{stored.id}"), st.text_input("Topic", key=f"t_{stored.id}")
                edit = {"op": "add_topic", "module": n, "text": t} if t.strip() else None
            elif op == "Add a learning outcome":
                t = st.text_input("Outcome (start with an action verb)", key=f"o_{stored.id}")
                edit = {"op": "add_outcome", "text": t} if t.strip() else None
            elif op == "Set total hours":
                h = st.number_input("Total hours", min_value=1.0, value=float(cur.total_hours or 1), step=5.0, key=f"h_{stored.id}")
                edit = {"op": "set_total_hours", "hours": h}
            elif op == "Rename a module":
                n, t = st.selectbox("Module", nums, key=f"rn_{stored.id}"), st.text_input("New title", key=f"rt_{stored.id}")
                edit = {"op": "rename_module", "module": n, "title": t} if t.strip() else None
            elif op == "Add a module":
                t = st.text_input("Module title", key=f"am_{stored.id}")
                tp = lines(st.text_area("Topics (one per line)", key=f"amt_{stored.id}"))
                edit = {"op": "add_module", "title": t, "topics": tp} if t.strip() else None
            else:
                n = st.selectbox("Module to remove", nums, key=f"rm_{stored.id}")
                edit = {"op": "remove_module", "module": n}
            if st.button("Apply change", disabled=edit is None):
                try:
                    service.apply_edits(stored.id, reviewer or "admin", [edit]); st.rerun()
                except (CurriculumError, ValueError) as exc:
                    st.error(str(exc))

        # ---- review workflow
        st.markdown("**Review**")
        who = st.text_input("Reviewer name", key=f"rev_{stored.id}")
        note = st.text_area("Note (required to request changes)", key=f"note_{stored.id}", height=70)
        b1, b2, b3 = st.columns(3)
        try:
            if b1.button("📨 Submit for review", disabled=stored.status not in (CurriculumStatus.DRAFT, CurriculumStatus.CHANGES_REQUESTED)):
                service.submit(stored.id, who or "admin", note); st.rerun()
            if b2.button("✅ Approve", disabled=stored.status != CurriculumStatus.IN_REVIEW):
                service.approve(stored.id, who, note); st.rerun()
            if b3.button("↩️ Request changes", disabled=stored.status != CurriculumStatus.IN_REVIEW):
                service.request_changes(stored.id, who, note); st.rerun()
        except CurriculumError as exc:
            st.error(str(exc))
        with st.expander("Review log"):
            st.dataframe(stored.review_log, use_container_width=True, hide_index=True)

with chat_tab:
    st.caption("Try: frameworks · create a curriculum for TRK-002 · list · align CUR-XXXXXX · أنشئ منهج TRK-001")
    if prompt := st.chat_input("e.g. create a curriculum for TRK-001 with 80 hours"):
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            st.markdown(ctx.registry.dispatch(AGENT, AgentRequest.from_text(prompt)).text)
