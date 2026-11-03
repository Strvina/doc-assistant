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

### Setup

1. Run n8n, e.g. `docker run -it --rm -p 5678:5678 docker.n8n.io/n8nio/n8n`, open <http://localhost:5678>.
2. *Workflows → Import from File* → `automations/escalation_workflow.json`.
3. Open **Notify HR by e-mail**: add SMTP credentials, set the From/To addresses.
4. Open **Escalation webhook** and copy the *Test URL* (or the *Production URL* after activating the workflow).
5. In `.env`: `ESCALATION_WEBHOOK_URL=<that URL>`.
6. Try it: `uv run python -m docassist.cli ask "Ko odobrava bonus? Prosledi pitanje HR-u." --agent`

> If Doc Assistant runs in Docker and n8n on the host, use `http://host.docker.internal:5678/...`.

## Calling Doc Assistant from n8n

Any workflow can ask questions through the REST API (`uv run uvicorn docassist.api:app`):
use an **HTTP Request** node with `POST http://<host>:8000/ask`, JSON body
`{"question": "...", "agent": false}` and, if `API_TOKEN` is set, the header `X-API-Key`.
The response contains `answer`, `found` and `citations` (source, page, text) — e.g. to answer
questions arriving by e-mail and forward only the ones with `found: false` to a person.
