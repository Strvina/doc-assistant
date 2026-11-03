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

## Command line

```bash
uv run python -m docassist.cli index data/sample_docs        # load, chunk, embed, store
uv run python -m docassist.cli search "Koliko traje godišnji odmor?"   # chunks only
uv run python -m docassist.cli ask "Koliko traje godišnji odmor?"      # answer + citations
uv run python -m docassist.cli ask "Koliki je moment za M8?" --provider openai
uv run python -m docassist.cli docs                          # list indexed documents
uv run python -m docassist.cli delete <doc_id>
```

No API key yet? Set `EMBEDDING_PROVIDER=fake` and `LLM_PROVIDER=fake` in `.env` to try the
whole flow offline (keyword-like matching and canned answers; switching embeddings back to
OpenAI needs `index --rebuild`).

## Demo documents

[`data/sample_docs/`](data/sample_docs/) contains documents of a **fictional** switchgear
manufacturer, Panelvolt d.o.o. (all people, phone numbers and `*.example` addresses are made up):

| File | Language | Content |
|---|---|---|
| `pravilnik_o_radu.pdf` | Serbian | Employee handbook: working hours, leave, sick leave, remote work |
| `montageanleitung_pv400.pdf` | German | Installation manual for the PV-400 cabinet system |
| `quality_control_procedure.docx` | English | Quality control procedure and routine tests |
| `bezbednost_na_radu.docx` | Serbian | Occupational health and safety rules |
| `faq.txt` | Serbian | Employee FAQ |

They are generated from the text sources in `scripts/sample_docs_src/`:

```bash
uv run python scripts/build_sample_docs.py
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Design decisions](docs/DECISIONS.md)
- [Development workflow](docs/WORKFLOW.md)
