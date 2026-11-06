import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from docassist.ingest import ingest
from docassist.models import Chunk
from docassist.retrieval import (
    EmbedderMismatchError,
    FakeEmbedder,
    LocalEmbedder,
    OpenAIEmbedder,
    SearchResult,
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


# --- LocalEmbedder (fake backend, no model download) -------------------------------------


class FakeFastembed:
    def __init__(self):
        self.calls = []

    def passage_embed(self, texts, batch_size):
        self.calls.append(("passage", list(texts), batch_size))
        return iter([[float(len(t)), 1.0] for t in texts])  # fastembed yields numpy arrays

    def query_embed(self, texts):
        self.calls.append(("query", list(texts)))
        return iter([[0.5, 0.5] for _ in texts])


def test_local_embedder_uses_passage_and_query_encoding():
    backend = FakeFastembed()
    embedder = LocalEmbedder("some/multilingual-model", backend=backend, batch_size=8)

    assert embedder.embed(["ab", "abc"]) == [[2.0, 1.0], [3.0, 1.0]]
    assert embedder.embed_query("pitanje") == [0.5, 0.5]
    assert backend.calls == [("passage", ["ab", "abc"], 8), ("query", ["pitanje"])]
    assert embedder.name == "local:some/multilingual-model"


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


def test_index_from_an_older_chunking_must_be_rebuilt(tmp_path, monkeypatch):
    import docassist.retrieval.store as store_module

    monkeypatch.setattr(store_module, "CHUNKING", "fixed-v1")
    VectorStore(tmp_path, FakeEmbedder()).add_document([chunk("a.txt", 1, 0, "old chunks")])
    monkeypatch.setattr(store_module, "CHUNKING", "sections-v2-ocr1")

    with pytest.raises(EmbedderMismatchError, match="chunking 'fixed-v1'.*--rebuild"):
        VectorStore(tmp_path, FakeEmbedder())
    assert VectorStore(tmp_path, FakeEmbedder(), rebuild=True).count() == 0


def test_rebuild_clears_the_index(tmp_path):
    VectorStore(tmp_path, FakeEmbedder()).add_document([chunk("a.txt", 1, 0, "old")])

    rebuilt = VectorStore(tmp_path, FakeEmbedder(dimensions=128), rebuild=True)

    assert rebuilt.count() == 0


# --- retrieve ----------------------------------------------------------------------------


def test_retrieve_on_sample_docs(store):
    for path in sorted(SAMPLE_DOCS.iterdir()):
        store.add_document(ingest(path))

    results = retrieve(store, "Anzugsmoment Sammelschienen M8 Schrauben", k=3)

    assert len(results) in (3, 4)  # k by meaning + at most one extra keyword hit
    top = results[0].chunk
    assert (top.source, top.page) == ("montageanleitung_pv400.pdf", 2)


def test_retrieve_min_score_filters_weak_matches(store):
    store.add_document([chunk("a.txt", 1, 0, "torque values"), chunk("a.txt", 2, 0, "canteen")])

    results = retrieve(store, "torque values", k=2, min_score=0.5)

    assert [r.chunk.text for r in results] == ["torque values"]


# --- hybrid search (ADR-023) -------------------------------------------------------------


class AxisEmbedder:
    """Puts each known text on its own axis, so the "meaning" ranking is fully scripted."""

    name = "axis"

    def __init__(self, axes: dict[str, int], query_vector: list[float]) -> None:
        self.axes, self.query_vector = axes, query_vector

    def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * len(self.query_vector)
            vector[self.axes.get(text, 0)] = 1.0
            vectors.append(vector)
        return vectors

    def embed_query(self, text):
        return self.query_vector


def hybrid_store(tmp_path, hybrid=True):
    texts = ["Radno vreme je od 7 do 15.", "Parking je iza hale B.", "Kantina radi od 10:30."]
    # By meaning the canteen passage is last; by keywords it is the only match.
    embedder = AxisEmbedder({t: i for i, t in enumerate(texts)}, [0.9, 0.4, 0.1])
    store = VectorStore(tmp_path / "hybrid", embedder, hybrid=hybrid)
    store.add_document([chunk("faq.txt", 1, i, t) for i, t in enumerate(texts)])
    return store


def test_hybrid_search_adds_the_top_keyword_hit(tmp_path):
    results = hybrid_store(tmp_path).search("Kada radi kantina?", k=1)

    assert [r.chunk.text for r in results] == [
        "Radno vreme je od 7 do 15.",
        "Kantina radi od 10:30.",
    ]
    assert results[0].keyword_rank is None
    assert results[1].keyword_rank == 1
    assert 0 < results[1].score < results[0].score  # cosine similarity, also for the extra hit


def test_keyword_hit_already_found_by_meaning_is_not_added_twice(tmp_path):
    results = hybrid_store(tmp_path).search("Kada radi kantina?", k=3)
    assert len(results) == 3


def test_hybrid_search_can_be_turned_off(tmp_path):
    results = hybrid_store(tmp_path, hybrid=False).search("Kada radi kantina?", k=1)
    assert [r.chunk.text for r in results] == ["Radno vreme je od 7 do 15."]


def test_keyword_index_sees_new_and_deleted_documents(tmp_path):
    store = hybrid_store(tmp_path)
    store.search("kantina", k=1)  # builds the keyword index
    store.delete_document("doc1")
    store.add_document([chunk("menu.txt", 1, 0, "Kantina: ručak 450 din.", doc_id="doc2")])

    results = store.search("kantina", k=1)

    assert {r.chunk.source for r in results} == {"menu.txt"}


# --- neighbouring chunks (ADR-030) -------------------------------------------------------


def list_document(store):
    texts = [
        "Uvodni pasus o zakonu.",
        "Kategorije :\n1) Imunizacija: tetanus.",
        "Kategorije :\n2) Hrana i voda: salmoneloza.",
        "Kategorije :\n4) Zoonoze: besnilo.",
        "Kategorije :\n6) Hepatitisi: hepatitis A.",
        "Kategorije :\n8) Ostale bolesti.",
        "Kategorije :\n9) Jedna previše.",
    ]
    chunks = [chunk("z.pdf", 2, i, text) for i, text in enumerate(texts)]
    chunks += [chunk("z.pdf", 1, 0, "Strana jedan."), chunk("z.pdf", 3, 0, "Strana tri.")]
    store.add_document(chunks)
    return {c.chunk_id: c for c in chunks}


def test_neighbors_add_the_chunks_around_a_hit_and_follow_a_list(store):
    chunks = list_document(store)
    hit = SearchResult(chunks["z.pdf::p2::1"], 0.9)

    results = store.with_neighbors([hit])

    assert results[0] == hit
    assert [r.chunk.chunk_id for r in results[1:]] == [
        "z.pdf::p2::0",  # before
        "z.pdf::p2::2",  # after
        "z.pdf::p2::3",  # the list goes on: up to LIST_FOLLOW more
        "z.pdf::p2::4",
        "z.pdf::p2::5",
    ]
    assert all(r.neighbor and r.score == 0.9 for r in results[1:])


def test_neighbors_stay_on_the_page_and_appear_once(store):
    chunks = list_document(store)
    hits = [SearchResult(chunks["z.pdf::p2::0"], 0.9), SearchResult(chunks["z.pdf::p2::2"], 0.8)]

    results = store.with_neighbors(hits, window=1)

    ids = [r.chunk.chunk_id for r in results]
    assert len(ids) == len(set(ids))
    assert {r.chunk.page for r in results} == {2}
    assert ids[:2] == ["z.pdf::p2::0", "z.pdf::p2::2"]  # hits first, in their order


def test_no_neighbors_when_turned_off(store):
    chunks = list_document(store)
    hits = [SearchResult(chunks["z.pdf::p2::1"], 0.9)]

    assert store.with_neighbors(hits, window=0) == hits
    assert store.with_neighbors([]) == []
