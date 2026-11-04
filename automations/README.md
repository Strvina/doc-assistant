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

## Calling Doc Assistant from n8n

Any workflow can ask questions through the REST API (`uv run uvicorn docassist.api:app`):
use an **HTTP Request** node with `POST http://<host>:8000/ask`, JSON body
`{"question": "...", "agent": false}` and, if `API_TOKEN` is set, the header `X-API-Key`.
The response contains `answer`, `found` and `citations` (source, page, text) — e.g. to answer
questions arriving by e-mail and forward only the ones with `found: false` to a person.
