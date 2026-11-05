"""Build the configured components from `Settings` (one place that knows about providers)."""

from __future__ import annotations

from docassist.agent.tools import Escalator, JsonlFile, ToolContext, WebhookEscalator
from docassist.config import Settings
from docassist.ingest.vision import ReadImage
from docassist.library import Library
from docassist.llm import FakeProvider, LLMProvider
from docassist.retrieval import Embedder, FakeEmbedder, LocalEmbedder, OpenAIEmbedder, VectorStore


class MissingAPIKeyError(RuntimeError):
    """A provider was selected but its API key is not configured."""


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "fake":
        return FakeEmbedder()
    if settings.embedding_provider == "local":
        return LocalEmbedder(settings.embedding_model, cache_dir=settings.embedding_cache_dir)
    if not settings.openai_api_key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set (needed for EMBEDDING_PROVIDER=openai). Add it to .env, "
            "or use EMBEDDING_PROVIDER=local (default, no key needed)."
        )
    return OpenAIEmbedder(settings.embedding_model, api_key=settings.openai_api_key)


def build_store(settings: Settings, *, rebuild: bool = False) -> VectorStore:
    return VectorStore(
        settings.index_dir,
        build_embedder(settings),
        rebuild=rebuild,
        hybrid=settings.hybrid_search,
    )


FREE_SERVICES = ("groq", "gemini")  # free tiers, OpenAI-compatible (ADR-021, ADR-022)
_KEY_HELP = {
    "gemini": "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com",
    "groq": "GROQ_API_KEY is not set. Get a free key at https://console.groq.com",
}


def build_free_llm(settings: Settings, service: str) -> LLMProvider:
    """The chosen free service first, then the other one as a backup when it has a key."""
    from docassist.llm.chat_completions import ChatCompletionsProvider, endpoint

    models = {
        "gemini": (
            settings.gemini_api_key,
            (settings.gemini_model, settings.gemini_fallback_model),
            8192,  # thinking models spend part of it on reasoning
        ),
        "groq": (
            settings.groq_api_key,
            (settings.groq_model, settings.groq_fallback_model),
            1536,  # input + this count against Groq's 8,000 tokens/minute per model
        ),
    }
    if not models[service][0]:
        raise MissingAPIKeyError(f"{_KEY_HELP[service]} and add it to .env.")
    endpoints = []
    for name in [service, *(s for s in FREE_SERVICES if s != service)]:
        key, names, max_tokens = models[name]
        if not key:
            continue
        client = None
        for model in filter(None, names):
            ep = endpoint(name, model, key, max_tokens=max_tokens, client=client)
            client = ep.client  # one HTTP client per service
            endpoints.append(ep)
    return ChatCompletionsProvider(service, endpoints)


def build_vision(settings: Settings) -> tuple[ReadImage, str]:
    """`read_image` for pictures in documents and a label of the models (ADR-029).

    Gemini only: its models read images, and its fallback model takes over when the main
    one is out of its daily quota. Groq's gpt-oss models read text only.
    """
    if not settings.gemini_api_key:
        raise MissingAPIKeyError(f"{_KEY_HELP['gemini']} and add it to .env.")
    from docassist.ingest.vision import llm_reader
    from docassist.llm.chat_completions import ChatCompletionsProvider, endpoint

    models = [m for m in (settings.gemini_model, settings.gemini_fallback_model) if m]
    endpoints, client = [], None
    for model in models:
        ep = endpoint("gemini", model, settings.gemini_api_key, max_tokens=8192, client=client)
        client = ep.client
        endpoints.append(ep)
    return llm_reader(ChatCompletionsProvider("gemini", endpoints)), " / ".join(models)


def build_llm(settings: Settings, provider: str | None = None) -> LLMProvider:
    """The configured LLM, or `provider` ("claude" | "openai" | "gemini" | "groq" | "fake")."""
    provider = provider or settings.llm_provider
    if provider == "fake":
        return FakeProvider()
    if provider == "claude":
        if not settings.anthropic_api_key:
            raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set. Add it to .env.")
        from docassist.llm.claude import ClaudeProvider

        return ClaudeProvider(
            settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            effort=settings.anthropic_effort,
        )
    if provider == "openai":
        if not settings.openai_api_key:
            raise MissingAPIKeyError("OPENAI_API_KEY is not set. Add it to .env.")
        if not settings.openai_model:
            raise MissingAPIKeyError("OPENAI_MODEL is not set. Add a current chat model to .env.")
        from docassist.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings.openai_model, api_key=settings.openai_api_key)
    if provider in FREE_SERVICES:
        return build_free_llm(settings, provider)
    raise ValueError(f"Unknown LLM provider {provider!r}")


def build_escalator(settings: Settings) -> Escalator:
    """Escalations go to the webhook (n8n) if configured, otherwise to a local file."""
    if settings.escalation_webhook_url:
        return WebhookEscalator(settings.escalation_webhook_url)
    return JsonlFile(settings.outbox_dir / "escalations.jsonl")


def build_tool_context(settings: Settings, store: VectorStore) -> ToolContext:
    """Everything the agent tools need."""
    return ToolContext(
        store=store,
        escalator=build_escalator(settings),
        leave_requests=JsonlFile(settings.outbox_dir / "leave_requests.jsonl"),
        top_k=settings.top_k,
        teams=settings.escalation_teams,
    )


def build_library(settings: Settings, *, rebuild: bool = False) -> Library:
    """The index plus the stored originals (for the viewer and downloads)."""
    return Library(build_store(settings, rebuild=rebuild), settings.documents_dir)
