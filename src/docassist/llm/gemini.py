"""Google Gemini through its OpenAI-compatible Chat Completions endpoint (ADR-021).

Uses the OpenAI SDK with a different base URL, so no extra dependency is needed. The free
tier of the Gemini API makes a public demo possible without paid credits.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import openai

from docassist.agent.tools import Tool, execute_tool
from docassist.llm.base import AgentRun, LLMError, LLMResult, Message, StopReason, ToolCall

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MAX_TOKENS = 8192  # thinking models spend part of this on reasoning
OVERLOADED_STATUS = (500, 503, 504)  # "model is overloaded" on the free tier
OVERLOADED_PAUSE_S = 60  # skip an overloaded model this long before trying it again
MAX_PAUSE_S = 24 * 3600

# model -> time.monotonic() until which it is skipped. Module level because a new provider
# is built per request; a used-up daily quota (20 requests/day on some free-tier models)
# would otherwise cost a failed call, with retries, on every agent step.
_PAUSED_UNTIL: dict[str, float] = {}


class GeminiProvider:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        *,
        base_url: str = GEMINI_BASE_URL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        fallback_models: tuple[str, ...] = (),
        client: Any = None,
        paused_until: dict[str, float] | None = None,
    ) -> None:
        if not model:
            raise ValueError("GEMINI_MODEL is not set")
        self._client = client or openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._fallback_models = tuple(m for m in fallback_models if m and m != model)
        self._max_tokens = max_tokens
        self._paused_until = _PAUSED_UNTIL if paused_until is None else paused_until

    @property
    def name(self) -> str:
        return "gemini"

    def complete(self, system: str, messages: list[Message]) -> LLMResult:
        response = self._create(messages=[{"role": "system", "content": system}, *messages])
        choice = response.choices[0]
        input_tokens, output_tokens = _usage(response)
        return LLMResult(
            text=(choice.message.content or "").strip(),
            model=response.model or self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=_stop_reason(choice.finish_reason),
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
        model = self._model

        for step in range(1, max_steps + 1):
            response = self._create(messages=history, tools=tool_defs)
            used_in, used_out = _usage(response)
            input_tokens += used_in
            output_tokens += used_out
            model = response.model or model
            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls:
                text = (message.content or "").strip()
                stop = _stop_reason(choice.finish_reason)
                return AgentRun(text, calls, model, input_tokens, output_tokens, stop, step)

            # Send the assistant turn back unchanged: Gemini attaches "thought signatures"
            # to tool calls and expects them in the next request.
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

    def _create(self, **request: Any) -> Any:
        """Call the main model; if it is out of quota or overloaded, use the fallbacks.

        A model that failed that way is skipped until its quota resets (Google's retryDelay)
        or for a minute, and a model with a fallback behind it is not retried by the SDK:
        switching models at once is faster than waiting.
        """
        all_models = (self._model, *self._fallback_models)
        now = time.monotonic()
        models = [m for m in all_models if self._paused_until.get(m, 0) <= now] or [all_models[-1]]
        for index, model in enumerate(models):
            last = index == len(models) - 1
            try:
                return self._call(model, retries=None if last else 0, **request)
            except LLMError as exc:
                pause = _pause_after(exc.__cause__)
                if pause is None:
                    raise
                self._paused_until[model] = time.monotonic() + pause
                if last:
                    raise
        raise AssertionError("unreachable")

    def _call(self, model: str, *, retries: int | None, **request: Any) -> Any:
        client = self._client
        if retries is not None and hasattr(client, "with_options"):
            client = client.with_options(max_retries=retries)
        try:
            return client.chat.completions.create(
                model=model, max_tokens=self._max_tokens, **request
            )
        except openai.AuthenticationError as exc:
            raise LLMError("Gemini: invalid GEMINI_API_KEY") from exc
        except openai.RateLimitError as exc:
            raise LLMError("Gemini: free-tier rate limit reached, try again in a minute") from exc
        except openai.APIStatusError as exc:
            if exc.status_code in OVERLOADED_STATUS:
                raise LLMError("Gemini is overloaded right now, try again in a minute") from exc
            raise LLMError(f"Gemini API error {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Gemini: cannot reach the API (network problem)") from exc


def _pause_after(error: BaseException | None) -> float | None:
    """Seconds to skip a model after this error, or None if it is not a quota/overload error."""
    if isinstance(error, openai.RateLimitError):
        match = re.search(r"retryDelay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", str(error.body))
        return min(float(match.group(1)), MAX_PAUSE_S) if match else OVERLOADED_PAUSE_S
    if isinstance(error, openai.APIStatusError) and error.status_code in OVERLOADED_STATUS:
        return OVERLOADED_PAUSE_S
    return None


def _as_dict(message: Any) -> Any:
    dump = getattr(message, "model_dump", None)
    return dump(exclude_none=True) if dump else message


def _usage(response: Any) -> tuple[int, int]:
    usage = response.usage
    if not usage:
        return 0, 0
    return usage.prompt_tokens or 0, usage.completion_tokens or 0


def _stop_reason(finish_reason: str | None) -> StopReason:
    if finish_reason in (None, "stop", "tool_calls"):
        return "end"
    return {"length": "max_tokens", "content_filter": "refusal"}.get(finish_reason, "other")
