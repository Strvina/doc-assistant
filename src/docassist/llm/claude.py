"""Claude through the official Anthropic SDK."""

from __future__ import annotations

import functools
from typing import Any

import anthropic

from docassist.agent.tools import Tool, execute_tool
from docassist.llm.base import AgentRun, LLMError, LLMResult, Message, StopReason, ToolCall

DEFAULT_MAX_TOKENS = 16000  # room for adaptive thinking + the answer; non-streaming request
# Seconds without a byte from the API before the request fails (the SDK default is 600). Long
# enough for DEFAULT_MAX_TOKENS of thinking and answer in one non-streaming response.
REQUEST_TIMEOUT_S = 300.0

# Server-side refusal fallback: if a safety classifier declines, the API retries the request
# on Anthropic's recommended model for that category. Not available for Haiku models.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


@functools.cache
def shared_client(api_key: str | None) -> anthropic.Anthropic:
    """One SDK client per key for the process: its HTTPS connections are reused by every
    question instead of being opened again (a provider is built per question)."""
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_S)


class ClaudeProvider:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        *,
        effort: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any = None,
    ) -> None:
        self._client = client or shared_client(api_key)
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "claude"

    def complete(self, system: str, messages: list[Message]) -> LLMResult:
        response = self._create(system=system, messages=messages)
        stop_reason = _STOP_REASONS.get(response.stop_reason or "", "other")
        return LLMResult(
            text="" if stop_reason == "refusal" else _text_of(response.content),
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=stop_reason,
        )

    def run_agent(
        self, system: str, messages: list[Message], tools: list[Tool], max_steps: int = 8
    ) -> AgentRun:
        """Manual tool-use loop (ADR-008).

        The conversation is append-only: each assistant turn goes back exactly as returned
        (`response.content`, including thinking blocks), followed by one user turn with all
        tool results of that step.
        """
        conversation: list[dict[str, Any]] = [dict(m) for m in messages]
        tool_defs = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "strict": True,
            }
            for tool in tools
        ]
        calls: list[ToolCall] = []
        input_tokens = output_tokens = 0
        model = self._model

        for step in range(1, max_steps + 1):
            response = self._create(system=system, messages=conversation, tools=tool_defs)
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            model = response.model

            if response.stop_reason == "pause_turn":
                conversation.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason != "tool_use":
                stop_reason = _STOP_REASONS.get(response.stop_reason or "", "other")
                text = "" if stop_reason == "refusal" else _text_of(response.content)
                return AgentRun(text, calls, model, input_tokens, output_tokens, stop_reason, step)

            conversation.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output, is_error = execute_tool(tools, block.name, dict(block.input))
                calls.append(ToolCall(block.name, dict(block.input), output, is_error))
                result: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
                if is_error:
                    result["is_error"] = True
                results.append(result)
            conversation.append({"role": "user", "content": results})

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
        request = {"model": self._model, "max_tokens": self._max_tokens, **request}
        if self._effort:
            request["output_config"] = {"effort": self._effort}
        try:
            if self._supports_fallback():
                return self._client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **request
                )
            return self._client.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Claude: invalid ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude: rate limit reached, try again in a minute") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Claude: cannot reach the API (network problem)") from exc

    def _supports_fallback(self) -> bool:
        return not self._model.startswith("claude-haiku")


def _text_of(content: list[Any]) -> str:
    # A response can start with thinking blocks; only text blocks are the answer.
    return "".join(block.text for block in content if block.type == "text").strip()
