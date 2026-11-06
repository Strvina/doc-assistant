"""Provider-neutral interface to a chat LLM (ADR-005)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

if TYPE_CHECKING:
    from docassist.agent.tools import Tool


class Message(TypedDict):
    role: Literal["user", "assistant"]
    # Text, or text and image parts for a model that reads pictures (ADR-029).
    content: str | list[dict[str, Any]]


def text_of(content: str | list[dict[str, Any]]) -> str:
    """The text of a message, without its image parts."""
    if isinstance(content, str):
        return content
    return "\n".join(str(part.get("text", "")) for part in content if part.get("type") == "text")


StopReason = Literal["end", "max_tokens", "refusal", "other"]


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: StopReason


class LLMError(RuntimeError):
    """The provider call failed (bad key, no credit, rate limit, network...)."""


class LLMProvider(Protocol):
    @property
    def name(self) -> str:
        """Short provider name shown in the UI: "claude", "openai" or "fake"."""
        ...

    def complete(self, system: str, messages: list[Message]) -> LLMResult: ...

    def run_agent(
        self, system: str, messages: list[Message], tools: list[Tool], max_steps: int = 8
    ) -> AgentRun:
        """Native tool-use loop: call tools until the model gives a final answer."""
        ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool


@dataclass(frozen=True)
class AgentRun:
    """Result of an agent loop: final text plus every tool call made on the way."""

    text: str
    tool_calls: list[ToolCall]
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str  # "end" | "max_tokens" | "refusal" | "max_steps" | "other"
    steps: int


def stream_complete(llm: Any, system: str, messages: list[Message]) -> Iterator[str | LLMResult]:
    """Pieces of the answer as they are written, then the whole `LLMResult` last.

    Providers with a `stream` method send text as the model writes it; for the others the
    whole answer arrives as one piece (same result, just no early text).
    """
    stream = getattr(llm, "stream", None)
    if stream is not None:
        yield from stream(system, messages)
        return
    result = llm.complete(system, messages)
    if result.text:
        yield result.text
    yield result
