# Course Launch workflow (staff)

Launch a new course from the chat: **curriculum → catalog course (with fee) → offer → marketing e-mail**,
starting from one sentence. Staff only: students cannot reach it (the orchestrator's permission check).

## How to use it (three messages)

| # | You type | What happens | What is live afterwards |
|---|---|---|---|
| 1 | `Launch a new course "Cloud Computing" covering Linux, Docker and AWS, for beginners, 60 hours, fee 4000 EGP, 20% early-bird until Dec 15, email our leads and ali@example.com` | The sentence is parsed into a plan. A curriculum is generated and put **in review**. You see the modules, fee, offer and the number of recipients. | Nothing |
| 2 | `approve LP-1A2B3C as Nadin` | The curriculum is approved, so the course is published to the catalog. The fee is stored, the offer is proposed and approved, and the e-mail is **drafted, fact-checked and shown to you**. | Course, fee and offer (REX answers students about it immediately) |
| 3 | `send LP-1A2B3C` | The e-mail you just read is approved and sent, **one e-mail per recipient**. | The e-mail is sent (or logged as a dry run) |

Arabic works the same way: `أطلق كورس جديد اسمه ... المحاور: ...` → `اعتمد LP-… باسم نادين` → `ارسل LP-…`.
`cancel LP-…` (`الغ LP-…`) closes the plan in this chat.

If something is missing (title, topics, recipients, offer end date), REX asks for **only that** and
merges your answer into the plan. It never guesses.

### What the sentence can contain

| Field | Examples | Required |
|---|---|---|
| Title | `"Cloud Computing"` in quotes, `course called Cloud Computing`, `كورس جديد اسمه ...` | yes |
| Topics | `covering Linux, Docker and AWS`, `topics: ...`, `المحاور: ...` | yes (a new course has no catalog content to build a curriculum from) |
| Level | `beginners`, `intermediate`, `للمبتدئين` | no |
| Hours | `60 hours`, `٦٠ ساعة` | no |
| Fee | `4000 EGP`, `$300`, `٤٠٠٠ جنيه` | no (without it REX tells students the fee is not available) |
| Offer | `20% until Dec 15`, `خصم ٢٠٪ حتى ١٥ ديسمبر` | no; if given, the end date is required |
| Recipients | `our leads` (consented leads), e-mail addresses, or both | yes |

## Why three messages and not one

1. **The project brief requires human approval** before marketing content is published or sent.
2. **An offer and a campaign need a course in the catalog.** `OfferService.propose()` and the marketing
   fact loader refuse unknown courses, and a new course enters the catalog only when its curriculum is
   approved. So the e-mail cannot be written, or shown, before message 2.
3. **Nobody approves text they have not read.** Message 2 approves the curriculum you saw in message 1.
   Message 3 approves the e-mail you saw in message 2.

## Design

```
Chat ──▶ OrchestratorAgent ──("launch … course" or an LP-id)──▶ CourseLaunchAgent      (chat commands, replies)
                                                                     │
                                                                     ▼
                                                           CourseLaunchWorkflow       (order of steps, resume)
                     ┌───────────────┬──────────────┬─────────────────┼──────────────────┬──────────────────┐
                     ▼               ▼              ▼                 ▼                  ▼                  ▼
            CurriculumService  CourseRepository  OfferService   MarketingAgent    ApprovalService   DistributionService
            (create, submit,   (set_fee)         (propose,      (create_campaign) (approve draft)   (the ONLY sender:
             approve→publish)                     approve)                                         checks, dry run, log)
```

| File | Responsibility |
|---|---|
| `src/workflows/launch_plan.py` | Parse one sentence into a `LaunchPlan` (regex, no LLM). Lists what is missing. Creates nothing. |
| `src/workflows/course_launch.py` | `CourseLaunchWorkflow`: calls the existing services in the safe order and records completed steps. |
| `src/agents/course_launch_agent.py` | `CourseLaunchAgent`: understands `approve / send / cancel` and writes the replies (EN/AR). |
| `src/db/repositories.py` | New `CourseRepository.set_fee()`: stores the approved fee (curricula do not contain fees). |
| `src/agents/orchestrator.py`, `src/bootstrap.py` | Staff-only routing (`launch`, `LP-…`, `/launch`) and registration. |

The workflow owns **no business rules**. Who may approve, fact-checking against today's data, dry-run
mode, duplicate protection and delivery logs all stay in the existing services, so the chat and the
admin pages behave identically.

### Safety properties (each covered by `tests/test_course_launch.py`)

- Students cannot start a launch.
- Nothing is live after message 1.
- Approving needs a name and the exact plan ID from this conversation.
- E-mail cannot be sent before approval, and it is re-checked against today's data before sending.
- **One e-mail per recipient**: `EmailService` puts all addresses in `To:`, so a group e-mail would show
  every lead's address to everyone. Each person also gets their own delivery log entry.
- Only consented leads are e-mailed (a lead row exists only after consent). Leads marked `closed` are skipped.
- A failed step stops the workflow. The steps before it stay done, and repeating the command resumes
  without creating a second course or offer.
- Sending twice does not send twice.
- `MARKETING_DELIVERY_MODE=dry_run` (the default) runs every check and logs, but sends nothing.
  Set `live` and the `SMTP_*` variables in `.env` to really send.

## Known limitations

- **Approver names are typed, not verified.** Staff share one admin password, so `as Nadin` is
  self-declared. Proper per-user login would fix this.
- **The plan lives in the chat state.** If the browser session ends between messages 1 and 2, the
  curriculum stays in review on the Curriculum page and can be approved there. The launch plan itself
  is not saved in the database.
- **Curricula from a title and topics are template-based without an LLM.** Review the modules before
  approving, or edit them on the Curriculum page first.
- **Parsing is rule-based.** Unusual phrasing may need the missing details given in a follow-up message.
- **The e-mail is one variation in the sentence's language.** Use the Marketing page for more variations
  or other channels.

## Testing

```bash
pytest tests/test_launch_plan.py tests/test_course_launch.py -q
```

Manual check: log in as staff, send the example sentence, approve, read the e-mail, send. Then switch
to student mode and ask *"Do you have a cloud computing course? how much is it?"*. REX should answer
with the new course, its fee and the offer.
