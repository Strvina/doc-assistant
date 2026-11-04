"""Shared test setup: no test may touch the real data folders or use real API keys."""

import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Point every data path at a temporary folder and blank out keys from a local .env."""
    for name, value in {
        "INDEX_DIR": tmp_path / "index",
        "DOCUMENTS_DIR": tmp_path / "documents",
        "LOG_DB": tmp_path / "logs.sqlite",
        "OUTBOX_DIR": tmp_path / "outbox",
        "EMBEDDING_CACHE_DIR": tmp_path / "models",
    }.items():
        monkeypatch.setenv(name, str(value))
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_FALLBACK_MODEL",
        "API_TOKEN",
    ):
        monkeypatch.setenv(key, "")  # empty beats a value load_dotenv() would add
    return tmp_path
