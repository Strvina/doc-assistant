from docassist.models import Chunk
from docassist.retrieval.keywords import KeywordIndex, tokenize


def chunk(i, text):
    return Chunk(f"f.txt::p1::{i}", "d", "f.txt", 1, text)


def test_tokenize_folds_case_diacritics_and_inflection():
    assert tokenize("Kantina, KANTINE, kantini") == ["kantin", "kantin", "kantin"]
    assert tokenize("Šta je đak? Größe M8") == ["sta", "je", "djak", "grosse", "m8"]


def test_bm25_ranks_the_passage_with_the_rare_word_first():
    index = KeywordIndex(
        [
            chunk(0, "Radno vreme je od 7 do 15 časova, radi se ponedeljak do petak."),
            chunk(1, "Kantina radi od 10:30 do 13:30."),
            chunk(2, "Parking je iza hale B."),
        ]
    )

    results = index.search("Kada radi kantina?", k=3)

    assert results[0][0].chunk_id == "f.txt::p1::1"
    assert all(score > 0 for _, score in results)
    assert "f.txt::p1::2" not in {c.chunk_id for c, _ in results}  # no shared word


def test_no_match_gives_no_results():
    index = KeywordIndex([chunk(0, "Kantina radi od 10:30.")])
    assert index.search("Anzugsmoment", k=3) == []
    assert KeywordIndex([]).search("bilo šta", k=3) == []
