"""Course Launch agent (staff only): runs the whole launch from the chat.

    staff:  Launch a new course "Cloud Computing" for beginners, 60 hours, fee 4000 EGP,
            20% early-bird until Dec 15, email our leads and ali@example.com
    REX:    plan LP-1A2B3C: curriculum (in review), course, fee, offer, recipients
    staff:  approve LP-1A2B3C as Nadin         -> course live, offer active, e-mail drafted and shown
    staff:  send LP-1A2B3C                     -> e-mail approved and sent (one per recipient)

Also: "cancel LP-1A2B3C" forgets the plan in this chat. Arabic: "اعتمد LP-… باسم نادين", "ارسل LP-…", "الغ LP-…".

This class only understands chat commands and writes the replies. The work and the rules are in
CourseLaunchWorkflow and the services it calls.
"""
from __future__ import annotations

import re

from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode, SourceReference
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.workflows.course_launch import CourseLaunchWorkflow, LaunchError, LaunchRecord, PartialLaunchError
from src.workflows.launch_plan import LaunchPlan, parse_launch_request

_ID = r"(lp-[0-9a-f]{6})"
_NAME = r"([^\n]{2,60}?)\s*[.!]?\s*$"
_APPROVE = re.compile(normalize_arabic(rf"(?:\bapprove|اعتمد|وافق علي)\s+{_ID}(?:\s+(?:as|by|باسم|بواسطه)\s+{_NAME})?"), re.I)
_SEND = re.compile(normalize_arabic(rf"(?:\bsend|ارسل|ابعت)\s+{_ID}(?:\s+(?:as|by|باسم|بواسطه)\s+{_NAME})?"), re.I)
_CANCEL = re.compile(normalize_arabic(rf"(?:\bcancel|الغ|الغي)\s+{_ID}"), re.I)
_ANY_ID = re.compile(_ID, re.I)


class CourseLaunchAgent(Agent):
    name = "course_launch"
    description = "Course Launch workflow: new course, curriculum, offer and e-mail campaign from one sentence"

    def __init__(self, workflow: CourseLaunchWorkflow) -> None:
        self._wf = workflow

    def expects_reply(self, state: dict) -> bool:
        """While a plan is being completed (\"which recipients?\"), the next message belongs here."""
        return bool(state.get("pending_plan"))

    # ------------------------------------------------------------------ dispatch
    def handle(self, request: AgentRequest) -> AgentResponse:
        message = request.message
        lang = "ar" if contains_arabic(message) else "en"
        norm = normalize_arabic(message.lower())
        state = dict(request.state)
        record = LaunchRecord.model_validate(state["launch"]) if state.get("launch") else None

        for pattern, action in ((_APPROVE, self._approve), (_SEND, self._send), (_CANCEL, self._cancel)):
            m = pattern.search(norm)
            if m:
                if record is None or m.group(1).upper() != record.plan_id:
                    return self._reply(state, lang, self._t(lang,
                        f"I don't have plan {m.group(1).upper()} in this conversation."
                        + (f" The current plan is {record.plan_id}." if record else " Start with a launch sentence."),
                        f"لا توجد خطة {m.group(1).upper()} في هذه المحادثة."
                        + (f" الخطة الحالية هي {record.plan_id}." if record else " ابدأ بجملة الإطلاق.")), "unknown_plan")
                name = (m.group(2) if m.lastindex and m.lastindex >= 2 else None)
                return action(state, record, lang, self._original_name(message, name))
        if record is not None and record.stage != "sent" and _ANY_ID.search(norm) is None and not state.get("pending_plan"):
            return self._reply(state, lang, self._t(lang,
                f"Plan {record.plan_id} is still open. Type **approve {record.plan_id} as <your name>**, "
                f"**send {record.plan_id}**, or **cancel {record.plan_id}** before starting a new launch.",
                f"الخطة {record.plan_id} ما زالت مفتوحة. اكتب **اعتمد {record.plan_id} باسم <اسمك>** أو "
                f"**ارسل {record.plan_id}** أو **الغ {record.plan_id}** قبل بدء إطلاق جديد."), "plan_open")
        return self._plan(state, message, lang)

    @staticmethod
    def _original_name(message: str, name: str | None) -> str | None:
        """The name as typed (the regex ran on the normalised text, which changes Arabic letters/case)."""
        if not name:
            return None
        m = re.search(r"(?:\bas|\bby|باسم|بواسطة|بواسطه)\s+(.+?)\s*[.!]?\s*$", message, re.I)
        return m.group(1).strip() if m else name.strip()

    # ------------------------------------------------------------------ message 1: the plan
    def _plan(self, state: dict, message: str, lang: str) -> AgentResponse:
        new = parse_launch_request(message)
        if state.get("pending_plan"):
            new = _merge(LaunchPlan.model_validate(state["pending_plan"]), new)
        if new.missing:
            state["pending_plan"] = new.model_dump(mode="json")
            lines = [self._t(lang, "To prepare the launch I still need:", "لتجهيز الإطلاق ما زلت أحتاج:")]
            lines += [f"- {m}" for m in new.missing]
            lines.append(self._t(lang, "Reply with just the missing details.", "أرسل التفاصيل الناقصة فقط."))
            return self._reply(state, lang, "\n".join(lines), "launch_needs_info")
        state.pop("pending_plan", None)
        try:
            record, stored = self._wf.prepare(new)
        except LaunchError as exc:
            return self._reply(state, lang, self._t(lang, f"I couldn't prepare the launch: {exc}",
                                                    f"تعذر تجهيز الإطلاق: {exc}"), "launch_failed", error=True)
        state["launch"] = record.model_dump(mode="json")
        return self._reply(state, lang, self._summary(record, stored, lang), "launch_prepared", record=record)

    def _summary(self, r: LaunchRecord, stored, lang: str) -> str:
        p, cur = r.plan, stored.curriculum
        t = lambda en, ar: self._t(lang, en, ar)
        recipients = self._wf.recipients(r)
        lines = [t(f"📋 **Launch plan {r.plan_id}** is prepared. Nothing is live yet.",
                   f"📋 **خطة الإطلاق {r.plan_id}** جاهزة. لم يُنشر أي شيء بعد."),
                 t(f"**Curriculum** {stored.id} (in review): {cur.title}, {cur.total_hours:g} hours, "
                   f"{len(cur.modules)} modules",
                   f"**المنهج** {stored.id} (قيد المراجعة): {cur.title}، {cur.total_hours:g} ساعة، {len(cur.modules)} وحدات")]
        lines += [f"  {m.number}. {m.title} ({m.hours:g}h)" for m in cur.modules]
        lines.append(t("**Course**: added to the catalog when you approve" + (f" · level {p.skill_level}" if p.skill_level else ""),
                       "**المقرر**: يُضاف إلى الكتالوج عند الاعتماد" + (f" · المستوى {p.skill_level}" if p.skill_level else "")))
        lines.append(t(f"**Fee**: {p.fee_amount:g} {p.fee_currency}" if p.fee_amount is not None else "**Fee**: not given (students will be told it is not available)",
                       f"**الرسوم**: {p.fee_amount:g} {p.fee_currency}" if p.fee_amount is not None else "**الرسوم**: غير محددة (سيُقال للطلاب إنها غير متاحة)"))
        lines.append(t(f"**Offer**: {p.offer_percent:g}% until {p.offer_valid_until}" if p.offer_percent else "**Offer**: none",
                       f"**العرض**: {p.offer_percent:g}% حتى {p.offer_valid_until}" if p.offer_percent else "**العرض**: لا يوجد"))
        lines.append(t(f"**E-mail**: written after approval, then shown to you · {len(recipients)} recipient(s)"
                       + (" (consented leads included)" if p.email_leads else ""),
                       f"**الإيميل**: يُكتب بعد الاعتماد ثم يُعرض عليك · {len(recipients)} مستلم"
                       + (" (يشمل العملاء الموافقين)" if p.email_leads else "")))
        for note in cur.notices[:3]:
            lines.append(f"⚠ {note}")
        lines.append(t(f"\nReview the curriculum, then type **approve {r.plan_id} as <your name>**.",
                       f"\nراجع المنهج ثم اكتب **اعتمد {r.plan_id} باسم <اسمك>**."))
        return "\n".join(lines)

    # ------------------------------------------------------------------ message 2: approve
    def _approve(self, state: dict, record: LaunchRecord, lang: str, name: str | None) -> AgentResponse:
        if not name:
            return self._reply(state, lang, self._t(lang,
                f"Approval needs your name: **approve {record.plan_id} as <your name>**.",
                f"الاعتماد يحتاج اسمك: **اعتمد {record.plan_id} باسم <اسمك>**."), "approve_needs_name")
        try:
            record = self._wf.approve(record, name)
        except PartialLaunchError as exc:
            state["launch"] = exc.record.model_dump(mode="json")
            return self._reply(state, lang, self._failed(exc.record, str(exc), lang, "approve"), "approve_failed",
                               record=exc.record, error=True)
        except LaunchError as exc:
            return self._reply(state, lang, str(exc), "approve_failed", error=True)
        state["launch"] = record.model_dump(mode="json")
        e = record.email or {}
        t = lambda en, ar: self._t(lang, en, ar)
        lines = [t(f"✅ Approved by {record.approved_by}.", f"✅ تم الاعتماد بواسطة {record.approved_by}."),
                 t(f"- Curriculum {record.curriculum_id} approved; **{record.course_id}** is now in the catalog "
                   "(REX and every agent can see it)",
                   f"- تم اعتماد المنهج {record.curriculum_id}؛ **{record.course_id}** أصبح في الكتالوج (يراه REX وكل الوكلاء)")]
        if "fee_set" in record.completed:
            lines.append(t(f"- Fee {record.plan.fee_amount:g} {record.plan.fee_currency} saved",
                           f"- تم حفظ الرسوم {record.plan.fee_amount:g} {record.plan.fee_currency}"))
        if "offer_approved" in record.completed:
            lines.append(t(f"- Offer {record.plan.offer_percent:g}% active until {record.plan.offer_valid_until}",
                           f"- العرض {record.plan.offer_percent:g}% مفعل حتى {record.plan.offer_valid_until}"))
        lines += [t("\n**The e-mail (draft, fact-checked, not sent):**", "\n**الإيميل (مسودة تم فحصها، لم تُرسل):**"),
                  t(f"**Subject:** {e.get('email_subject') or e.get('headline', '')}", f"**العنوان:** {e.get('email_subject') or e.get('headline', '')}"),
                  f"**{e.get('headline', '')}**", e.get("body", ""), f"👉 {e.get('cta', '')}",
                  t(f"\n{len(self._wf.recipients(record))} recipient(s), one e-mail each. Read it, then type "
                    f"**send {record.plan_id}**.",
                    f"\n{len(self._wf.recipients(record))} مستلم، إيميل منفصل لكل شخص. اقرأه ثم اكتب **ارسل {record.plan_id}**.")]
        return self._reply(state, lang, "\n".join(lines), "launch_approved", record=record)

    # ------------------------------------------------------------------ message 3: send
    def _send(self, state: dict, record: LaunchRecord, lang: str, name: str | None) -> AgentResponse:
        try:
            record, report = self._wf.send(record, name or record.approved_by or "")
        except LaunchError as exc:
            return self._reply(state, lang, str(exc), "send_failed", error=True)
        state["launch"] = record.model_dump(mode="json")
        t = lambda en, ar: self._t(lang, en, ar)
        verb = t("logged as a dry run (nothing left the system, MARKETING_DELIVERY_MODE=dry_run)",
                 "سُجلت كتجربة (لم يخرج شيء من النظام، MARKETING_DELIVERY_MODE=dry_run)") if report.dry_run \
            else t("sent", "أُرسلت")
        lines = [t(f"📧 {len(report.sent)} e-mail(s) {verb}.", f"📧 {len(report.sent)} إيميل {verb}.")]
        if report.skipped:
            lines.append(t(f"{len(report.skipped)} already sent earlier, skipped.", f"{len(report.skipped)} أُرسلت سابقاً، تم تخطيها."))
        for address, reason in report.failed.items():
            lines.append(f"⚠ {_mask(address)}: {reason}")
        if report.failed:
            lines.append(t(f"Fix the problem and type **send {record.plan_id}** again; only the failed ones are retried.",
                           f"أصلح المشكلة واكتب **ارسل {record.plan_id}** مرة أخرى؛ ستُعاد المحاولة للفاشلة فقط."))
        else:
            lines.append(t(f"🎉 Launch {record.plan_id} is complete. Every delivery is logged on the Marketing page.",
                           f"🎉 اكتمل الإطلاق {record.plan_id}. كل عملية إرسال مسجلة في صفحة التسويق."))
        return self._reply(state, lang, "\n".join(lines), "launch_sent", record=record, error=bool(report.failed))

    def _cancel(self, state: dict, record: LaunchRecord, lang: str, _name) -> AgentResponse:
        state.pop("launch", None)
        state.pop("pending_plan", None)
        done = record.stage != "awaiting_approval"
        return self._reply(state, lang, self._t(lang,
            f"Plan {record.plan_id} is closed in this chat. "
            + ("What was already approved stays live; change it on the admin pages." if done else
               f"Curriculum {record.curriculum_id} stays in review on the Curriculum page; nothing went live."),
            f"تم إغلاق الخطة {record.plan_id} في هذه المحادثة. "
            + ("ما تم اعتماده يبقى مفعلاً؛ عدّله من صفحات الإدارة." if done else
               f"المنهج {record.curriculum_id} يبقى قيد المراجعة في صفحة المناهج؛ لم يُنشر شيء.")), "launch_cancelled")

    # ------------------------------------------------------------------ helpers
    def _failed(self, r: LaunchRecord, error: str, lang: str, command: str) -> str:
        done = ", ".join(r.completed)
        return self._t(lang,
            f"⚠ Stopped: {error}\nAlready done (kept, not repeated): {done}.\n"
            f"Fix the problem and type **{command} {r.plan_id} as <your name>** to continue.",
            f"⚠ توقف: {error}\nما تم إنجازه (محفوظ ولن يتكرر): {done}.\n"
            f"أصلح المشكلة واكتب **اعتمد {r.plan_id} باسم <اسمك>** للمتابعة.")

    def _reply(self, state: dict, lang: str, text: str, intent: str, *, record: LaunchRecord | None = None,
               error: bool = False) -> AgentResponse:
        sources = []
        if record and record.course_id:
            sources.append(SourceReference(source_type="course", reference=f"Launch {record.plan_id}",
                                           course_id=record.course_id))
        if record:
            sources.append(SourceReference(source_type="curriculum", reference=record.curriculum_id))
        return AgentResponse(agent_name=self.name, text=text, language=lang,
                             mode=ResponseMode.ERROR if error else ResponseMode.DETERMINISTIC,
                             sources=sources, state=state,
                             data={"intent": intent, "launch": record.model_dump(mode="json") if record else None})

    @staticmethod
    def _t(lang: str, en: str, ar: str) -> str:
        return ar if lang == "ar" else en


def _merge(old: LaunchPlan, new: LaunchPlan) -> LaunchPlan:
    """A follow-up answer fills the gaps; values given again replace the old ones."""
    data = old.model_dump()
    for key, value in new.model_dump().items():
        if key == "problems":
            data[key] = value
        elif key == "topics" and value and old.topics and not new.title:
            data[key] = list(dict.fromkeys([*old.topics, *value]))     # "also add Kubernetes" style follow-ups
        elif key == "recipients":
            data[key] = list(dict.fromkeys([*old.recipients, *new.recipients]))
        elif key == "email_leads":
            data[key] = old.email_leads or new.email_leads
        elif key == "language":
            continue                                   # keep the language of the launch sentence
        elif value not in (None, [], ""):
            data[key] = value
    return LaunchPlan.model_validate(data)


def _mask(address: str) -> str:
    user, _, domain = address.partition("@")
    return f"{user[:2]}***@{domain}"
