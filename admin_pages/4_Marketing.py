"""Admin: Marketing Agent — create campaigns, review/edit drafts, approve, then send APPROVED drafts.

Sending goes through DistributionService only: approved drafts, re-checked facts, explicit confirmation,
every attempt logged. MARKETING_DELIVERY_MODE=dry_run (default) logs without sending."""
from __future__ import annotations

import streamlit as st

from src.db.models import DraftStatus
from src.marketing.models import CampaignBrief, CampaignObjective, DraftVariation, Platform, Tone
from src.marketing.distribution import DistributionError
from src.marketing.platforms import spec_for
from src.services.approval_service import ApprovalError
from src.ui_context import get_context, require_admin

st.set_page_config(page_title="REX AI · Marketing", page_icon="📣", layout="wide")
st.title("📣 Marketing Agent")
ctx = get_context()
if not require_admin(ctx):
    st.stop()
agent = ctx.registry.get("marketing")
st.caption("Copy is generated only from approved course data and approved offers. Every draft waits for a named "
           "reviewer. Only approved drafts can be sent, after an explicit confirmation; every attempt is logged.")

tab_new, tab_review, tab_approved = st.tabs(["Create campaign", "Review drafts", "Approved"])

with tab_new:
    courses = {c.course_id: c for c in ctx.courses.list_approved()}
    with st.form("new_campaign"):
        course_id = st.selectbox("Course", list(courses), format_func=lambda cid: f"{cid} — {courses[cid].title}")
        platforms = st.multiselect("Platforms", list(Platform), default=[Platform.LINKEDIN],
                                   format_func=lambda p: spec_for(p).label_en)
        objective = st.selectbox("Objective", list(CampaignObjective), format_func=lambda o: o.value.replace("_", " "))
        c1, c2, c3 = st.columns(3)
        language = c1.radio("Language", ["en", "ar"], format_func={"en": "English", "ar": "العربية"}.get)
        tone = c2.selectbox("Tone", list(Tone), format_func=lambda t: t.value)
        variations = c3.slider("Variations per platform", 1, 5, 3)
        audience = st.text_input("Target audience (optional, defaults to the course's approved audience)")
        budget = st.text_input("Budget note (optional, shapes the strategy only)")
        submitted = st.form_submit_button("Generate drafts")
    if submitted:
        if not platforms:
            st.error("Choose at least one platform.")
        else:
            brief = CampaignBrief(course_id=course_id, platforms=platforms, objective=objective, language=language,
                                  tone=tone, variations=variations, audience=audience or None, budget_note=budget or None)
            with st.spinner("Generating and fact-checking…"):
                response = agent.create_campaign(brief)
            st.caption(f"Mode: {response.mode.value} · {ctx.llm_status}")
            st.markdown(response.text)

with tab_review:
    reviewer = st.text_input("Your name (recorded with every decision)", key="reviewer")
    pending = ctx.marketing.list_drafts(DraftStatus.PENDING_REVIEW)
    st.write(f"{len(pending)} draft(s) waiting for review.")
    campaigns: dict = {}
    for d in pending:
        campaign = campaigns.setdefault(d.campaign_id, ctx.marketing.get_campaign(d.campaign_id))
        v = DraftVariation.model_validate(d.content)
        label = f"{spec_for(v.platform).label_en} · {campaign.course_id} · {v.language} · {v.headline[:60]}"
        with st.expander(label + (" · edited" if d.edited else "")):
            blocks = [i for i in d.claim_issues if i["severity"] == "block"]
            for issue in d.claim_issues:
                (st.error if issue["severity"] == "block" else st.warning)(f"{issue['kind']}: “{issue['span']}” — "
                                                                          f"{issue['explanation']}")
            if d.evaluation:
                st.caption("Checks: " + " · ".join(f"{s['dimension']} {s['score']}" for s in d.evaluation["scores"]
                                                   if s["score"] is not None))
            subject = st.text_input("Email subject", v.email_subject or "", key=f"s{d.id}") if v.email_subject else None
            headline = st.text_input("Headline", v.headline, key=f"h{d.id}")
            body = st.text_area("Body", v.body, height=180, key=f"b{d.id}")
            cta = st.text_input("Call to action", v.cta, key=f"c{d.id}")
            tags = st.text_input("Hashtags", " ".join(v.hashtags), key=f"t{d.id}")
            note = st.text_input("Review note (optional)", key=f"n{d.id}")
            b1, b2, b3 = st.columns(3)
            try:
                if b1.button("Save edits & re-check", key=f"e{d.id}"):
                    edited = v.model_copy(update={"headline": headline, "body": body, "cta": cta,
                                                  "hashtags": tags.split(), "email_subject": subject})
                    issues = ctx.approvals.edit(d.id, DraftVariation.model_validate(edited.model_dump()), reviewer)
                    st.success(f"Saved. {sum(i.severity == 'block' for i in issues)} blocking issue(s).")
                    st.rerun()
                if b2.button("Approve", key=f"a{d.id}", disabled=bool(blocks)):
                    ctx.approvals.approve(d.id, reviewer, note or None)
                    st.rerun()
                if b3.button("Reject", key=f"r{d.id}"):
                    ctx.approvals.reject(d.id, reviewer, note or None)
                    st.rerun()
            except (ApprovalError, ValueError) as exc:
                st.error(str(exc))

with tab_approved:
    approved = ctx.marketing.list_drafts(DraftStatus.APPROVED)
    dist = ctx.distribution
    st.write(f"{len(approved)} approved draft(s). Copy the text, or send it below.")
    if dist is not None and dist.dry_run:
        st.info("Delivery mode: **dry run**. Sending runs every check and logs the attempt, but nothing leaves "
                "the system. Set MARKETING_DELIVERY_MODE=live in .env to really send.")
    for d in approved:
        v = DraftVariation.model_validate(d.content)
        st.markdown(f"**{spec_for(v.platform).label_en}** · approved by {d.reviewed_by} on {d.reviewed_at:%Y-%m-%d %H:%M}"
                    + (f" · note: {d.review_note}" if d.review_note else ""))
        st.code(v.full_text(), language=None)
        if dist is None:
            continue
        channels = dist.channels(v.platform)
        with st.expander("Send this draft"):
            if not channels:
                st.caption("Only e-mail and LinkedIn drafts can be sent. For those, configure SMTP_* or "
                           "LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN in .env.")
                continue
            labels = {c.label: c.name for c in channels}
            choice = st.selectbox("Channel", list(labels), key=f"ch{d.id}")
            recipients = ""
            if labels[choice] == "email":
                recipients = st.text_input("Recipients (comma-separated, max 10)", key=f"to{d.id}")
            sender = st.text_input("Your name", key=f"by{d.id}")
            confirmed = st.checkbox("I confirm this approved text may be sent outside the academy.", key=f"ok{d.id}")
            repeat = st.checkbox("Send again (it was already sent to this target)", key=f"rp{d.id}")
            if st.button("Send", key=f"send{d.id}", type="primary"):
                try:
                    outcome = dist.deliver(d.id, labels[choice], requested_by=sender, confirmed=confirmed,
                                           recipients=recipients, allow_repeat=repeat)
                    (st.success if outcome.status.value == "sent" else st.info)(outcome.message)
                except DistributionError as exc:
                    st.error(str(exc))
            history = dist.history(d.id)
            if history:
                st.caption("Delivery log")
                st.table([{"when": h.created_at.strftime("%Y-%m-%d %H:%M"), "channel": h.channel,
                           "target": h.target, "status": h.status, "by": h.requested_by,
                           "error": h.error or ""} for h in history])
