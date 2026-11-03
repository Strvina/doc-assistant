from pathlib import Path

import pytest

from docassist import __version__
from docassist.config import Settings


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_EFFORT",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL",
        "INDEX_DIR",
        "TOP_K",
        "ALLOW_UPLOADS",
        "MAX_QUESTIONS_PER_SESSION",
        "ESCALATION_WEBHOOK_URL",
        "OUTBOX_DIR",
        "API_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_version():
    assert __version__ == "0.1.0"


def test_defaults(clean_env):
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.llm_provider == "claude"
    assert settings.anthropic_api_key is None
    assert settings.index_dir == Path("data/index")
    assert settings.top_k == 5
    assert settings.embedding_provider == "openai"


def test_env_overrides(clean_env):
    clean_env.setenv("LLM_PROVIDER", "OpenAI")
    clean_env.setenv("TOP_K", "8")
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.llm_provider == "openai"
    assert settings.top_k == 8


def test_invalid_provider(clean_env):
    clean_env.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        Settings.from_env(load_dotenv_file=False)


def test_invalid_embedding_provider(clean_env):
    clean_env.setenv("EMBEDDING_PROVIDER", "word2vec")
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        Settings.from_env(load_dotenv_file=False)


def test_effort(clean_env):
    assert Settings.from_env(load_dotenv_file=False).anthropic_effort is None
    clean_env.setenv("ANTHROPIC_EFFORT", "Low")
    assert Settings.from_env(load_dotenv_file=False).anthropic_effort == "low"
    clean_env.setenv("ANTHROPIC_EFFORT", "extreme")
    with pytest.raises(ValueError, match="ANTHROPIC_EFFORT"):
        Settings.from_env(load_dotenv_file=False)


def test_blank_values_use_defaults(clean_env):
    for var in ("LLM_PROVIDER", "ANTHROPIC_MODEL", "INDEX_DIR", "TOP_K", "ANTHROPIC_API_KEY"):
        clean_env.setenv(var, "  ")
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.llm_provider == "claude"
    assert settings.anthropic_model == "claude-sonnet-5-5"
    assert settings.index_dir == Path("data/index")
    assert settings.top_k == 5
    assert settings.anthropic_api_key is None


def test_ui_settings(clean_env):
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.allow_uploads is True
    assert settings.max_questions_per_session == 30
    clean_env.setenv("ALLOW_UPLOADS", "false")
    clean_env.setenv("MAX_QUESTIONS_PER_SESSION", "3")
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.allow_uploads is False
    assert settings.max_questions_per_session == 3
