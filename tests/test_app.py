"""Streamlit UI smoke tests with offline providers (no browser, no network)."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "TOP_K"):
        monkeypatch.setenv(key, "")  # set empty so a local .env cannot fill them in
    st.cache_resource.clear()  # settings and store are cached per process
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    yield at
    st.cache_resource.clear()


def test_starts_empty(app):
    assert not app.exception
    assert app.title[0].value == "📄 Doc Assistant"
    assert any("No documents yet" in c.value for c in app.sidebar.caption)
    labels = app.sidebar.radio(key="provider").options
    assert labels[0].endswith("— no key")  # Claude without a key
    assert app.sidebar.radio(key="provider").value == "fake"


def test_load_demo_and_ask(app):
    app.sidebar.button(key="load_demo").click().run()
    assert not app.exception
    assert len([b for b in app.sidebar.button if b.key.startswith("delete_")]) == 5

    app.chat_input[0].set_value("Koliko dana traje godišnji odmor?").run()

    assert not app.exception
    assert app.chat_message[0].name == "user"
    assistant = app.chat_message[1]
    assert "[pravilnik_o_radu.pdf, str. 2]" in assistant.markdown[0].value
    assert assistant.expander[0].label == "📄 pravilnik_o_radu.pdf — str. 2"
    assert "Offline demo" in assistant.caption[0].value


def test_delete_document(app):
    app.sidebar.button(key="load_demo").click().run()
    delete = next(b for b in app.sidebar.button if b.key.startswith("delete_"))

    delete.click().run()

    assert len([b for b in app.sidebar.button if b.key.startswith("delete_")]) == 4


def test_missing_key_shows_error(app):
    app.sidebar.button(key="load_demo").click().run()
    app.sidebar.radio(key="provider").set_value("claude").run()

    app.chat_input[0].set_value("Pitanje?").run()

    assert not app.exception
    assert "ANTHROPIC_API_KEY" in app.error[0].value


def test_public_demo_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("ALLOW_UPLOADS", "false")
    monkeypatch.setenv("MAX_QUESTIONS_PER_SESSION", "1")
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "TOP_K"):
        monkeypatch.setenv(key, "")
    st.cache_resource.clear()
    try:
        at = AppTest.from_file(str(APP), default_timeout=60)
        at.run()

        # Demo documents are indexed automatically; no upload, demo or delete buttons.
        assert not at.exception
        assert len(at.sidebar.button) == 0
        assert any("faq.txt" in m.value for m in at.sidebar.markdown)

        at.chat_input[0].set_value("Kada radi kantina?").run()
        at.run()  # the next rerun (any interaction) shows the input as disabled
        assert at.chat_input[0].disabled
        assert "limit" in at.info[0].value
    finally:
        st.cache_resource.clear()


def test_agent_mode_shows_tool_calls(app):
    app.sidebar.button(key="load_demo").click().run()
    app.sidebar.toggle(key="agent_mode").set_value(True).run()

    app.chat_input[0].set_value("Kada radi kantina?").run()

    assert not app.exception
    assistant = app.chat_message[1]
    assert assistant.expander[0].label == "🛠 1 tool call(s)"
