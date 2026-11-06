from pathlib import Path

import pytest

from docassist.agent.prompts import AGENT_SYSTEM_PROMPT
from docassist.ingest import ingest
from docassist.llm import NOT_FOUND_SR, SYSTEM_PROMPT, FakeProvider, LLMResult
from docassist.llm.prompts import LIST_RULE
from docassist.models import Chunk
from docassist.rag import answer, context_passages, extract_citations, merge_passages
from docassist.retrieval import FakeEmbedder, SearchResult, VectorStore

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


@pytest.fixture
def store(tmp_path):
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    for path in sorted(SAMPLE_DOCS.iterdir()):
        store.add_document(ingest(path))
    return store


def result(source, page, text="text", score=0.5):
    return SearchResult(Chunk(f"{source}::p{page}::0", "d", source, page, text), score)


# --- extract_citations -------------------------------------------------------------------


def test_extract_valid_invalid_and_duplicate_citations():
    retrieved = [result("pravilnik_o_radu.pdf", 2), result("faq.txt", 1)]
    text = (
        "Odmor traje 20 dana [pravilnik_o_radu.pdf, str. 2]. Zahtev se podnosi preko portala "
        "[pravilnik_o_radu.pdf, str. 2][faq.txt, str. 1]. Izmišljeno [nepostojeci.pdf, str. 9]."
    )

    citations, invalid = extract_citations(text, retrieved)

    assert [(c.source, c.page) for c in citations] == [
        ("pravilnik_o_radu.pdf", 2),
        ("faq.txt", 1),
    ]
    assert invalid == ["[nepostojeci.pdf, str. 9]"]
    assert citations[0].chunks[0].source == "pravilnik_o_radu.pdf"


@pytest.mark.parametrize(
    "citation",
    ["[Manual.PDF, str. 2]", "[manual.pdf, p. 2]", "[manual.pdf, S. 2]", "[ manual.pdf , str 2 ]"],
)
def test_citation_format_variants(citation):
    citations, invalid = extract_citations(f"Fact {citation}.", [result("manual.pdf", 2)])

    assert [(c.source, c.page) for c in citations] == [("manual.pdf", 2)]
    assert invalid == []


def test_several_sources_in_one_bracket_and_any_extension():
    retrieved = [result("parts.xlsx", 2), result("Cenovnik 2026. (v2).pdf", 3)]
    text = (
        "Okvir košta 23900 [parts.xlsx, str. 2; Cenovnik 2026. (v2).pdf, str. 3; x.pptx, str. 1]."
    )

    citations, invalid = extract_citations(text, retrieved)

    assert [(c.source, c.page) for c in citations] == [
        ("parts.xlsx", 2),
        ("Cenovnik 2026. (v2).pdf", 3),
    ]
    assert invalid == ["[x.pptx, str. 1]"]


@pytest.mark.parametrize(
    "text", ["[see manual.pdf]", "[manual.pdf, str. 2; not a citation]", "[v1.2, page 3]"]
)
def test_brackets_that_are_not_citations(text):
    assert extract_citations(text, [result("manual.pdf", 2)]) == ([], [])


def test_citation_to_wrong_page_is_invalid():
    citations, invalid = extract_citations("x [manual.pdf, str. 3]", [result("manual.pdf", 2)])

    assert citations == []
    assert invalid == ["[manual.pdf, str. 3]"]


# --- answer ------------------------------------------------------------------------------


def test_answer_with_fake_provider(store):
    llm = FakeProvider()

    result = answer("Koliko dana traje godišnji odmor?", store, llm, k=3)

    assert result.found
    assert result.citations[0].source == "pravilnik_o_radu.pdf"
    assert result.citations[0].page == 2
    assert "radnih dana" in result.text
    assert result.provider == "fake"
    hits = [r for r in result.retrieved if not r.neighbor]
    assert len(hits) == 3
    assert all(r.chunk.page in {h.chunk.page for h in hits} for r in result.retrieved)
    assert result.latency_s >= 0
    assert result.input_tokens > 0

    [(system, messages)] = llm.calls
    assert system == SYSTEM_PROMPT
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"].endswith("Question: Koliko dana traje godišnji odmor?")
    pages = {(r.chunk.source, r.chunk.page) for r in result.retrieved}
    assert messages[-1]["content"].count("<document ") == len(pages)  # one per page


def test_answer_not_found_has_no_citations(store):
    result = answer("Šta je kvantna gravitacija?", store, FakeProvider(NOT_FOUND_SR))

    assert not result.found
    assert result.text == NOT_FOUND_SR


def test_empty_index_skips_the_llm(tmp_path):
    llm = FakeProvider()
    empty = VectorStore(tmp_path / "empty", FakeEmbedder())

    result = answer("Bilo šta?", empty, llm)

    assert result.text == NOT_FOUND_SR
    assert llm.calls == []


def test_min_score_can_filter_everything(store):
    llm = FakeProvider()

    result = answer("Bilo šta?", store, llm, min_score=1.01)

    assert not result.found and llm.calls == []


def test_history_is_sent_before_the_question(store):
    llm = FakeProvider()

    answer(
        "A za roditelje?",
        store,
        llm,
        history=[("Koliko traje odmor?", "20 dana [pravilnik_o_radu.pdf, str. 2]")],
    )

    messages = llm.calls[0][1]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "Koliko traje odmor?"
    assert "<documents>" not in messages[0]["content"]


def test_invalid_citations_are_reported(store):
    result = answer("Kantina?", store, FakeProvider("Radi do 13:30 [izmisljeno.pdf, str. 4]."))

    assert not result.found
    assert result.invalid_citations == ["[izmisljeno.pdf, str. 4]"]


def test_refusal_gets_a_readable_message(store):
    class Refusing(FakeProvider):
        def complete(self, system, messages):
            return LLMResult("", "m", 10, 0, "refusal")

    result = answer("Pitanje?", store, Refusing())

    assert result.stop_reason == "refusal"
    assert "declined" in result.text


def test_merge_passages_removes_overlap():
    def c(index, text):
        return Chunk(f"x.pdf::p1::{index}", "d", "x.pdf", 1, text)

    first = c(0, "Prvi deo teksta. Ovo je zajednički deo koji se ponavlja.")
    second = c(1, "Ovo je zajednički deo koji se ponavlja. Drugi deo.")
    far = c(5, "Daleki pasus.")

    assert merge_passages([second, first]) == (
        "Prvi deo teksta. Ovo je zajednički deo koji se ponavlja. Drugi deo."
    )
    assert merge_passages([first, far]).endswith("[…]\n\nDaleki pasus.")


def test_merge_passages_joins_neighbours_and_drops_the_repeated_heading():
    def c(index, text):
        return Chunk(f"x.pdf::p2::{index}", "d", "x.pdf", 2, text)

    merged = merge_passages(
        [c(2, "Kategorije :\n2) Hrana."), c(0, "Uvod."), c(1, "Kategorije :\n1) Imunizacija.")]
    )

    assert merged == "Uvod.\n\nKategorije :\n1) Imunizacija.\n\n2) Hrana."


def test_context_has_one_passage_per_page_in_the_order_of_the_best_hit():
    def r(page, index, text, score=0.5):
        return SearchResult(Chunk(f"x.pdf::p{page}::{index}", "d", "x.pdf", page, text), score)

    results = [r(5, 0, "Pet."), r(2, 1, "Dva b."), r(5, 1, "Pet b."), r(2, 0, "Dva a.")]

    passages = context_passages(results)

    assert [(p.page, p.text) for p in passages] == [(5, "Pet.\n\nPet b."), (2, "Dva a.\n\nDva b.")]


def test_the_model_gets_the_whole_list_around_a_hit(tmp_path):
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    texts = ["Kategorije bolesti :\n1) Imunizacija.", "2) Hrana.", "3) Vazduh.", "4) Zoonoze."]
    store.add_document([Chunk(f"z.pdf::p2::{i}", "d", "z.pdf", 2, t) for i, t in enumerate(texts)])
    llm = FakeProvider()

    answer("Kategorije bolesti", store, llm, k=1)

    [(_, messages)] = llm.calls
    assert messages[-1]["content"].count("<document ") == 1
    assert all(t.splitlines()[-1] in messages[-1]["content"] for t in texts)


def test_prompts_ask_for_whole_lists():
    assert LIST_RULE in SYSTEM_PROMPT
    assert LIST_RULE in AGENT_SYSTEM_PROMPT
