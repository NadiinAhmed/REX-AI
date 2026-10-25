"""Curriculum -> DocumentContent -> PDF (brief: 'Generate a professional PDF curriculum')."""
from __future__ import annotations

from src.curriculum.models import AreaStatus, Curriculum, CurriculumStatus, Origin
from src.pdf.content import DocumentContent, Section, bullets, note, para, table
from src.pdf.renderer import render_pdf

ORIGIN_LABEL = {
    Origin.CATALOG: "academy record", Origin.DOCUMENT: "academy document", Origin.FRAMEWORK: "framework gap",
    Origin.USER: "added by staff", Origin.TEMPLATE: "proposal (template)", Origin.LLM: "proposal (AI-written)",
}
STATUS_MARK = {AreaStatus.COVERED: "Covered", AreaStatus.PARTIAL: "Partial", AreaStatus.MISSING: "Missing"}


def _hours(h: float) -> str:
    return f"{h:g}"


def curriculum_document(cur: Curriculum, alignment: list, *, curriculum_id: str = "", status: CurriculumStatus | None = None,
                        revision: int = 1, approved_by: str | None = None) -> DocumentContent:
    meta = [("Curriculum ID", curriculum_id or "—"), ("Course", f"{cur.course_id or 'new course'}"),
            ("Department", cur.department or "not specified"), ("Industry", cur.industry or "not specified"),
            ("Skill level", cur.skill_level.replace("_", " ")), ("Country", cur.country or "not specified"),
            ("Total training hours", _hours(cur.total_hours or 0) + ("" if cur.hours_origin != Origin.TEMPLATE else " (proposed)")),
            ("Status", f"{(status or CurriculumStatus.DRAFT).label} · revision {revision}"
                       + (f" · approved by {approved_by}" if approved_by else ""))]
    sections: list[Section] = []
    if status != CurriculumStatus.APPROVED:
        sections.append(Section(heading="Review status", blocks=[note(
            "This curriculum is NOT approved. It is a draft for instructor/administrator review.")]))
    overview = [para(cur.summary)] if cur.summary else []
    if cur.title_ar:
        overview.append(para(f"Arabic title: {cur.title_ar}"))
    if cur.career_paths:
        overview.append(para("Career paths: " + ", ".join(cur.career_paths) + "."))
    if cur.tools:
        overview.append(para("Tools covered: " + ", ".join(cur.tools) + "."))
    if overview:
        sections.append(Section(heading="Course overview", blocks=overview))
    sections.append(Section(heading="Course objectives", blocks=[bullets([o.text for o in cur.objectives])]))
    sections.append(Section(heading="Target audience and prerequisites", blocks=[
        para("Target audience: " + (", ".join(cur.target_audience) or "not defined.")),
        para("Prerequisites: " + ("; ".join(p.text for p in cur.prerequisites) if cur.prerequisites else "none published."))]))
    sections.append(Section(heading="Learning outcomes", blocks=[table(
        ["ID", "Learning outcome", "Modules", "Basis"],
        [[o.id, o.text, ", ".join(map(str, o.module_numbers)) or "all", ORIGIN_LABEL[o.origin]] for o in cur.learning_outcomes])]))
    sections.append(Section(heading="Modules and training hours", blocks=[table(
        ["#", "Module", "Hours", "Topics"],
        [[str(m.number), m.title, _hours(m.hours), str(len(m.topics))] for m in cur.modules]
        + ([["", "Capstone project", _hours(cur.capstone_hours), ""]] if cur.capstone else [])
        + [["", "Total", _hours(cur.allocated_hours), ""]])]))
    for m in cur.modules:
        blocks = []
        if m.topics:
            blocks += [para("Topics"), bullets([t.text + (f"  [{t.source}]" if t.origin == Origin.DOCUMENT and t.source else "")
                                                  for t in m.topics])]
        else:
            blocks.append(para("No topic detail is published for this module yet."))
        blocks += [para("Practical exercises"), bullets([e.text for e in m.exercises]), para(f"Assessment: {m.assessment}.")]
        sections.append(Section(heading=f"Module {m.number}: {m.title} ({_hours(m.hours)} h)", blocks=blocks))
    sections.append(Section(heading="Assessment scheme", blocks=[table(
        ["Component", "Type", "Weight", "Description"],
        [[a.name, a.kind, f"{a.weight_percent:g}%", a.description] for a in cur.assessments])]))
    if cur.capstone:
        cap = [para(cur.capstone.description.text), para(f"Allocated hours: {_hours(cur.capstone.hours)}.")]
        if cur.capstone.deliverables:
            cap += [para("Deliverables"), bullets([d.text for d in cur.capstone.deliverables])]
        sections.append(Section(heading="Capstone project", blocks=cap))
    for rep in alignment:
        blocks = [para(f"{rep.framework_name} — issuer: {rep.issuer}. Coverage: {rep.coverage_percent:g}% "
                       f"({rep.covered} covered, {rep.partial} partial, {rep.missing} missing)."),
                  table(["Area", "Result", "Where it is taught", "What to add"],
                        [[a.title, STATUS_MARK[a.status], ", ".join(a.evidence[:4]) or "—", a.recommendation or "—"] for a in rep.areas]),
                  note(rep.disclaimer)]
        sections.append(Section(heading="Accreditation alignment: " + rep.framework_id, blocks=blocks))
    if not alignment:
        sections.append(Section(heading="Accreditation alignment", blocks=[
            para("No framework was selected, so no alignment report exists."),
            note("No accreditation is claimed for this course by REX AI.")]))
    research = [bullets([f"{s.kind.replace('_', ' ')}: {s.reference}" + (f" — {s.detail}" if s.detail else "") for s in cur.sources])]
    if cur.market_signals:
        research.append(para("Market signals: " + "; ".join(cur.market_signals) + "."))
    sections.append(Section(heading="Research sources and traceability", blocks=research))
    if cur.notices:
        sections.append(Section(heading="Gaps and assumptions to review", blocks=[bullets(cur.notices)]))
    return DocumentContent(
        title=f"{cur.title}: Curriculum", subtitle=f"Andalusia Academy · generated by REX AI ({cur.generation_mode} mode)",
        meta=meta, sections=sections,
        disclaimer="Proposal for review. This document does not state or imply that the course is accredited, "
                   "certified or recognised by any body. Items marked 'proposal' were produced by templates or an AI model.")


def curriculum_pdf(cur: Curriculum, alignment: list, **kwargs) -> bytes:
    return render_pdf(curriculum_document(cur, alignment, **kwargs))
