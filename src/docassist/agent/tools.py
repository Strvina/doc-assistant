"""Tools the agent can call, with JSON schemas shared by all providers (ADR-008)."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from docassist.llm.prompts import format_context
from docassist.models import Chunk
from docassist.retrieval import SearchResult, VectorStore, retrieve

MAX_DOCUMENT_CHARS = 30_000  # keeps one document read well inside the context window


class ToolError(Exception):
    """Expected tool failure; the message is shown to the model so it can recover."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]


@dataclass
class ToolContext:
    """What the tools need, plus what they collected during one agent run."""

    store: VectorStore
    escalator: Escalator
    leave_requests: LeaveRequestSink
    top_k: int = 5
    retrieved: list[SearchResult] = field(default_factory=list)


# --- side-effect sinks (real + recording implementations) --------------------------------


class Escalator(Protocol):
    def escalate(self, payload: dict[str, Any]) -> str:
        """Hand the question to a person; return a reference shown to the user."""
        ...


class LeaveRequestSink(Protocol):
    def save(self, request: dict[str, Any]) -> str:
        """Store a leave request draft; return its reference."""
        ...


class WebhookEscalator:
    """POSTs the escalation as JSON to a webhook (e.g. the n8n workflow in automations/)."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout

    def escalate(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status = response.status
        except OSError as exc:
            raise ToolError(f"Escalation webhook is unreachable: {exc}") from exc
        if status >= 300:
            raise ToolError(f"Escalation webhook answered with HTTP {status}")
        return payload["id"]


class JsonlFile:
    """Appends records to a local JSON Lines file (used when no webhook is configured)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _append(self, record: dict[str, Any]) -> str:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record["id"]

    def escalate(self, payload: dict[str, Any]) -> str:
        return self._append(payload)

    def save(self, request: dict[str, Any]) -> str:
        return self._append(request)


class Recorder:
    """In-memory sink for tests."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def escalate(self, payload: dict[str, Any]) -> str:
        self.records.append(payload)
        return payload["id"]

    def save(self, request: dict[str, Any]) -> str:
        self.records.append(request)
        return request["id"]


# --- tools -------------------------------------------------------------------------------


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    # Strict schemas: every property required, nothing extra (works for Claude and OpenAI).
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def build_tools(ctx: ToolContext) -> list[Tool]:
    def search_documents(args: dict[str, Any]) -> str:
        results = retrieve(ctx.store, args["query"], k=ctx.top_k)
        if not results:
            return "No matching passages found."
        ctx.retrieved.extend(results)
        return format_context([r.chunk for r in results])

    def list_documents(args: dict[str, Any]) -> str:
        docs = ctx.store.documents()
        if not docs:
            return "No documents are indexed."
        return "\n".join(f"- {d.source} ({d.pages} pages)" for d in docs)

    def summarize_document(args: dict[str, Any]) -> str:
        source = args["source"]
        chunks = ctx.store.document_chunks(source)
        if not chunks:
            names = ", ".join(d.source for d in ctx.store.documents()) or "none"
            raise ToolError(f"No document named {source!r}. Available: {names}")
        ctx.retrieved.extend(SearchResult(c, 1.0) for c in chunks)
        return _document_text(chunks)

    def escalate_to_human(args: dict[str, Any]) -> str:
        payload = {
            "id": _new_id("ESC"),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "question": args["question"],
            "reason": args["reason"],
            "contact": args["contact"],
        }
        reference = ctx.escalator.escalate(payload)
        return f"Escalated to a person, reference {reference}."

    def create_leave_request(args: dict[str, Any]) -> str:
        start = _parse_date(args["start_date"], "start_date")
        end = _parse_date(args["end_date"], "end_date")
        if end < start:
            raise ToolError("end_date is before start_date")
        request = {
            "id": _new_id("GO"),
            "status": "draft",
            "employee_name": args["employee_name"],
            "leave_type": args["leave_type"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "working_days": working_days(start, end),
            "note": args["note"],
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        ctx.leave_requests.save(request)
        return json.dumps(request, ensure_ascii=False)

    return [
        Tool(
            "search_documents",
            "Search the company documents (Serbian, German, English) and return the most "
            "relevant passages with file name and page. Use it before answering any question "
            "about company rules, procedures or products. Write the query in the language of "
            "the documents you expect, or try several languages.",
            _schema({"query": {"type": "string", "description": "What to look for."}}),
            search_documents,
        ),
        Tool(
            "list_documents",
            "List the names of all indexed documents.",
            _schema({}),
            list_documents,
        ),
        Tool(
            "summarize_document",
            "Return the full text of one document (by exact file name from list_documents) "
            "so you can summarize it or answer questions about the whole document.",
            _schema({"source": {"type": "string", "description": "File name, e.g. faq.txt"}}),
            summarize_document,
        ),
        Tool(
            "escalate_to_human",
            "Send the user's question to the responsible person when the documents do not "
            "answer it. Only call this after the user agreed to escalate.",
            _schema(
                {
                    "question": {"type": "string", "description": "The user's question."},
                    "reason": {"type": "string", "description": "Why it is escalated."},
                    "contact": {
                        "type": "string",
                        "description": "How to reach the user (name/e-mail), or 'unknown'.",
                    },
                }
            ),
            escalate_to_human,
        ),
        Tool(
            "create_leave_request",
            "Create a draft leave request after the user confirmed all fields. "
            "Dates are ISO (YYYY-MM-DD). Returns the draft as JSON.",
            _schema(
                {
                    "employee_name": {"type": "string"},
                    "leave_type": {
                        "type": "string",
                        "enum": ["annual", "paid_absence", "unpaid"],
                    },
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD (inclusive)"},
                    "note": {"type": "string", "description": "Optional note, or empty."},
                }
            ),
            create_leave_request,
        ),
    ]


def execute_tool(tools: list[Tool], name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool and return (output, is_error). Never raises: errors go back to the model."""
    by_name = {tool.name.lower(): tool for tool in tools}
    tool = by_name.get(name.lower())  # tolerate a different letter case in the name
    if tool is None:
        return f"Unknown tool {name!r}. Available: {', '.join(t.name for t in tools)}", True
    missing = [key for key in tool.input_schema["required"] if key not in args]
    if missing:
        return f"Missing arguments for {tool.name}: {', '.join(missing)}", True
    try:
        return tool.handler(args), False
    except ToolError as exc:
        return f"Error: {exc}", True


def working_days(start: date, end: date) -> int:
    """Monday–Friday days between start and end, inclusive (public holidays not excluded)."""
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"{field_name} must be YYYY-MM-DD, got {value!r}") from exc


def _document_text(chunks: list[Chunk]) -> str:
    parts: list[str] = []
    page = None
    length = 0
    for chunk in chunks:
        header = f"\n--- {chunk.source}, page {chunk.page} ---\n" if chunk.page != page else "\n"
        page = chunk.page
        piece = header + chunk.text
        if length + len(piece) > MAX_DOCUMENT_CHARS:
            parts.append("\n[... document truncated ...]")
            break
        parts.append(piece)
        length += len(piece)
    return "".join(parts).strip()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC):%Y%m%d%H%M%S%f}"
