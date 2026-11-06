"""ChatGPT models through the official OpenAI SDK (Responses API)."""

from __future__ import annotations

import json
from typing import Any

import openai

from docassist.agent.tools import Tool, execute_tool
from docassist.llm.base import AgentRun, LLMError, LLMResult, Message, StopReason, ToolCall

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
        response = self._create(instructions=system, input=list(messages))
        usage = response.usage
        return LLMResult(
            text=(response.output_text or "").strip(),
            model=response.model,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            stop_reason=_stop_reason(response),
        )

    def run_agent(
        self, system: str, messages: list[Message], tools: list[Tool], max_steps: int = 8
    ) -> AgentRun:
        """Function-calling loop: output items (incl. reasoning) go back as input each step."""
        items: list[Any] = list(messages)
        tool_defs = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
                "strict": True,
            }
            for tool in tools
        ]
        calls: list[ToolCall] = []
        input_tokens = output_tokens = 0
        model = self._model

        for step in range(1, max_steps + 1):
            response = self._create(
                instructions=system,
                input=items,
                tools=tool_defs,
                # Needed to pass reasoning items back when store=False.
                include=["reasoning.encrypted_content"],
            )
            if response.usage:
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
            model = response.model

            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                text = (response.output_text or "").strip()
                return AgentRun(
                    text, calls, model, input_tokens, output_tokens, _stop_reason(response), step
                )

            items.extend(response.output)
            for call in function_calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                if not isinstance(args, dict):
                    output, is_error = "Error: arguments were not a valid JSON object", True
                    args = {}
                else:
                    output, is_error = execute_tool(tools, call.name, args)
                calls.append(ToolCall(call.name, args, output, is_error))
                items.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": output}
                )

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
        try:
            return self._client.responses.create(
                model=self._model,
                max_output_tokens=self._max_output_tokens,
                store=False,  # don't keep company documents on OpenAI's side longer than needed
                **request,
            )
        except openai.AuthenticationError as exc:
            raise LLMError("OpenAI: invalid OPENAI_API_KEY") from exc
        except openai.RateLimitError as exc:
            raise LLMError("OpenAI: rate limit or quota reached") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"OpenAI API error {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("OpenAI: cannot reach the API (network problem)") from exc


def _stop_reason(response: Any) -> StopReason:
    if response.status != "incomplete":
        return "end"
    reason = getattr(response.incomplete_details, "reason", None) or ""
    return _STOP_REASONS.get(reason, "other")


_STOP_REASONS: dict[str, StopReason] = {
    "max_output_tokens": "max_tokens",
    "content_filter": "refusal",
}
