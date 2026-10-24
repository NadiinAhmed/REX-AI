"""Agent 2 dashboard: Egypt & Saudi Arabia education/training tenders.

Run a scan, see Egypt opportunities flagged for review, filter and search the pipeline, export
CSV (opens in Excel or Google Sheets), mark opportunities reviewed/dismissed, and chat with the agent.
"""
from __future__ import annotations

import csv
import io
from datetime import date

import streamlit as st

from src.agents.base import AgentRequest
from src.rfp.models import Category, Country, OpportunityStatus
from src.ui_context import get_context

AGENT = "rfp_research"
st.set_page_config(page_title="REX AI · RFP Opportunities", page_icon="📑", layout="wide")
st.title("📑 RFP & tender opportunities")
st.caption("Education and training tenders in **Egypt** (flagged for immediate review) and "
           "**Saudi Arabia** (monitored). Listings outside these two countries are always excluded.")

ctx = get_context()
repo = ctx.rfp_repository
if repo is None or AGENT not in {a.name for a in ctx.registry.list_agents() if a.status.value == "implemented"}:
    st.error("The RFP research agent is not available. See the warnings on the main page.")
    st.stop()
agent = ctx.registry.get(AGENT)

# ------------------------------------------------------------------ scan
left, right = st.columns([1, 3])
if left.button("🔎 Run scan", type="primary"):
    with st.spinner("Scanning tender feeds…"):
        st.session_state.rfp_report = agent.run_scan()
report = st.session_state.get("rfp_report")
if report:
    right.success(f"Read {report.fetched} listings · {len(report.new)} new · {len(report.updated)} refreshed · "
                  f"{len(report.excluded)} excluded · {report.expired_marked} newly expired")
    for notice in report.source_notices:
        st.warning(notice)
    if report.excluded:
        with st.expander(f"Why {len(report.excluded)} listing(s) were excluded"):
            st.dataframe([{"title": e.title, "reason": e.reason.value.replace("_", " "), "detail": e.detail}
                          for e in report.excluded], use_container_width=True)

# ------------------------------------------------------------------ Egypt alerts (business rule)
flagged = repo.list(country=Country.EGYPT.value, statuses=(OpportunityStatus.FLAGGED.value,))
if flagged:
    st.error(f"🔔 {len(flagged)} Egypt opportunity(ies) flagged for immediate review")
    for r in flagged:
        due = f"{r.deadline.isoformat()} ({(r.deadline - date.today()).days} days)" if r.deadline else "deadline not stated"
        st.markdown(f"- **{r.id}** · {r.title} · {r.issuing_organization or '—'} · {due} · fit **{r.fit}**")

# ------------------------------------------------------------------ table + filters
st.subheader("Pipeline")
c1, c2, c3, c4 = st.columns(4)
country = c1.selectbox("Country", ["All", "Egypt", "Saudi Arabia"])
statuses = c2.multiselect("Status", [s.value for s in OpportunityStatus],
                          default=[OpportunityStatus.FLAGGED.value, OpportunityStatus.MONITORING.value,
                                   OpportunityStatus.REVIEWED.value])
category = c3.selectbox("Category", ["All"] + [c.label for c in Category])
query = c4.text_input("Search title / organization")

code = {"Egypt": "EG", "Saudi Arabia": "SA"}.get(country)
records = repo.list(country=code, statuses=tuple(statuses) or None)
if category != "All":
    wanted = next(c.value for c in Category if c.label == category)
    records = [r for r in records if wanted in r.categories]
if query.strip():
    q = query.strip().lower()
    records = [r for r in records if q in r.title.lower() or q in (r.issuing_organization or "").lower()]

rows = [{
    "id": r.id, "title": r.title, "organization": r.issuing_organization, "country": Country(r.country).label,
    "deadline": r.deadline.isoformat() if r.deadline else "", "published": r.publication_date.isoformat() if r.publication_date else "",
    "category": ", ".join(Category(c).label for c in r.categories), "fit": r.fit, "status": r.status,
    "link": r.submission_link or "", "synthetic": r.synthetic,
} for r in records]
st.caption(f"{len(rows)} opportunity(ies). Fit is a first assessment from the listing, not a guarantee.")
if any(r["synthetic"] for r in rows):
    st.info("Rows marked synthetic come from the demo feed and are not real tenders.")
st.dataframe(rows, use_container_width=True, hide_index=True,
             column_config={"link": st.column_config.LinkColumn("link")})

if rows:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    st.download_button("⬇️ Export CSV (Excel / Google Sheets)", buffer.getvalue().encode("utf-8-sig"),
                       file_name="rfp_opportunities.csv", mime="text/csv")

# ------------------------------------------------------------------ details + human decision
if records:
    st.subheader("Review an opportunity")
    chosen = st.selectbox("Opportunity", [r.id for r in records],
                          format_func=lambda i: f"{i} · {next(r.title for r in records if r.id == i)}")
    detail = agent.handle(AgentRequest.from_text(chosen))
    st.markdown(detail.text)
    b1, b2 = st.columns(2)
    if b1.button("✅ Mark reviewed"):
        repo.set_status(chosen, OpportunityStatus.REVIEWED)
        st.rerun()
    if b2.button("🚫 Dismiss"):
        repo.set_status(chosen, OpportunityStatus.DISMISSED)
        st.rerun()

# ------------------------------------------------------------------ chat with the agent
st.subheader("Ask the RFP agent")
st.caption("Try: scan · egypt · saudi · RFP-XXXXXX · ابحث · مصر · السعودية")
if prompt := st.chat_input("e.g. show Egypt opportunities"):
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        st.markdown(ctx.registry.dispatch(AGENT, AgentRequest.from_text(prompt)).text)