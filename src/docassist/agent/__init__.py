"""Tool-using agent on top of the native tool-use APIs (ADR-008)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import date

from docassist.agent.prompts import AGENT_SYSTEM_PROMPT
from docassist.agent.tools import (
    Escalator,
    JsonlFile,
    LeaveRequestSink,
    Recorder,
    Tool,
    ToolContext,
    WebhookEscalator,
    build_tools,
    execute_tool,
)
from docassist.llm import LLMProvider, Message
from docassist.rag import Answer, extract_citations

DEFAULT_MAX_STEPS = 8


def run_agent(
    question: str,
    ctx: ToolContext,
    llm: LLMProvider,
    *,
    history: Sequence[tuple[str, str]] = (),
    max_steps: int = DEFAULT_MAX_STEPS,
    today: date | None = None,
) -> Answer:
    """Let the model use the tools to answer `question`; citations are checked against every
    passage the tools returned during this run."""
    started = time.perf_counter()
    ctx.retrieved.clear()
    tools = build_tools(ctx)

    messages: list[Message] = []
    for previous_question, previous_answer in history:
        messages.append({"role": "user", "content": previous_question})
        messages.append({"role": "assistant", "content": previous_answer})
    # The date goes into the user turn (not the system prompt) so the system prompt stays
    # cacheable; the agent needs it for "from next Monday" in leave requests.
    today = today or date.today()
    messages.append({"role": "user", "content": f"(Today is {today.isoformat()}.)\n{question}"})

    run = llm.run_agent(AGENT_SYSTEM_PROMPT, messages, tools, max_steps=max_steps)
    citations, invalid = extract_citations(run.text, ctx.retrieved)
    text = run.text
    if run.stop_reason == "refusal" and not text:
        text = "The model declined to answer this question."

    return Answer(
        question=question,
        text=text,
        citations=citations,
        retrieved=list(ctx.retrieved),
        provider=llm.name,
        model=run.model,
        latency_s=time.perf_counter() - started,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        stop_reason=run.stop_reason,
        invalid_citations=invalid,
        tool_calls=run.tool_calls,
    )


__all__ = [
    "AGENT_SYSTEM_PROMPT",
    "Escalator",
    "JsonlFile",
    "LeaveRequestSink",
    "Recorder",
    "Tool",
    "ToolContext",
    "WebhookEscalator",
    "build_tools",
    "execute_tool",
    "run_agent",
]
