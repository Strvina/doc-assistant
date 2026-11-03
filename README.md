# Doc Assistant

An internal AI assistant that answers employees' questions **only from documents the company uploads**
(PDF, DOCX, TXT) — with a source citation (document + page) for every claim, and an honest
"not in the documents" when the answer isn't there.

> 🚧 Work in progress — see [`docs/PLAN.md`](docs/PLAN.md) for the roadmap.

## Planned features

- Upload company documents in Serbian, German or English; ask questions in any of them
- Answers with citations, grounded strictly in uploaded documents
- Switchable LLM provider: **Claude** or **ChatGPT**
- Agent with tools: document summary, escalation to a human, leave-request drafting
- Evaluation suite and usage monitoring (accuracy, latency, cost, user feedback)
- REST API + n8n automation example

## Tech stack

Python 3.12 · uv · Anthropic & OpenAI APIs · ChromaDB · Streamlit · FastAPI · n8n · GitHub Actions

## Quick start

```bash
uv sync
cp .env.example .env    # add your API keys
uv run pytest
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Design decisions](docs/DECISIONS.md)
- [Development workflow](docs/WORKFLOW.md)
