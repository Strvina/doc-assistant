"""Offline LLM for tests and for trying the app without an API key."""

from __future__ import annotations

import re
from collections.abc import Callable
from html import unescape

from docassist.llm.base import LLMResult, Message
from docassist.llm.prompts import NOT_FOUND_SR

Responder = Callable[[str, list[Message]], str]

_DOCUMENT = re.compile(
    r'<document index="\d+" source="(?P<source>[^"]+)" page="(?P<page>\d+)">\n'
    r"(?P<text>.*?)\n</document>",
    re.DOTALL,
)


def quote_first_document(system: str, messages: list[Message]) -> str:
    """Default fake answer: the first sentence of the best-ranked document, with a citation."""
    match = _DOCUMENT.search(messages[-1]["content"])
    if match is None:
        return NOT_FOUND_SR
    paragraphs = [p.strip() for p in match["text"].split("\n\n") if p.strip()]
    # Skip a short heading line if there is a real paragraph after it.
    body = next((p for p in paragraphs if len(p) > 60), paragraphs[0] if paragraphs else "")
    sentence = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0]
    return f"{sentence} [{unescape(match['source'])}, str. {match['page']}]"


class FakeProvider:
    """Returns canned answers and records every call. No network, no cost."""

    def __init__(self, responder: Responder | str = quote_first_document) -> None:
        if isinstance(responder, str):
            fixed = responder
            responder = lambda system, messages: fixed  # noqa: E731
        self._responder = responder
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
