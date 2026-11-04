"""GeminiProvider with a fake OpenAI-compatible client (no network)."""

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
from docassist.llm.gemini import GeminiProvider

MESSAGES = [{"role": "user", "content": "Pitanje?"}]


class Message(SimpleNamespace):
    def model_dump(self, exclude_none=False):
        data = {"role": "assistant", "content": self.content, "tool_calls": self.raw_calls}
        return {k: v for k, v in data.items() if v is not None or not exclude_none}


def response(content=None, tool_calls=None, finish="stop"):
    calls = [
        SimpleNamespace(
            id=c["id"], function=SimpleNamespace(name=c["name"], arguments=c["arguments"])
        )
        for c in tool_calls or []
    ]
    message = Message(content=content, tool_calls=calls or None, raw_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        model="gemini-test",
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
    )


class Scripted:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})
        return self._responses.pop(0)


def provider(completions):
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return GeminiProvider("gemini-test", client=client, paused_until={})


def test_complete_sends_system_message_and_maps_result():
    completions = Scripted(response("20 Nm [manual.pdf, str. 2]"))

    result = provider(completions).complete("SYSTEM", MESSAGES)

    request = completions.requests[0]
    assert request["model"] == "gemini-test"
    assert request["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert request["messages"][1:] == MESSAGES
    assert result.text == "20 Nm [manual.pdf, str. 2]"
    assert (result.input_tokens, result.output_tokens) == (100, 10)
    assert result.stop_reason == "end"


@pytest.mark.parametrize(
    ("finish", "expected"), [("length", "max_tokens"), ("content_filter", "refusal")]
)
def test_stop_reasons(finish, expected):
    result = provider(Scripted(response("x", finish=finish))).complete("s", MESSAGES)
    assert result.stop_reason == expected


def test_agent_loop_returns_tool_results_and_keeps_assistant_turn():
    seen = []
    tool = Tool(
        name="search_documents",
        description="Search",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=lambda args: seen.append(args) or "found: 20 Nm",
    )
    calls = [
        {"id": "c1", "name": "search_documents", "arguments": json.dumps({"query": "M8"})},
        {"id": "c2", "name": "search_documents", "arguments": "not json"},
    ]
    completions = Scripted(response(tool_calls=calls, finish="tool_calls"), response("20 Nm"))

    run = provider(completions).run_agent("SYSTEM", MESSAGES, [tool])

    first, second = completions.requests
    assert first["tools"][0]["function"]["name"] == "search_documents"
    assistant, *tool_msgs = second["messages"][-3:]
    assert assistant["tool_calls"] == calls  # passed back unchanged (thought signatures)
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert tool_msgs[0]["content"] == "found: 20 Nm"
    assert "not a valid JSON" in tool_msgs[1]["content"]
    assert seen == [{"query": "M8"}]
    assert run.text == "20 Nm"
    assert [c.is_error for c in run.tool_calls] == [False, True]
    assert (run.steps, run.input_tokens) == (2, 200)


def test_rate_limit_becomes_llm_error():
    request = httpx2.Request("POST", "https://generativelanguage.googleapis.com")
    error = openai.RateLimitError(
        "quota", response=httpx2.Response(429, request=request), body=None
    )

    class Failing:
        def create(self, **request):
            raise error

    with pytest.raises(LLMError, match="rate limit"):
        provider(Failing()).complete("s", MESSAGES)


def test_factory_needs_a_key(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    settings = Settings.from_env(load_dotenv_file=False)

    with pytest.raises(MissingAPIKeyError, match="GEMINI_API_KEY"):
        build_llm(settings, "gemini")
    llm = build_llm(replace(settings, gemini_api_key="g-key"), "gemini")
    assert llm.name == "gemini"


def status_error(code, body=None):
    request = httpx2.Request("POST", "https://generativelanguage.googleapis.com")
    response = httpx2.Response(code, request=request)
    if code == 429:
        return openai.RateLimitError("quota", response=response, body=body)
    return openai.APIStatusError("busy", response=response, body=body)


class Overloaded:
    """Fails with `code` for the main model, answers with any other model."""

    def __init__(self, code, body=None):
        self.code = code
        self.body = body
        self.models = []

    def create(self, **request):
        self.models.append(request["model"])
        if request["model"] == "gemini-test":
            raise status_error(self.code, self.body)
        return response("odgovor")


def test_falls_back_when_the_main_model_is_overloaded():
    completions = Overloaded(503)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm = GeminiProvider(
        "gemini-test", fallback_models=("gemini-lite",), client=client, paused_until={}
    )

    assert llm.complete("s", MESSAGES).text == "odgovor"
    assert completions.models == ["gemini-test", "gemini-lite"]


def test_no_fallback_for_other_errors():
    completions = Overloaded(400)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm = GeminiProvider(
        "gemini-test", fallback_models=("gemini-lite",), client=client, paused_until={}
    )

    with pytest.raises(LLMError, match="400"):
        llm.complete("s", MESSAGES)
    assert completions.models == ["gemini-test"]


def test_used_up_daily_quota_skips_the_model_until_it_resets():
    body = [
        {
            "error": {
                "code": 429,
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "67913s"}
                ],
            }
        }
    ]
    completions = Overloaded(429, body)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    paused = {}
    llm = GeminiProvider(
        "gemini-test", fallback_models=("gemini-lite",), client=client, paused_until=paused
    )

    llm.complete("s", MESSAGES)
    llm.complete("s", MESSAGES)

    assert completions.models == ["gemini-test", "gemini-lite", "gemini-lite"]
    assert paused["gemini-test"] - time.monotonic() > 67000


def test_last_model_is_tried_even_when_paused():
    completions = Scripted(response("odgovor"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    paused = {"gemini-test": time.monotonic() + 100}
    llm = GeminiProvider("gemini-test", client=client, paused_until=paused)

    assert llm.complete("s", MESSAGES).text == "odgovor"
