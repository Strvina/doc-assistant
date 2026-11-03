"""LLM providers behind one interface, plus the grounded-answer prompt."""

from docassist.llm.base import LLMError, LLMProvider, LLMResult, Message
from docassist.llm.fake import FakeProvider
from docassist.llm.prompts import NOT_FOUND_SR, SYSTEM_PROMPT, build_user_message

__all__ = [
    "NOT_FOUND_SR",
    "SYSTEM_PROMPT",
    "FakeProvider",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "Message",
    "build_user_message",
]
