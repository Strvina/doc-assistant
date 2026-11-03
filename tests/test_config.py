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
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "EMBEDDING_MODEL",
        "INDEX_DIR",
        "TOP_K",
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
