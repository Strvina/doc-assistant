"""Answers that arrive as they are written: POST /ask/stream and provider streaming."""

import json
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient

from docassist.api import create_app
from docassist.config import Settings
from docassist.llm import FakeProvider, LLMError
from docassist.llm.base import LLMResult, stream_complete
from docassist.llm.chat_completions import ChatCompletionsProvider, Endpoint
from docassist.retrieval import FakeEmbedder, VectorStore


def make_client(monkeypatch, tmp_path, llm_factory=None):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    settings = Settings.from_env(load_dotenv_file=False)
    app = create_app(
        settings=replace(settings, api_token="secret"),
        store=VectorStore(tmp_path / "index", FakeEmbedder()),
        llm_factory=llm_factory or (lambda s, p: FakeProvider()),
    )
    client = TestClient(app)
    client.get("/")
    client.post("/documents/demo")
    return client


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_stream_sends_pieces_then_the_full_answer(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post("/ask/stream", json={"question": "Kada radi kantina?"})

    assert response.headers["content-type"].startswith("application/x-ndjson")
    sent = events(response)
    deltas = [e["text"] for e in sent if e["type"] == "delta"]
    done = sent[-1]
    assert len(deltas) > 1 and done["type"] == "done"
    assert "".join(deltas).strip() == done["response"]["answer"]
    assert done["response"]["citations"] and done["response"]["conversation_id"]
    [saved] = client.get("/conversations").json()
    assert saved["turns"] == 1


def test_agent_mode_sends_only_the_full_answer(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    sent = events(
        client.post("/ask/stream", json={"question": "Kada radi kantina?", "agent": True})
    )

    assert [e["type"] for e in sent] == ["done"]
    assert sent[0]["response"]["tool_calls"]


def test_model_error_is_an_error_event(monkeypatch, tmp_path):
    class Broken(FakeProvider):
        def stream(self, system, messages):
            yield "Počinjem"
            raise LLMError("Groq: the answer stopped halfway")

    client = make_client(monkeypatch, tmp_path, llm_factory=lambda s, p: Broken())

    sent = events(client.post("/ask/stream", json={"question": "Kada radi kantina?"}))

    assert sent[0] == {"type": "delta", "text": "Počinjem"}
    assert sent[-1]["type"] == "error" and sent[-1]["status"] == 502
    assert client.get("/conversations").json() == []


def test_cancelled_stream_is_not_saved(monkeypatch, tmp_path):
    def factory(s, p):
        client.post("/ask/cancel", json={"request_id": "r-9"})
        return FakeProvider()

    client = make_client(monkeypatch, tmp_path, llm_factory=factory)

    sent = events(client.post("/ask/stream", json={"question": "Greška", "request_id": "r-9"}))

    assert sent[-1] == {"type": "error", "status": 409, "message": "cancelled"}
    assert client.get("/conversations").json() == []


def test_checks_run_before_the_stream_starts(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    response = client.post("/ask/stream", json={"question": "x", "conversation_id": "nope"})
    assert response.status_code == 404


def test_providers_without_streaming_send_one_piece():
    class Plain:
        def complete(self, system, messages):
            return LLMResult("Ceo odgovor.", "m", 1, 1, "end")

    pieces = list(stream_complete(Plain(), "s", []))
    assert pieces[0] == "Ceo odgovor." and isinstance(pieces[1], LLMResult)


def test_chat_completions_stream_collects_text_and_usage():
    def chunk(text=None, finish=None, usage=None, choices=True):
        delta = SimpleNamespace(content=text)
        return SimpleNamespace(
            model="gpt-oss",
            usage=usage,
            choices=[SimpleNamespace(delta=delta, finish_reason=finish)] if choices else [],
        )

    closed = []

    class Stream(list):
        def close(self):
            closed.append(True)

    stream = Stream(
        [
            chunk("20 Nm "),
            chunk("【manual.pdf, str. 2】"),
            chunk(finish="stop"),
            chunk(usage=SimpleNamespace(prompt_tokens=90, completion_tokens=8), choices=False),
        ]
    )
    requests = []

    class Completions:
        def create(self, **request):
            requests.append(request)
            return stream

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    llm = ChatCompletionsProvider("groq", [Endpoint("groq", "m", client)], paused_until={})

    pieces = list(llm.stream("s", [{"role": "user", "content": "q"}]))

    assert pieces[:2] == ["20 Nm ", "[manual.pdf, str. 2]"]
    result = pieces[-1]
    assert result.text == "20 Nm [manual.pdf, str. 2]"
    assert (result.input_tokens, result.output_tokens, result.stop_reason) == (90, 8, "end")
    assert requests[0]["stream"] is True and requests[0]["stream_options"] == {
        "include_usage": True
    }
    assert closed == [True]
