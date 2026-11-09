"""Free-tier LLMs over OpenAI-compatible Chat Completions: Google Gemini and Groq (ADR-021/022).

Both services speak the Chat Completions protocol, so one provider with the `openai` SDK and a
different base URL serves both (no extra dependency). Free tiers have small limits (Gemini:
20 requests/day on some models; Groq: 8,000 tokens/minute per model), so the provider walks a
chain of endpoints: when one is out of quota, overloaded or gets a too-large request, the next
one answers. An endpoint that is out of quota is skipped until it resets.
"""

from __future__ import annotations

import functools
import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import openai

from docassist.agent.tools import Tool, execute_tool
from docassist.llm.base import AgentRun, LLMError, LLMResult, Message, StopReason, ToolCall

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
BASE_URLS = {"gemini": GEMINI_BASE_URL, "groq": GROQ_BASE_URL}
# Seconds without a byte from the service before the request fails (the SDK default is 600):
# a stuck request would otherwise hold the question, and a server thread, for ten minutes.
REQUEST_TIMEOUT_S = 120.0

OVERLOADED_STATUS = (500, 502, 503, 504)
TOO_LARGE_STATUS = 413  # Groq: request is over the per-minute token limit of this model
OVERLOADED_PAUSE_S = 60  # skip an overloaded endpoint this long before trying it again
MAX_PAUSE_S = 24 * 3600
# A per-minute limit that frees up within this many seconds is waited out on the same endpoint:
# faster than a slower fallback, and the conversation stays with one service.
SHORT_WAIT_S = 15
# Gemini rejects tool calls made by another model without a thought signature; this value is
# Google's documented placeholder for such calls.
FOREIGN_CALL_SIGNATURE = "skip_thought_signature_validator"

# "service/model" -> time.monotonic() until which it is skipped. Module level because a new
# provider is built per request; a used-up daily quota would otherwise cost a failed call,
# with retries, on every agent step.
_PAUSED_UNTIL: dict[str, float] = {}


@dataclass(frozen=True)
class Endpoint:
    """One model at one service, e.g. Groq's openai/gpt-oss-120b."""

    service: str  # "gemini" | "groq" — also selects the message format
    model: str
    client: Any
    max_tokens: int = 4096

    @property
    def key(self) -> str:
        return f"{self.service}/{self.model}"


def endpoint(
    service: str, model: str, api_key: str, *, max_tokens: int = 4096, client: Any = None
) -> Endpoint:
    return Endpoint(service, model, client or shared_client(service, api_key), max_tokens)


@functools.cache
def shared_client(service: str, api_key: str) -> openai.OpenAI:
    """One SDK client per service and key for the whole process. A provider is built per
    question; a client of its own would open a new HTTPS connection every time (measured:
    0.1-0.17 s of the ~1 s answer) and leave the old connection pool for the garbage
    collector. The client is thread-safe."""
    return openai.OpenAI(api_key=api_key, base_url=BASE_URLS[service], timeout=REQUEST_TIMEOUT_S)


class ChatCompletionsProvider:
    def __init__(
        self,
        name: str,
        endpoints: list[Endpoint],
        *,
        paused_until: dict[str, float] | None = None,
    ) -> None:
        if not endpoints:
            raise ValueError(f"{name}: no model configured")
        self._name = name
        # Drop duplicates (e.g. fallback model = main model), keep the order.
        unique: dict[str, Endpoint] = {}
        for ep in endpoints:
            unique.setdefault(ep.key, ep)
        self._endpoints = list(unique.values())
        self._paused_until = _PAUSED_UNTIL if paused_until is None else paused_until

    @property
    def name(self) -> str:
        return self._name

    def complete(self, system: str, messages: list[Message]) -> LLMResult:
        response = self._create([{"role": "system", "content": system}, *messages])
        choice = response.choices[0]
        input_tokens, output_tokens = _usage(response)
        return LLMResult(
            text=_clean(choice.message.content),
            model=response.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=_stop_reason(choice.finish_reason),
        )

    def stream(self, system: str, messages: list[Message]) -> Iterator[str | LLMResult]:
        """Answer text as the model writes it, then the whole `LLMResult`.

        The endpoint chain works as in `complete`: quota and overload errors arrive when the
        stream is opened, before any text, so the next endpoint can still take over.
        """
        response = self._create(
            [{"role": "system", "content": system}, *messages],
            stream=True,
            stream_options={"include_usage": True},
        )
        parts: list[str] = []
        model, finish, usage = "", None, None
        try:
            for chunk in response:
                model = getattr(chunk, "model", None) or model
                usage = getattr(chunk, "usage", None) or usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                piece = (getattr(choice.delta, "content", None) or "").translate(_PLAIN)
                piece = piece.replace("\u3010", "[").replace("\u3011", "]")
                if piece:
                    parts.append(piece)
                    yield piece
        except openai.APIError as exc:
            raise LLMError(f"{self._name}: the answer stopped halfway ({exc})") from exc
        finally:
            close = getattr(response, "close", None)
            if close:  # also when the client went away: stop generating, keep the quota
                close()
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        yield LLMResult(
            text=_clean("".join(parts)),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=_stop_reason(finish),
        )

    def run_agent(
        self, system: str, messages: list[Message], tools: list[Tool], max_steps: int = 8
    ) -> AgentRun:
        """Function-calling loop over Chat Completions."""
        history: list[Any] = [{"role": "system", "content": system}, *messages]
        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]
        calls: list[ToolCall] = []
        input_tokens = output_tokens = 0
        model = self._endpoints[0].model

        for step in range(1, max_steps + 1):
            # Last step: no tools, so the model has to answer with what it found so far.
            final = step == max_steps
            response = self._create(history, **({} if final else {"tools": tool_defs}))
            used_in, used_out = _usage(response)
            input_tokens += used_in
            output_tokens += used_out
            model = response.model or model
            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls or final:
                stop = _stop_reason(choice.finish_reason)
                text = _clean(message.content)
                return AgentRun(text, calls, model, input_tokens, output_tokens, stop, step)

            # Keep the assistant turn as sent: Gemini attaches "thought signatures" to tool
            # calls and expects them back (they are stripped for other services, see _for).
            history.append(_as_dict(message))
            for call in message.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                if not isinstance(args, dict):
                    output, is_error = "Error: arguments were not a valid JSON object", True
                    args = {}
                else:
                    output, is_error = execute_tool(tools, call.function.name, args)
                calls.append(ToolCall(call.function.name, args, output, is_error))
                history.append({"role": "tool", "tool_call_id": call.id, "content": output})

        return AgentRun(
            "I could not finish within the allowed number of steps.",
            calls,
            model,
            input_tokens,
            output_tokens,
            "max_steps",
            max_steps,
        )

    def _create(self, messages: list[Any], **request: Any) -> Any:
        """Try the endpoints in order; skip ones that are paused; pause ones out of quota.

        Only the last endpoint gets the SDK's own retries: switching to the next endpoint at
        once is faster than waiting for a busy one.
        """
        now = time.monotonic()
        ready = [e for e in self._endpoints if self._paused_until.get(e.key, 0) <= now]
        chain = ready or [self._endpoints[-1]]
        for index, ep in enumerate(chain):
            last = index == len(chain) - 1
            retries = None if last else 0
            try:
                try:
                    return self._call(ep, _for(ep, messages), retries=retries, **request)
                except LLMError as exc:
                    pause = _pause_after(exc.__cause__)
                    if not (isinstance(exc.__cause__, openai.RateLimitError) and pause):
                        raise
                    if pause > SHORT_WAIT_S:
                        raise
                    time.sleep(pause)  # tokens-per-minute limit: frees up in a few seconds
                    return self._call(ep, _for(ep, messages), retries=retries, **request)
            except LLMError as exc:
                pause = _pause_after(exc.__cause__)
                if pause is None:
                    raise
                if pause:
                    self._paused_until[ep.key] = time.monotonic() + pause
                if last:
                    raise
        raise AssertionError("unreachable")

    def _call(
        self, ep: Endpoint, messages: list[Any], *, retries: int | None, **request: Any
    ) -> Any:
        client = ep.client
        if retries is not None and hasattr(client, "with_options"):
            client = client.with_options(max_retries=retries)
        label = ep.service.capitalize()
        try:
            return client.chat.completions.create(
                model=ep.model, max_tokens=ep.max_tokens, messages=messages, **request
            )
        except openai.AuthenticationError as exc:
            raise LLMError(f"{label}: invalid {ep.service.upper()}_API_KEY") from exc
        except openai.RateLimitError as exc:
            raise LLMError(f"{label}: free-tier limit reached, try again later") from exc
        except openai.APIStatusError as exc:
            if exc.status_code in OVERLOADED_STATUS:
                raise LLMError(f"{label} is overloaded right now, try again in a minute") from exc
            raise LLMError(f"{label} API error {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"{label}: cannot reach the API (network problem)") from exc


def _pause_after(error: BaseException | None) -> float | None:
    """Seconds to skip an endpoint after this error (0 = just try the next one this time),
    or None if the error is not about quota or load (then it is raised)."""
    if isinstance(error, openai.RateLimitError):
        return min(_retry_after(error), MAX_PAUSE_S)
    if isinstance(error, openai.APIStatusError):
        if error.status_code in OVERLOADED_STATUS:
            return OVERLOADED_PAUSE_S
        if error.status_code == TOO_LARGE_STATUS:
            return 0  # this request is too big for the model; smaller ones still fit
    return None


def _retry_after(error: openai.APIStatusError) -> float:
    header = error.response.headers.get("retry-after") if error.response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = re.search(r"retryDelay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", str(error.body))  # Gemini
    return float(match.group(1)) if match else OVERLOADED_PAUSE_S


_STANDARD_CALL_FIELDS = ("id", "type", "function")


def _for(ep: Endpoint, messages: list[Any]) -> list[Any]:
    """Messages in the form `ep` accepts. An agent run can move between services when one
    hits its limit: assistant turns keep only the standard fields; Gemini also gets its own
    thought signatures back, and a placeholder for tool calls another model made."""
    gemini = ep.service == "gemini"
    cleaned = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant":
            out: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("tool_calls"):
                out["tool_calls"] = [_call_for(call, gemini) for call in message["tool_calls"]]
            cleaned.append(out)
        else:
            cleaned.append(message)
    return cleaned


def _call_for(call: dict[str, Any], gemini: bool) -> dict[str, Any]:
    out = {k: call[k] for k in _STANDARD_CALL_FIELDS if k in call}
    if gemini:
        out["extra_content"] = call.get("extra_content") or {
            "google": {"thought_signature": FOREIGN_CALL_SIGNATURE}
        }
    return out


def _as_dict(message: Any) -> Any:
    dump = getattr(message, "model_dump", None)
    return dump(exclude_none=True) if dump else message


def _clean(text: str | None) -> str:
    # Some open models put their reasoning in the answer; drop it.
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    # gpt-oss sometimes writes citations in CJK brackets: 【file.pdf, str. 2】 -> [file.pdf, str. 2]
    text = text.replace("【", "[").replace("】", "]")
    # gpt-oss writes typographic look-alikes: a narrow no-break space ("20 Nm") and
    # non-breaking hyphens ("Panelvolt-Staff"); plain characters keep names copyable.
    return text.translate(_PLAIN).strip()


_PLAIN = str.maketrans({" ": " ", " ": " ", "‑": "-", "‐": "-"})


def _usage(response: Any) -> tuple[int, int]:
    usage = response.usage
    if not usage:
        return 0, 0
    return usage.prompt_tokens or 0, usage.completion_tokens or 0


def _stop_reason(finish_reason: str | None) -> StopReason:
    if finish_reason in (None, "stop", "tool_calls"):
        return "end"
    return _STOP_REASONS.get(finish_reason, "other")


_STOP_REASONS: dict[str, StopReason] = {"length": "max_tokens", "content_filter": "refusal"}
