"""ChatCompletionsProvider (Gemini / Groq free tiers) with fake clients — no network."""

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx2
import openai
import pytest

from docassist.agent.tools import Tool
from docassist.config import Settings
from docassist.factory import MissingAPIKeyError, build_llm
from docassist.llm import LLMError
from docassist.llm.chat_completions import ChatCompletionsProvider, Endpoint

MESSAGES = [{"role": "user", "content": "Pitanje?"}]


class Message(SimpleNamespace):
    def model_dump(self, exclude_none=False):
        data = {"role": "assistant", "content": self.content, "tool_calls": self.raw_calls}
        return {k: v for k, v in data.items() if v is not None or not exclude_none}


def response(content=None, tool_calls=None, finish="stop", model="m-1"):
    calls = [
        SimpleNamespace(
            id=c["id"], function=SimpleNamespace(name=c["name"], arguments=c["arguments"])
        )
        for c in tool_calls or []
    ]
    message = Message(content=content, tool_calls=calls or None, raw_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        model=model,
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
    )


def status_error(code, body=None, headers=None):
    request = httpx2.Request("POST", "https://example.test")
    resp = httpx2.Response(code, request=request, headers=headers or {})
    if code == 429:
        return openai.RateLimitError("quota", response=resp, body=body)
    return openai.APIStatusError("error", response=resp, body=body)


class FakeService:
    """Answers per model from a script of responses/exceptions, consumed in order."""

    def __init__(self, **script):
        self.script = {model: list(items) for model, items in script.items()}
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        item = self.script[request["model"]].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(service):
    return SimpleNamespace(chat=SimpleNamespace(completions=service))


def provider(*endpoints, paused=None):
    paused = {} if paused is None else paused
    return ChatCompletionsProvider("groq", list(endpoints), paused_until=paused)


def search_tool(seen):
    return Tool(
        name="search_documents",
        description="Search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=lambda args: seen.append(args) or "found: 20 Nm",
    )


def test_complete_sends_system_message_and_cleans_text():
    text = "<think>hmm</think>20 Nm 【manual.pdf, str. 2】 Panelvolt‑Staff"
    service = FakeService(**{"m-1": [response(text)]})

    result = provider(Endpoint("groq", "m-1", client(service), 2048)).complete("SYSTEM", MESSAGES)

    request = service.calls[0]
    assert request["model"] == "m-1"
    assert request["max_tokens"] == 2048
    assert request["messages"] == [{"role": "system", "content": "SYSTEM"}, *MESSAGES]
    assert result.text == "20 Nm [manual.pdf, str. 2] Panelvolt-Staff"
    assert (result.input_tokens, result.output_tokens) == (100, 10)
    assert result.stop_reason == "end"


@pytest.mark.parametrize(
    ("finish", "expected"), [("length", "max_tokens"), ("content_filter", "refusal")]
)
def test_stop_reasons(finish, expected):
    service = FakeService(**{"m-1": [response("x", finish=finish)]})
    result = provider(Endpoint("groq", "m-1", client(service))).complete("s", MESSAGES)
    assert result.stop_reason == expected


def test_agent_loop_returns_tool_results_and_keeps_gemini_signatures():
    seen = []
    calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "search_documents", "arguments": '{"query": "M8"}'},
            "extra_content": {"google": {"thought_signature": "sig"}},
            "name": "search_documents",
            "arguments": json.dumps({"query": "M8"}),
        },
        {"id": "c2", "name": "search_documents", "arguments": "not json"},
    ]
    service = FakeService(
        **{"m-1": [response(tool_calls=calls, finish="tool_calls"), response("20 Nm")]}
    )

    run = provider(Endpoint("gemini", "m-1", client(service))).run_agent(
        "SYSTEM", MESSAGES, [search_tool(seen)]
    )

    first, second = service.calls
    assert first["tools"][0]["function"]["name"] == "search_documents"
    assistant, *tool_msgs = second["messages"][-3:]
    assert assistant["tool_calls"][0]["extra_content"]  # Gemini gets its signatures back
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert tool_msgs[0]["content"] == "found: 20 Nm"
    assert "not a valid JSON" in tool_msgs[1]["content"]
    assert seen == [{"query": "M8"}]
    assert run.text == "20 Nm"
    assert [c.is_error for c in run.tool_calls] == [False, True]
    assert (run.steps, run.input_tokens) == (2, 200)


def test_non_gemini_services_get_only_standard_message_fields():
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "search_documents", "arguments": '{"query": "M8"}'},
        "extra_content": {"google": {}},
        "name": "search_documents",
        "arguments": '{"query": "M8"}',
    }
    gemini = FakeService(
        **{"g": [response(tool_calls=[call], finish="tool_calls"), status_error(503)]}
    )
    groq = FakeService(**{"q": [response("done")]})
    llm = provider(Endpoint("gemini", "g", client(gemini)), Endpoint("groq", "q", client(groq)))

    run = llm.run_agent("s", MESSAGES, [search_tool([])])

    assistant = groq.calls[0]["messages"][-2]
    assert assistant["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "search_documents", "arguments": '{"query": "M8"}'},
        }
    ]
    assert run.text == "done"


def test_quota_pauses_an_endpoint_and_the_next_one_answers():
    body = [{"error": {"details": [{"retryDelay": "67913s"}]}}]
    gemini = FakeService(**{"g": [status_error(429, body)]})
    groq = FakeService(**{"q": [response("a", model="q"), response("b", model="q")]})
    paused = {}
    llm = provider(
        Endpoint("gemini", "g", client(gemini)), Endpoint("groq", "q", client(groq)), paused=paused
    )

    assert llm.complete("s", MESSAGES).model == "q"
    assert llm.complete("s", MESSAGES).text == "b"
    assert len(gemini.calls) == 1  # skipped the second time
    assert paused["gemini/g"] - time.monotonic() > 67000


def test_long_retry_after_pauses_the_endpoint():
    groq = FakeService(q=[status_error(429, headers={"retry-after": "60"})], r=[response("ok")])
    paused = {}
    llm = provider(
        Endpoint("groq", "q", client(groq)), Endpoint("groq", "r", client(groq)), paused=paused
    )

    assert llm.complete("s", MESSAGES).text == "ok"
    assert 50 < paused["groq/q"] - time.monotonic() <= 60


def test_short_per_minute_limit_is_waited_out_on_the_same_endpoint(monkeypatch):
    waits = []
    monkeypatch.setattr(time, "sleep", waits.append)
    groq = FakeService(
        q=[status_error(429, headers={"retry-after": "8"}), response("ok")], r=[response("no")]
    )
    llm = provider(Endpoint("groq", "q", client(groq)), Endpoint("groq", "r", client(groq)))

    assert llm.complete("s", MESSAGES).text == "ok"
    assert waits == [8.0]
    assert [c["model"] for c in groq.calls] == ["q", "q"]


def test_gemini_gets_a_placeholder_signature_for_calls_from_another_model():
    call = {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
    groq_turn = {"role": "assistant", "tool_calls": [call], "reasoning": "hidden"}
    gemini = FakeService(g=[response("ok")])
    llm = provider(Endpoint("gemini", "g", client(gemini)))

    llm._create([*MESSAGES, groq_turn, {"role": "tool", "tool_call_id": "c1", "content": "x"}])

    sent = gemini.calls[0]["messages"][1]
    assert "reasoning" not in sent
    assert sent["tool_calls"][0]["extra_content"]["google"]["thought_signature"]


def test_too_large_request_tries_the_next_endpoint_without_pausing():
    groq = FakeService(q=[status_error(413)], r=[response("ok")])
    paused = {}
    llm = provider(
        Endpoint("groq", "q", client(groq)), Endpoint("groq", "r", client(groq)), paused=paused
    )

    assert llm.complete("s", MESSAGES).text == "ok"
    assert paused == {}


def test_other_errors_are_raised_without_fallback():
    groq = FakeService(q=[status_error(400)], r=[response("ok")])
    llm = provider(Endpoint("groq", "q", client(groq)), Endpoint("groq", "r", client(groq)))

    with pytest.raises(LLMError, match="Groq API error 400"):
        llm.complete("s", MESSAGES)


def test_last_endpoint_is_tried_even_when_paused():
    groq = FakeService(q=[response("ok")])
    llm = provider(Endpoint("groq", "q", client(groq)), paused={"groq/q": time.monotonic() + 99})

    assert llm.complete("s", MESSAGES).text == "ok"


def test_factory_builds_the_free_chain(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    settings = Settings.from_env(load_dotenv_file=False)

    with pytest.raises(MissingAPIKeyError, match="GROQ_API_KEY"):
        build_llm(settings, "groq")

    both = replace(settings, groq_api_key="gsk", gemini_api_key="g-key")
    llm = build_llm(both, "groq")
    assert llm.name == "groq"
    assert [e.key for e in llm._endpoints] == [
        "groq/openai/gpt-oss-120b",
        "groq/openai/gpt-oss-20b",
        "gemini/gemini-3.5-flash",
        "gemini/gemini-flash-lite-latest",
    ]
    assert build_llm(both, "gemini")._endpoints[0].key == "gemini/gemini-3.5-flash"
    assert len(build_llm(replace(settings, groq_api_key="gsk"), "groq")._endpoints) == 2


def test_last_agent_step_has_no_tools_so_the_model_must_answer():
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "search_documents", "arguments": '{"query": "x"}'},
        "name": "search_documents",
        "arguments": '{"query": "x"}',
    }
    loop = [response(tool_calls=[call], finish="tool_calls")] * 2
    service = FakeService(**{"m-1": [*loop, response("Nema toga u ubačenim dokumentima.")]})
    llm = provider(Endpoint("groq", "m-1", client(service)))

    run = llm.run_agent("s", MESSAGES, [search_tool([])], max_steps=3)

    assert "tools" in service.calls[0] and "tools" not in service.calls[2]
    assert run.text == "Nema toga u ubačenim dokumentima."
    assert run.steps == 3
