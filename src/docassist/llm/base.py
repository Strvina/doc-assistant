"""Provider-neutral interface to a chat LLM (ADR-005)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict


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
