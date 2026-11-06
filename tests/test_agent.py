import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from docassist.agent import (
    AGENT_SYSTEM_PROMPT,
    JsonlFile,
    Recorder,
    ToolContext,
    build_tools,
    execute_tool,
    run_agent,
)
from docassist.agent.tools import working_days
from docassist.config import Team
from docassist.ingest import ingest
from docassist.llm import FakeProvider
from docassist.llm.claude import ClaudeProvider
from docassist.llm.openai_provider import OpenAIProvider
from docassist.retrieval import FakeEmbedder, VectorStore

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"
TODAY = date(2026, 11, 4)


@pytest.fixture
def store(tmp_path):
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    for path in sorted(SAMPLE_DOCS.iterdir()):
        store.add_document(ingest(path))
    return store


@pytest.fixture
def ctx(store):
    return ToolContext(store=store, escalator=Recorder(), leave_requests=Recorder(), top_k=3)


def tools_by_name(ctx):
    return {tool.name: tool for tool in build_tools(ctx)}


# --- tools -------------------------------------------------------------------------------


def test_tool_schemas_are_strict(ctx):
    for tool in build_tools(ctx):
        schema = tool.input_schema
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


def test_search_documents_returns_context_and_records_results(ctx):
    output, is_error = execute_tool(build_tools(ctx), "search_documents", {"query": "kantina radi"})

    assert not is_error
    assert '<document index="1" source="faq.txt" page="1">' in output
    hits = [r for r in ctx.retrieved if not r.neighbor]
    assert len(hits) in (3, 4)  # top_k by meaning + at most one keyword hit


def test_list_and_summarize_document(ctx):
    tools = build_tools(ctx)

    listing, _ = execute_tool(tools, "list_documents", {})
    text, is_error = execute_tool(tools, "summarize_document", {"source": "faq.txt"})

    assert "- faq.txt (1 pages)" in listing
    assert not is_error
    assert text.startswith("--- faq.txt, page 1 ---")
    assert "Kantina radi od 10:30 do 13:30" in text


def test_summarize_unknown_document_lists_alternatives(ctx):
    output, is_error = execute_tool(build_tools(ctx), "summarize_document", {"source": "x.pdf"})

    assert is_error
    assert "faq.txt" in output


def test_escalation_goes_to_the_escalator(ctx):
    output, is_error = execute_tool(
        build_tools(ctx),
        "escalate_to_human",
        {
            "team": "hr",
            "question": "Ko odobrava bonus?",
            "reason": "not in documents",
            "contact": "Ana",
        },
    )

    assert not is_error
    [record] = ctx.escalator.records
    assert record["question"] == "Ko odobrava bonus?"
    assert (record["team"], record["team_label"]) == ("hr", "HR")
    assert record["id"] in output


def test_escalation_to_a_configured_team(ctx):
    ctx.teams = (Team("hr", "HR"), Team("maintenance", "Održavanje", "odrz@firma.rs"))
    tools = build_tools(ctx)
    schema = next(t for t in tools if t.name == "escalate_to_human").input_schema
    assert schema["properties"]["team"]["enum"] == ["hr", "maintenance"]

    args = {"team": "Maintenance", "question": "q", "reason": "r", "contact": "c"}
    output, is_error = execute_tool(tools, "escalate_to_human", args)
    assert not is_error
    assert ctx.escalator.records[0]["team_email"] == "odrz@firma.rs"

    output, is_error = execute_tool(tools, "escalate_to_human", {**args, "team": "sales"})
    assert is_error and "Unknown team" in output


def test_leave_request_counts_working_days(ctx):
    output, is_error = execute_tool(
        build_tools(ctx),
        "create_leave_request",
        {
            "employee_name": "Petar Petrović",
            "leave_type": "annual",
            "start_date": "2026-12-07",
            "end_date": "2026-12-18",
            "note": "",
        },
    )

    assert not is_error
    request = json.loads(output)
    assert request["working_days"] == 10
    assert request["status"] == "draft"
    assert ctx.leave_requests.records == [request]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"start_date": "07.12.2026", "end_date": "2026-12-18"}, "YYYY-MM-DD"),
        ({"start_date": "2026-12-18", "end_date": "2026-12-07"}, "before"),
    ],
)
def test_leave_request_validation(ctx, args, message):
    full = {"employee_name": "P", "leave_type": "annual", "note": "", **args}

    output, is_error = execute_tool(build_tools(ctx), "create_leave_request", full)

    assert is_error and message in output
    assert ctx.leave_requests.records == []


def test_execute_tool_tolerates_case_and_reports_problems(ctx):
    tools = build_tools(ctx)

    assert execute_tool(tools, "List_Documents", {})[1] is False
    assert execute_tool(tools, "delete_everything", {}) == (
        "Unknown tool 'delete_everything'. Available: search_documents, list_documents, "
        "summarize_document, escalate_to_human, create_leave_request",
        True,
    )
    output, is_error = execute_tool(tools, "search_documents", {})
    assert is_error and "query" in output


def test_working_days_skips_weekends():
    assert working_days(date(2026, 11, 6), date(2026, 11, 9)) == 2  # Fri..Mon
    assert working_days(date(2026, 11, 7), date(2026, 11, 8)) == 0  # weekend


def test_jsonl_file_appends(tmp_path):
    sink = JsonlFile(tmp_path / "out" / "x.jsonl")

    sink.save({"id": "A", "x": "č"})
    sink.escalate({"id": "B"})

    lines = (tmp_path / "out" / "x.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["A", "B"]


# --- run_agent with FakeProvider ---------------------------------------------------------


def test_agent_searches_and_cites(ctx):
    llm = FakeProvider()

    result = run_agent("Koliko dana traje godišnji odmor?", ctx, llm, today=TODAY)

    assert [c.name for c in result.tool_calls] == ["search_documents"]
    assert result.found
    assert result.citations[0].source == "pravilnik_o_radu.pdf"
    assert result.retrieved


def test_agent_passes_date_and_history(ctx):
    llm = FakeProvider(tool_script=[])

    run_agent("Od ponedeljka?", ctx, llm, history=[("P1", "O1")], today=TODAY)

    # FakeProvider.run_agent answers through complete(); the run itself got the messages.
    assert llm.calls  # answered without tools


def test_agent_scripted_leave_request(ctx):
    script = [
        ("search_documents", {"query": "godišnji odmor zahtev"}),
        (
            "create_leave_request",
            {
                "employee_name": "Ana",
                "leave_type": "annual",
                "start_date": "2026-12-21",
                "end_date": "2026-12-24",
                "note": "",
            },
        ),
    ]

    result = run_agent("Hoću odmor 21-24.12.", ctx, FakeProvider(tool_script=script), today=TODAY)

    assert [c.name for c in result.tool_calls] == ["search_documents", "create_leave_request"]
    assert ctx.leave_requests.records[0]["working_days"] == 4


# --- Claude loop (fake client) -----------------------------------------------------------


def block(**fields):
    return SimpleNamespace(**fields)


def claude_response(stop_reason, content, tokens=(100, 10)):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class ScriptedMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **request):
        # Copy the list: the loop appends to the same conversation object afterwards.
        self.requests.append({**request, "messages": list(request["messages"])})
        return self._responses.pop(0)


def test_claude_agent_loop(ctx):
    tool_turn = [
        block(type="thinking", thinking=""),
        block(type="tool_use", id="tu_1", name="search_documents", input={"query": "kantina"}),
        block(type="tool_use", id="tu_2", name="nope", input={}),
    ]
    final = [block(type="text", text="Kantina radi do 13:30 [faq.txt, str. 1].")]
    messages = ScriptedMessages(
        [claude_response("tool_use", tool_turn), claude_response("end_turn", final)]
    )
    client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
    llm = ClaudeProvider("claude-sonnet-5-5", client=client)

    result = run_agent("Kada radi kantina?", ctx, llm, today=TODAY)

    first, second = messages.requests
    assert first["system"] == AGENT_SYSTEM_PROMPT
    assert {t["name"] for t in first["tools"]} >= {"search_documents", "escalate_to_human"}
    assert all(t["strict"] is True for t in first["tools"])
    assert "tool_choice" not in first
    # Append-only: assistant turn exactly as returned, then one user turn with both results.
    assert second["messages"][-2] == {"role": "assistant", "content": tool_turn}
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu_1", "tu_2"]
    assert results[1]["is_error"] is True and "is_error" not in results[0]

    assert result.text == "Kantina radi do 13:30 [faq.txt, str. 1]."
    assert result.found
    assert (result.input_tokens, result.output_tokens) == (200, 20)
    assert [c.is_error for c in result.tool_calls] == [False, True]


def test_claude_agent_stops_after_max_steps(ctx):
    tool_turn = [block(type="tool_use", id="t", name="list_documents", input={})]
    messages = ScriptedMessages([claude_response("tool_use", tool_turn) for _ in range(3)])
    client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))

    result = run_agent(
        "?", ctx, ClaudeProvider("claude-sonnet-5-5", client=client), max_steps=3, today=TODAY
    )

    assert result.stop_reason == "max_steps"
    assert len(result.tool_calls) == 3


# --- OpenAI loop (fake client) -----------------------------------------------------------


class ScriptedResponses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **request):
        self.requests.append({**request, "input": list(request["input"])})
        return self._responses.pop(0)


def openai_response(output, text=""):
    return SimpleNamespace(
        output=output,
        output_text=text,
        model="gpt-test",
        status="completed",
        incomplete_details=None,
        usage=SimpleNamespace(input_tokens=50, output_tokens=5),
    )


def test_openai_agent_loop(ctx):
    call = block(
        type="function_call", call_id="c1", name="search_documents", arguments='{"query":"M8"}'
    )
    bad = block(type="function_call", call_id="c2", name="list_documents", arguments="not json")
    reasoning = block(type="reasoning", id="r1")
    responses = ScriptedResponses(
        [
            openai_response([reasoning, call, bad]),
            openai_response(
                [block(type="message")], text="20 Nm [montageanleitung_pv400.pdf, str. 2]"
            ),
        ]
    )
    llm = OpenAIProvider("gpt-test", client=SimpleNamespace(responses=responses))

    result = run_agent("Moment za M8?", ctx, llm, today=TODAY)

    first, second = responses.requests
    assert first["tools"][0]["type"] == "function"
    assert first["tools"][0]["strict"] is True
    assert first["include"] == ["reasoning.encrypted_content"]
    assert first["store"] is False
    # Output items (incl. reasoning) go back, followed by one output per call.
    assert second["input"][-5:-2] == [reasoning, call, bad]
    outputs = [i for i in second["input"] if isinstance(i, dict) and i.get("type")]
    assert [o["call_id"] for o in outputs] == ["c1", "c2"]
    assert "not a valid JSON" in outputs[1]["output"]
    assert result.text == "20 Nm [montageanleitung_pv400.pdf, str. 2]"
    assert [c.is_error for c in result.tool_calls] == [False, True]


# --- webhook escalation + n8n workflow ---------------------------------------------------


def test_webhook_escalator_posts_json():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from docassist.agent import WebhookEscalator

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        reference = WebhookEscalator(url).escalate({"id": "ESC-1", "question": "Šta?"})
    finally:
        thread.join(timeout=5)
        server.server_close()

    assert reference == "ESC-1"
    assert received == [{"id": "ESC-1", "question": "Šta?"}]


def test_webhook_escalator_unreachable_is_a_tool_error(ctx):
    from docassist.agent import WebhookEscalator

    ctx.escalator = WebhookEscalator("http://127.0.0.1:9/nothing", timeout=1)
    output, is_error = execute_tool(
        build_tools(ctx),
        "escalate_to_human",
        {"team": "hr", "question": "q", "reason": "r", "contact": "c"},
    )

    assert is_error and "unreachable" in output


def test_n8n_workflow_matches_escalation_payload(ctx):
    import re

    workflow = json.loads(
        (
            Path(__file__).resolve().parent.parent / "automations" / "escalation_workflow.json"
        ).read_text(encoding="utf-8")
    )
    execute_tool(
        build_tools(ctx),
        "escalate_to_human",
        {"team": "hr", "question": "q", "reason": "r", "contact": "c"},
    )
    payload_fields = set(ctx.escalator.records[0])

    email = next(n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.emailSend")
    params = email["parameters"]
    template = params["toEmail"] + params["subject"] + params["text"]
    used = set(re.findall(r"\$json\.body\.(\w+)", template))
    assert used and used <= payload_fields
    assert set(workflow["connections"]) == {"Escalation webhook"}


def test_tools_see_only_the_chosen_documents(ctx):
    faq = next(d.doc_id for d in ctx.store.documents() if d.source == "faq.txt")
    ctx.doc_ids = frozenset({faq})
    tools = build_tools(ctx)

    found, _ = execute_tool(tools, "search_documents", {"query": "Anzugsmoment M8"})
    listing, _ = execute_tool(tools, "list_documents", {})

    assert {r.chunk.source for r in ctx.retrieved} == {"faq.txt"}
    assert 'source="faq.txt"' in found
    assert listing == "- faq.txt (1 pages)"
