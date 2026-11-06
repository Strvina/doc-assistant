# Automations (n8n)

## `escalation_workflow.json` — question escalated to a person

When the documents don't answer a question and the user agrees, the agent calls the
`escalate_to_human` tool. With `ESCALATION_WEBHOOK_URL` set, Doc Assistant POSTs this JSON:

```json
{
  "id": "ESC-20261104093012123456",
  "created_at": "2026-11-04T09:30:12+00:00",
  "question": "Ko odobrava bonus za prekovremeni rad?",
  "reason": "The documents do not mention bonus approval.",
  "contact": "Ana Jovanović, ana@example.com"
}
```

The workflow receives it with a **Webhook** node and sends an e-mail to HR
(**Send Email** node). Swap the e-mail node for Slack, Teams or a ticketing system as needed.
Without a webhook URL, escalations are appended to `data/outbox/escalations.jsonl` instead.

The payload names the team (`team`, `team_label`, `team_email`). Teams come from
`ESCALATION_TEAMS` in `.env` (`id:Label:e-mail`, comma-separated); the web UI shows one
"forward" button per team under unanswered questions, and the agent picks the team itself.
The workflow sends the e-mail to `team_email` (falling back to HR).

### Setup (one command, everything local and free)

```bash
uv run python scripts/setup_n8n.py
```

It starts **n8n** and **Mailpit** (a local test inbox) with Docker, imports the SMTP credentials
(`n8n_credentials_mailpit.json`, pointing to Mailpit) and the workflow, activates it and sends one
test escalation. Then:

- n8n: <http://localhost:5678> (the first visit asks you to create the owner account)
- Mailpit inbox: <http://localhost:8025> — the test e-mail should already be there
- add to `.env`: `ESCALATION_WEBHOOK_URL=http://localhost:5678/webhook/doc-assistant-escalation`

To send real e-mail, edit the *Mailpit* credential in n8n (or create a new SMTP credential) with
your mail server. If Doc Assistant itself runs in Docker Compose, use
`http://n8n:5678/webhook/doc-assistant-escalation` as the webhook URL.

## `gap_report_workflow.json` — weekly report of the gaps in the documentation

Every Monday at 8:00 (or with the *Run now* node) the workflow fetches
`GET /reports/gaps?days=7&classify=true` from Doc Assistant and sends **one e-mail per team**:
the questions of the last week that the documents could not answer, or whose answer was rated 👎.
The same question asked several times is listed once with a count; a follow-up such as
"a u petu?" is shown in the standalone form the search used.

Which team gets a question: the team it was already forwarded to; otherwise, with `classify=true`,
the team the AI model picks from the team names (`ESCALATION_TEAMS`); otherwise the first team.
The app writes the subject, text and HTML of each e-mail, so the workflow only splits the list
and sends. Preview the same report in the browser: `/reports/gaps?format=html` (link in the
*Praćenje* panel).

The workflow reaches the app at `DOC_ASSISTANT_URL` (default `http://host.docker.internal:8000`,
the app on this PC; `http://app:8000` when the app runs in compose) and sends `API_TOKEN` as
`X-API-Key` if it is set. `setup_n8n.py` imports and activates it; send one report right away with
`uv run python scripts/setup_n8n.py --send-gap-report`.

## Feeding the inbox folder

Doc Assistant imports every supported file that appears in the watched folder (chosen in the
web UI under *Automatski uvoz iz foldera*, default `INBOX_DIR` = `data/inbox`; checked every
`INBOX_POLL_S` = 10 s; ADR-036, ADR-037). Any workflow can feed it: e.g. an **Email Trigger
(IMAP)** node for a shared mailbox like `dokumenti@firma.rs` → **Write Binary File** of each
attachment into the folder (mounted into the n8n container). A scanner that saves to that folder
works the same way, without n8n. Imported files move to the subfolder `Uvezeno`; unreadable ones
to `Nije uspelo` with a `.txt` that says why (retry or remove them in the panel). `GET /inbox`
returns the status and the latest imports.

## Calling Doc Assistant from n8n

Any workflow can ask questions through the REST API (`uv run uvicorn docassist.api:app`):
use an **HTTP Request** node with `POST http://<host>:8000/ask`, JSON body
`{"question": "...", "agent": false}` and, if `API_TOKEN` is set, the header `X-API-Key`.
The response contains `answer`, `found` and `citations` (source, page, text) — e.g. to answer
questions arriving by e-mail and forward only the ones with `found: false` to a person.
