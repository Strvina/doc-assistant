from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from docassist.llm import (
    NOT_FOUND_SR,
    SYSTEM_PROMPT,
    FakeProvider,
    LLMError,
    build_user_message,
)
from docassist.llm.claude import ClaudeProvider
from docassist.llm.openai_provider import OpenAIProvider
from docassist.models import Chunk

MESSAGES = [{"role": "user", "content": "Pitanje?"}]


def chunk(source="manual.pdf", page=2, text="Das Anzugsmoment beträgt 20 Nm."):
    return Chunk(f"{source}::p{page}::0", "doc", source, page, text)


# --- prompts -----------------------------------------------------------------------------


def test_user_message_contains_context_and_question():
    message = build_user_message(
        "Koliki je moment?", [chunk(), chunk(source="a&b.txt", page=1, text="x")]
    )

    assert '<document index="1" source="manual.pdf" page="2">' in message
    assert "Das Anzugsmoment beträgt 20 Nm." in message
    assert 'source="a&amp;b.txt"' in message
    assert message.endswith("Question: Koliki je moment?")


def test_system_prompt_states_the_rules():
    assert "[file name, str. page]" in SYSTEM_PROMPT
    assert NOT_FOUND_SR in SYSTEM_PROMPT
    assert "language of the question" in SYSTEM_PROMPT


# --- FakeProvider ------------------------------------------------------------------------


def test_fake_provider_quotes_first_document_with_citation():
    text = (
        "6. Anzugsmomente\n\nDie folgenden Anzugsmomente gelten für Schrauben der "
        "Festigkeitsklasse 8.8. Nach dem Anziehen markieren."
    )
    message = build_user_message("?", [chunk(text=text)])
    llm = FakeProvider()

    result = llm.complete(SYSTEM_PROMPT, [{"role": "user", "content": message}])

    assert result.text == (
        "Die folgenden Anzugsmomente gelten für Schrauben der Festigkeitsklasse 8.8. "
        "[manual.pdf, str. 2]"
    )
    assert result.stop_reason == "end"
    assert llm.calls[0][0] == SYSTEM_PROMPT


def test_fake_provider_without_context_says_not_found():
    assert FakeProvider().complete("s", MESSAGES).text == NOT_FOUND_SR


def test_fake_provider_fixed_answer():
    assert FakeProvider("Odgovor.").complete("s", MESSAGES).text == "Odgovor."


# --- ClaudeProvider (fake client, no network) --------------------------------------------


class FakeAnthropicMessages:
    def __init__(self, response=None, error=None):
        self.requests = []
        self._response = response
        self._error = error

    def create(self, **request):
        self.requests.append(request)
        if self._error:
            raise self._error
        return self._response


def anthropic_response(stop_reason="end_turn", content=None):
    if content is None:
        content = [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Moment je 20 Nm "),
            SimpleNamespace(type="text", text="[manual.pdf, str. 2]"),
        ]
    return SimpleNamespace(
        content=content,
        model="claude-sonnet-5-5",
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=80),
    )


def fake_anthropic_client(response=None, error=None):
    messages = FakeAnthropicMessages(response, error)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)), messages


def test_claude_request_and_result():
    client, messages = fake_anthropic_client(anthropic_response())
    llm = ClaudeProvider("claude-sonnet-5-5", client=client, effort="low")

    result = llm.complete("SYSTEM", MESSAGES)

    [request] = messages.requests
    assert request["model"] == "claude-sonnet-5-5"
    assert request["system"] == "SYSTEM"
    assert request["messages"] == MESSAGES
    assert request["output_config"] == {"effort": "low"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in request and "temperature" not in request
    assert result.text == "Moment je 20 Nm [manual.pdf, str. 2]"
    assert (result.input_tokens, result.output_tokens) == (1200, 80)
    assert result.stop_reason == "end"
    assert llm.name == "claude"


def test_claude_without_effort_and_haiku_without_fallback():
    client, messages = fake_anthropic_client(anthropic_response())

    ClaudeProvider("claude-haiku-5-5", client=client).complete("s", MESSAGES)

    [request] = messages.requests
    assert "output_config" not in request
    assert "fallbacks" not in request and "betas" not in request


def test_claude_refusal_returns_empty_text():
    response = anthropic_response(
        stop_reason="refusal", content=[SimpleNamespace(type="text", text="partial")]
    )
    client, _ = fake_anthropic_client(response)

    result = ClaudeProvider("claude-sonnet-5-5", client=client).complete("s", MESSAGES)

    assert (result.text, result.stop_reason) == ("", "refusal")


def _anthropic_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("boom", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (_anthropic_error(anthropic.AuthenticationError, 401), "ANTHROPIC_API_KEY"),
        (_anthropic_error(anthropic.RateLimitError, 429), "rate limit"),
        (_anthropic_error(anthropic.BadRequestError, 400), "error 400"),
    ],
)
def test_claude_errors_become_llm_errors(error, message):
    client, _ = fake_anthropic_client(error=error)

    with pytest.raises(LLMError, match=message):
        ClaudeProvider("claude-sonnet-5-5", client=client).complete("s", MESSAGES)


# --- OpenAIProvider (fake client, no network) --------------------------------------------


class FakeResponses:
    def __init__(self, response):
        self.requests = []
        self._response = response

    def create(self, **request):
        self.requests.append(request)
        return self._response


def openai_response(status="completed", reason=None):
    return SimpleNamespace(
        output_text=" Moment je 20 Nm [manual.pdf, str. 2] ",
        model="gpt-test",
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        usage=SimpleNamespace(input_tokens=900, output_tokens=40),
    )


def test_openai_request_and_result():
    responses = FakeResponses(openai_response())
    llm = OpenAIProvider("gpt-test", client=SimpleNamespace(responses=responses))

    result = llm.complete("SYSTEM", MESSAGES)

    [request] = responses.requests
    assert request["model"] == "gpt-test"
    assert request["instructions"] == "SYSTEM"
    assert request["input"] == MESSAGES
    assert request["store"] is False
    assert result.text == "Moment je 20 Nm [manual.pdf, str. 2]"
    assert (result.input_tokens, result.output_tokens) == (900, 40)
    assert result.stop_reason == "end"


@pytest.mark.parametrize(
    ("reason", "expected"), [("max_output_tokens", "max_tokens"), ("content_filter", "refusal")]
)
def test_openai_incomplete_responses(reason, expected):
    responses = FakeResponses(openai_response(status="incomplete", reason=reason))
    llm = OpenAIProvider("gpt-test", client=SimpleNamespace(responses=responses))

    assert llm.complete("s", MESSAGES).stop_reason == expected


def test_openai_requires_model():
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        OpenAIProvider("", client=object())


def test_openai_errors_become_llm_errors():
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    error = openai.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=request), body=None
    )

    class Failing:
        def create(self, **request):
            raise error

    llm = OpenAIProvider("gpt-test", client=SimpleNamespace(responses=Failing()))
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        llm.complete("s", MESSAGES)
