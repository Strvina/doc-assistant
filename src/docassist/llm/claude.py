"""Claude through the official Anthropic SDK."""

from __future__ import annotations

from typing import Any

import anthropic

from docassist.llm.base import LLMError, LLMResult, Message, StopReason

DEFAULT_MAX_TOKENS = 16000  # room for adaptive thinking + the answer; non-streaming request

# Server-side refusal fallback: if a safety classifier declines, the API retries the request
# on Anthropic's recommended model for that category. Not available for Haiku models.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


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
        self._client = client or anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "claude"

    def complete(self, system: str, messages: list[Message]) -> LLMResult:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
        }
        if self._effort:
            request["output_config"] = {"effort": self._effort}

        try:
            if self._supports_fallback():
                response = self._client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **request
                )
            else:
                response = self._client.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Claude: invalid ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude: rate limit reached, try again in a minute") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Claude: cannot reach the API (network problem)") from exc

        stop_reason = _STOP_REASONS.get(response.stop_reason or "", "other")
        # A response can start with thinking blocks; only text blocks are the answer.
        text = "" if stop_reason == "refusal" else _text_of(response.content)
        return LLMResult(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=stop_reason,
        )

    def _supports_fallback(self) -> bool:
        return not self._model.startswith("claude-haiku")


def _text_of(content: list[Any]) -> str:
    return "".join(block.text for block in content if block.type == "text").strip()
