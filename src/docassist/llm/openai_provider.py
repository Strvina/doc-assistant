"""ChatGPT models through the official OpenAI SDK (Responses API)."""

from __future__ import annotations

from typing import Any

import openai

from docassist.llm.base import LLMError, LLMResult, Message, StopReason

DEFAULT_MAX_OUTPUT_TOKENS = 16000


class OpenAIProvider:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        *,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        client: Any = None,
    ) -> None:
        if not model:
            raise ValueError("OPENAI_MODEL is not set")
        self._client = client or openai.OpenAI(api_key=api_key)
        self._model = model
        self._max_output_tokens = max_output_tokens

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, system: str, messages: list[Message]) -> LLMResult:
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=system,
                input=list(messages),
                max_output_tokens=self._max_output_tokens,
                store=False,  # don't keep company documents on OpenAI's side longer than needed
            )
        except openai.AuthenticationError as exc:
            raise LLMError("OpenAI: invalid OPENAI_API_KEY") from exc
        except openai.RateLimitError as exc:
            raise LLMError("OpenAI: rate limit or quota reached") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"OpenAI API error {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("OpenAI: cannot reach the API (network problem)") from exc

        stop_reason: StopReason = "end"
        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            stop_reason = {"max_output_tokens": "max_tokens", "content_filter": "refusal"}.get(
                reason or "", "other"
            )
        usage = response.usage
        return LLMResult(
            text=(response.output_text or "").strip(),
            model=response.model,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            stop_reason=stop_reason,
        )
