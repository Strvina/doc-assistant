"""Offline LLM for tests and for trying the app without an API key."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from html import unescape
from typing import TYPE_CHECKING, Any

from docassist.llm.base import AgentRun, LLMResult, Message, ToolCall
from docassist.llm.prompts import NOT_FOUND_SR, REWRITE_PROMPT

if TYPE_CHECKING:
    from docassist.agent.tools import Tool

Responder = Callable[[str, list[Message]], str]

_DOCUMENT = re.compile(
    r'<document index="\d+" source="(?P<source>[^"]+)" page="(?P<page>\d+)">\n'
    r"(?P<text>.*?)\n</document>",
    re.DOTALL,
)


def quote_first_document(system: str, messages: list[Message]) -> str:
    """Default fake answer: the first sentence of the best-ranked document, with a citation.
    A follow-up question to rewrite (`REWRITE_PROMPT`) comes back unchanged."""
    if system == REWRITE_PROMPT:
        return messages[-1]["content"]
    match = _DOCUMENT.search(messages[-1]["content"])
    if match is None:
        return NOT_FOUND_SR
    lines = [line.strip() for line in match["text"].splitlines() if line.strip()]
    # Skip headings ("7. Povreda na radu") and table rows: take the first real sentence.
    body = next((line for line in lines if len(line) > 60), lines[0] if lines else "")
    sentence = re.split(r"(?<=[a-zčćžšđäöüß)\d][.!?])\s+(?=[A-ZČĆŽŠĐÄÖÜ])", body, maxsplit=1)[0]
    return f"{sentence} [{unescape(match['source'])}, str. {match['page']}]"


class FakeProvider:
    """Returns canned answers and records every call. No network, no cost."""

    def __init__(
        self,
        responder: Responder | str = quote_first_document,
        tool_script: Sequence[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        """`tool_script`: tool calls the fake agent makes, in order. Default: one
        search_documents call with the user's question."""
        if isinstance(responder, str):
            fixed = responder
            responder = lambda system, messages: fixed  # noqa: E731
        self._responder = responder
        self._tool_script = tool_script
        self.calls: list[tuple[str, list[Message]]] = []

    @property
    def name(self) -> str:
        return "fake"

    def complete(self, system: str, messages: list[Message]) -> LLMResult:
        self.calls.append((system, list(messages)))
        text = self._responder(system, messages)
        prompt_chars = len(system) + sum(len(m["content"]) for m in messages)
        return LLMResult(
            text=text,
            model="fake",
            input_tokens=prompt_chars // 4,  # rough token estimate, just for display
            output_tokens=len(text) // 4,
            stop_reason="end",
        )

    def stream(self, system: str, messages: list[Message]) -> Iterator[str | LLMResult]:
        """The same answer as `complete`, word by word (to test streaming without a model)."""
        result = self.complete(system, messages)
        yield from re.findall(r"\S+\s*", result.text)
        yield result

    def run_agent(
        self, system: str, messages: list[Message], tools: list[Tool], max_steps: int = 8
    ) -> AgentRun:
        from docassist.agent.tools import execute_tool

        question = messages[-1]["content"]
        script = self._tool_script
        if script is None:
            script = [("search_documents", {"query": question})]

        calls: list[ToolCall] = []
        for name, args in list(script)[:max_steps]:
            output, is_error = execute_tool(tools, name, args)
            calls.append(ToolCall(name, args, output, is_error))

        # Answer from the last successful tool output, as if it were the document context.
        context = next((c.output for c in reversed(calls) if not c.is_error), "")
        result = self.complete(system, [{"role": "user", "content": context}])
        return AgentRun(
            result.text,
            calls,
            "fake",
            result.input_tokens,
            result.output_tokens,
            "end",
            len(calls) + 1,
        )
