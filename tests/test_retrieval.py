import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from docassist.ingest import ingest
from docassist.models import Chunk
from docassist.retrieval import (
    EmbedderMismatchError,
    FakeEmbedder,
    OpenAIEmbedder,
    VectorStore,
    retrieve,
)

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


def chunk(source: str, page: int, i: int, text: str, doc_id: str = "doc1") -> Chunk:
    return Chunk(f"{source}::p{page}::{i}", doc_id, source, page, text)


@pytest.fixture
def store(tmp_path):
    return VectorStore(tmp_path / "index", FakeEmbedder())


# --- FakeEmbedder ------------------------------------------------------------------------


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embedder_is_deterministic_and_normalized():
    embedder = FakeEmbedder(dimensions=64)
    [a] = embedder.embed(["Anzugsmoment M8"])
    [b] = FakeEmbedder(dimensions=64).embed(["Anzugsmoment M8"])

    assert a == b
    assert len(a) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0)


def test_fake_embedder_similar_texts_are_closer():
    query, related, unrelated = FakeEmbedder().embed(
        ["godišnji odmor dana", "Godišnji odmor traje 20 radnih dana", "Kantina radi do 13:30"]
    )

    assert cosine(query, related) > cosine(query, unrelated)


def test_fake_embedder_handles_empty_text():
    [vector] = FakeEmbedder(dimensions=8).embed([""])

    assert vector == [1.0] + [0.0] * 7


# --- OpenAIEmbedder (fake client, no network) --------------------------------------------


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, model, input):
        self.calls.append((model, list(input)))
        # Return items out of order to check that the embedder sorts by index.
        data = [
            SimpleNamespace(index=i, embedding=[float(len(text))]) for i, text in enumerate(input)
        ]
        return SimpleNamespace(data=list(reversed(data)))


def test_openai_embedder_batches_and_keeps_order():
    client = FakeOpenAIClient()
    embedder = OpenAIEmbedder("text-embedding-3-small", client=client, batch_size=2)

    vectors = embedder.embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert vectors == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert [len(batch) for _, batch in client.calls] == [2, 2, 1]
    assert {model for model, _ in client.calls} == {"text-embedding-3-small"}
    assert embedder.name == "openai:text-embedding-3-small"


# --- VectorStore -------------------------------------------------------------------------


def test_empty_store_returns_no_results(store):
    assert store.count() == 0
    assert store.search("anything") == []
    assert store.documents() == []


def test_add_and_search(store):
    added = store.add_document(
        [
            chunk("hr.pdf", 1, 0, "Godišnji odmor traje najmanje 20 radnih dana."),
            chunk("hr.pdf", 2, 0, "Kantina radi od 10:30 do 13:30."),
            chunk("hr.pdf", 3, 0, "Bolovanje se prijavljuje rukovodiocu do 09:00."),
        ]
    )

    results = store.search("Koliko dana traje godišnji odmor?", k=2)

    assert added == 3
    assert len(results) == 2
    assert results[0].chunk == chunk(
        "hr.pdf", 1, 0, "Godišnji odmor traje najmanje 20 radnih dana."
    )
    assert results[0].score > results[1].score
    assert -1.0 <= results[1].score <= 1.0


def test_k_larger_than_index(store):
    store.add_document([chunk("a.txt", 1, 0, "one")])

    assert len(store.search("one", k=10)) == 1


def test_same_document_is_not_embedded_twice(store):
    chunks = [chunk("a.txt", 1, 0, "text")]

    assert store.add_document(chunks) == 1
    assert store.add_document(chunks) == 0
    assert store.count() == 1


def test_new_version_replaces_old_one(store):
    store.add_document([chunk("a.txt", 1, i, f"old {i}", doc_id="v1") for i in range(3)])
    store.add_document([chunk("a.txt", 1, 0, "new", doc_id="v2")])

    [doc] = store.documents()
    assert (doc.doc_id, doc.chunks) == ("v2", 1)
    assert store.search("old", k=5)[0].chunk.text == "new"


def test_add_document_rejects_mixed_documents(store):
    with pytest.raises(ValueError):
        store.add_document([chunk("a.txt", 1, 0, "x"), chunk("b.txt", 1, 0, "y")])


def test_documents_and_delete(store):
    store.add_document([chunk("b.pdf", p, 0, f"page {p}", doc_id="B") for p in (1, 2)])
    store.add_document([chunk("a.pdf", 1, i, f"chunk {i}", doc_id="A") for i in range(3)])

    docs = store.documents()
    assert [(d.source, d.doc_id, d.chunks, d.pages) for d in docs] == [
        ("a.pdf", "A", 3, 1),
        ("b.pdf", "B", 2, 2),
    ]

    assert store.delete_document("A") == 3
    assert store.delete_document("A") == 0
    assert [d.source for d in store.documents()] == ["b.pdf"]


def test_index_persists_between_instances(tmp_path):
    VectorStore(tmp_path, FakeEmbedder()).add_document([chunk("a.txt", 1, 0, "persisted")])

    reopened = VectorStore(tmp_path, FakeEmbedder())

    assert reopened.search("persisted")[0].chunk.text == "persisted"


def test_embedder_mismatch_is_detected(tmp_path):
    VectorStore(tmp_path, FakeEmbedder(dimensions=512))

    with pytest.raises(EmbedderMismatchError, match="Rebuild"):
        VectorStore(tmp_path, FakeEmbedder(dimensions=128))


def test_rebuild_clears_the_index(tmp_path):
    VectorStore(tmp_path, FakeEmbedder()).add_document([chunk("a.txt", 1, 0, "old")])

    rebuilt = VectorStore(tmp_path, FakeEmbedder(dimensions=128), rebuild=True)

    assert rebuilt.count() == 0


# --- retrieve ----------------------------------------------------------------------------


def test_retrieve_on_sample_docs(store):
    for path in sorted(SAMPLE_DOCS.iterdir()):
        store.add_document(ingest(path))

    results = retrieve(store, "Anzugsmoment Sammelschienen M8 Schrauben", k=3)

    assert len(results) == 3
    top = results[0].chunk
    assert (top.source, top.page) == ("montageanleitung_pv400.pdf", 2)


def test_retrieve_min_score_filters_weak_matches(store):
    store.add_document([chunk("a.txt", 1, 0, "torque values"), chunk("a.txt", 2, 0, "canteen")])

    results = retrieve(store, "torque values", k=2, min_score=0.5)

    assert [r.chunk.text for r in results] == ["torque values"]
