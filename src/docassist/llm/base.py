"""Provider-neutral interface to a chat LLM (ADR-005)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

if TYPE_CHECKING:
    from docassist.agent.tools import Tool


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


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
