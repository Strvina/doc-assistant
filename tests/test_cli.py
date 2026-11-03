from pathlib import Path

import pytest

from docassist import cli

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


@pytest.fixture
def offline_env(tmp_path, monkeypatch):
    """Fake embeddings and a temporary index; .env is ignored so no real keys are used."""
    monkeypatch.setattr(cli.Settings, "from_env", _from_env_without_dotenv)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TOP_K", raising=False)
    return tmp_path


_original_from_env = cli.Settings.from_env


def _from_env_without_dotenv(load_dotenv_file: bool = True):
    return _original_from_env(load_dotenv_file=False)


def test_index_search_docs_delete(offline_env, capsys):
    assert cli.main(["index", str(SAMPLE_DOCS)]) == 0
    out = capsys.readouterr().out
    assert "Index: 5 documents" in out

    assert cli.main(["index", str(SAMPLE_DOCS / "faq.txt")]) == 0
    assert "unchanged" in capsys.readouterr().out

    assert cli.main(["search", "Kada radi kantina", "-k", "2"]) == 0
    out = capsys.readouterr().out
    assert "[faq.txt, p. 1]" in out
    assert out.count("score") == 2

    assert cli.main(["docs"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 5
    faq_id = next(line.split()[0] for line in lines if line.endswith("faq.txt"))

    assert cli.main(["delete", faq_id]) == 0
    assert cli.main(["delete", faq_id]) == 1


def test_index_reports_bad_files(offline_env, capsys):
    broken = offline_env / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    assert cli.main(["index", str(broken)]) == 1
    assert "FAILED" in capsys.readouterr().err


def test_missing_openai_key_is_a_clear_error(offline_env, monkeypatch, capsys):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")

    assert cli.main(["docs"]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
