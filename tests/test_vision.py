"""Reading picture pages with an AI vision model (ADR-029). The model is always a fake."""

import json

import pymupdf
import pytest

from docassist.ingest import load_document, vision
from docassist.library import Library
from docassist.llm.base import LLMError, LLMResult
from docassist.retrieval import FakeEmbedder, VectorStore

TABLE = "| Proizvod | Cena |\n| Crema e Aroma 1 kg | 2.490,00 RSD |"


@pytest.fixture(autouse=True)
def no_tesseract(monkeypatch):
    monkeypatch.setenv("OCR", "false")  # pages the model did not read stay without OCR


def picture_pdf(path):
    """Page 1 has text only; pages 2 and 3 are pictures (like a photographed price list)."""
    picture = pymupdf.open()
    picture.new_page(width=200, height=100).insert_text((20, 50), "picture")
    pixmap = picture[0].get_pixmap(dpi=72)
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Cenovnik 2026")
    for _ in range(2):
        page = pdf.new_page()
        page.insert_image(page.rect, pixmap=pixmap)
    pdf.save(path)
    return path


class FakeReader:
    def __init__(self, texts, fail_after=None):
        self.texts = list(texts)
        self.fail_after = fail_after
        self.calls = []

    def __call__(self, png):
        assert png.startswith(b"\x89PNG")
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise LLMError("Gemini: free-tier limit reached, try again later")
        self.calls.append(png)
        return self.texts.pop(0)


def test_only_picture_pages_are_read_and_replace_their_text(tmp_path):
    path = picture_pdf(tmp_path / "cenovnik.pdf")
    reader = FakeReader([TABLE, "Ristretto — 1.099,00 RSD"])
    progress = []

    result = vision.read_document(path, reader, "fake-model", lambda *p: progress.append(p))

    assert result == vision.VisionResult(pages_total=2, pages_read=2)
    assert len(reader.calls) == 2
    assert progress == [(0, 2), (1, 2), (2, 2)]
    pages = {p.page_no: p.text for p in load_document(path)}
    assert pages == {1: "Cenovnik 2026", 2: TABLE, 3: "Ristretto — 1.099,00 RSD"}
    saved = json.loads(vision.sidecar_path(path).read_text(encoding="utf-8"))
    assert saved["model"] == "fake-model"
    assert saved["pages"] == {"2": TABLE, "3": "Ristretto — 1.099,00 RSD"}


def test_limit_halfway_keeps_what_was_read_and_continues_later(tmp_path):
    path = picture_pdf(tmp_path / "cenovnik.pdf")

    first = vision.read_document(path, FakeReader([TABLE], fail_after=1), "m")

    assert first.pages_read == 1 and first.pages_total == 2
    assert "limit" in first.error
    assert {p.page_no for p in load_document(path)} == {1, 2}  # page 3: no OCR in this test

    reader = FakeReader(["Ristretto"])
    second = vision.read_document(path, reader, "m")

    assert second == vision.VisionResult(pages_total=2, pages_read=2)
    assert len(reader.calls) == 1  # page 2 is not sent again


def test_text_for_another_file_with_the_same_name_is_ignored(tmp_path):
    path = picture_pdf(tmp_path / "cenovnik.pdf")
    vision.read_document(path, FakeReader([TABLE, "x"]), "m")

    with pymupdf.open(path) as pdf:  # the file is replaced under the same name
        pdf[0].insert_text((72, 120), "Nova verzija")
        path.write_bytes(pdf.tobytes())

    assert vision.load_pages(path) == {}
    assert {p.page_no for p in load_document(path)} == {1}


def test_broken_sidecar_is_ignored(tmp_path):
    path = picture_pdf(tmp_path / "cenovnik.pdf")
    vision.sidecar_path(path).write_text("{not json", encoding="utf-8")

    assert vision.load_pages(path) == {}


def test_only_pdfs_and_images(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("text", encoding="utf-8")

    with pytest.raises(vision.VisionError):
        vision.read_document(path, FakeReader([]), "m")


def test_llm_reader_sends_the_page_as_an_image():
    class Provider:
        def complete(self, system, messages):
            self.system, self.messages = system, messages
            return LLMResult(" text ", "m", 1, 1, "end")

    provider = Provider()

    assert vision.llm_reader(provider)(b"\x89PNG") == " text "
    content = provider.messages[0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Copy numbers" in provider.system


# --- library ---------------------------------------------------------------------------------


def test_library_indexes_the_document_again_and_delete_removes_the_text(tmp_path):
    library = Library(VectorStore(tmp_path / "index", FakeEmbedder()), tmp_path / "files")
    library.add_file(picture_pdf(tmp_path / "cenovnik.pdf"))
    doc = library.documents()[0]
    assert doc.pages == 1  # only the text page, no OCR

    result = library.read_pictures(doc.doc_id, FakeReader([TABLE, "Ristretto"]), "m")

    assert result.pages_read == 2
    chunks = library.store.document_chunks("cenovnik.pdf")
    assert {c.page for c in chunks} == {1, 2, 3}
    assert any("Crema e Aroma 1 kg | 2.490,00 RSD" in c.text for c in chunks)

    sidecar = vision.sidecar_path(tmp_path / "files" / "cenovnik.pdf")
    assert sidecar.is_file()
    library.delete(doc.doc_id)
    assert not sidecar.exists()


def test_library_unknown_document(tmp_path):
    library = Library(VectorStore(tmp_path / "index", FakeEmbedder()), tmp_path / "files")

    with pytest.raises(vision.VisionError):
        library.read_pictures("nope", FakeReader([]), "m")
