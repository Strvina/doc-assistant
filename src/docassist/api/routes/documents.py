"""The document library: list, upload, delete, view and download documents."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from docassist.api.deps import SAMPLE_DOCS, LibraryDep, SettingsDep, VisionFactoryDep
from docassist.api.schemas import DocumentOut, IndexResult, PicturesResult, fields_of
from docassist.api.security import ACCESS, UPLOADS
from docassist.factory import MissingAPIKeyError
from docassist.ingest import DocumentLoadError
from docassist.ingest.loaders import IMAGE_EXTENSIONS, PROGRESS
from docassist.ingest.vision import VisionError
from docassist.rag import merge_passages
from docassist.viewer import is_cited, pdf_with_highlights, render_html

router = APIRouter(prefix="/documents", tags=["documents"])

NOT_FOUND = "Document or its original file not found"


@router.get("", dependencies=ACCESS)
def list_documents(library: LibraryDep) -> list[DocumentOut]:
    return [
        DocumentOut(
            **fields_of(d),
            has_file=(library.files_dir / d.source).is_file(),
            language=library.language(d),
        )
        for d in library.documents()
    ]


@router.post("", dependencies=UPLOADS)
def upload_documents(
    library: LibraryDep, settings: SettingsDep, files: Annotated[list[UploadFile], File()]
) -> list[IndexResult]:
    limit = settings.max_upload_mb * 1024 * 1024
    results = []
    for upload in files:
        name = upload.filename or "upload"
        data = upload.file.read(limit + 1)
        if len(data) > limit:
            results.append(
                IndexResult.failed(name, f"file is larger than {settings.max_upload_mb} MB")
            )
            continue
        try:
            added = library.add_bytes(name, data)
        except DocumentLoadError as exc:  # includes unsupported file types
            results.append(IndexResult.failed(name, str(exc)))
            continue
        results.append(IndexResult(**fields_of(added), status=added.status))
    return results


@router.get("/progress", dependencies=ACCESS)
def upload_progress(request: Request) -> list[dict[str, Any]]:
    """Documents being read right now: pages read so far (OCR or AI model), of how many;
    first the rebuild of a stale index, while it runs (ADR-032)."""
    state = request.app.state
    running = [("ocr", PROGRESS), ("vision", state.vision_progress)]
    items: list[dict[str, Any]] = [
        {"source": source, "done": done, "total": total, "kind": kind}
        for kind, progress in running
        for source, (done, total) in list(progress.items())
    ]
    rebuilding = state.rebuild
    if rebuilding is not None and not rebuilding.finished:
        item = {"source": rebuilding.current, "done": rebuilding.done, "kind": "reindex"}
        items.insert(0, {**item, "total": rebuilding.total})
    return items


@router.post("/demo", dependencies=UPLOADS)
def load_demo_documents(library: LibraryDep) -> list[IndexResult]:
    results = []
    for path in sorted(SAMPLE_DOCS.glob("*.*")):
        added = library.add_file(path)
        results.append(IndexResult(**fields_of(added), status=added.status))
    return results


@router.delete("/{doc_id}", dependencies=UPLOADS)
def delete_document(doc_id: str, library: LibraryDep) -> dict[str, int]:
    removed = library.delete(doc_id)
    if not removed:
        raise HTTPException(404, f"No document with id {doc_id}")
    return {"chunks_removed": removed}


@router.post("/{doc_id}/pictures", dependencies=UPLOADS)
def read_pictures(
    doc_id: str,
    request: Request,
    library: LibraryDep,
    settings: SettingsDep,
    vision_factory: VisionFactoryDep,
) -> PicturesResult:
    """Read the document's picture pages with the AI vision model, then index it again.

    Sends those pages to Google (Gemini), so the UI asks first (ADR-029).
    """
    found = library.original(doc_id)
    if found is None:
        raise HTTPException(404, NOT_FOUND)
    doc, _path = found
    try:
        read_image, model = vision_factory(settings)
    except MissingAPIKeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    progress = request.app.state.vision_progress
    try:
        result = library.read_pictures(
            doc_id,
            read_image,
            model,
            lambda done, total: progress.update({doc.source: (done, total)}),
        )
    except VisionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except DocumentLoadError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        progress.pop(doc.source, None)
    return PicturesResult(
        source=doc.source,
        pages_total=result.pages_total,
        pages_read=result.pages_read,
        chunks=len(library.store.document_chunks(doc.source)),
        model=model,
        error=result.error,
    )


@router.get("/{doc_id}/view", dependencies=ACCESS)
def view_document(
    doc_id: str,
    library: LibraryDep,
    page: Annotated[int | None, Query(ge=1)] = None,
    chunk: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    """The original document for the viewer, cited passage highlighted on `page`.

    `q` is the start of the cited text as it was shown. Chunk ids of a citation saved
    before the index was rebuilt can name other chunks now (ADR-032): then the chunks of
    the page that contain `q` are highlighted, or nothing, never another passage.
    """
    found = library.original(doc_id)
    if found is None:
        raise HTTPException(404, NOT_FOUND)
    doc, path = found
    passage = None
    if chunk:
        wanted = set(chunk)
        chunks = library.store.document_chunks(doc.source)
        passage = merge_passages([c for c in chunks if c.chunk_id in wanted]) or None
        if q and not (passage and is_cited(q, passage)):
            on_page = [c for c in chunks if c.page == page and is_cited(q, c.text)]
            passage = merge_passages(on_page) or None
    disposition = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.source)}"}
    if path.suffix.lower() in (".pdf", *IMAGE_EXTENSIONS):
        data = pdf_with_highlights(path, page, passage)
        return Response(data, media_type="application/pdf", headers=disposition)
    return HTMLResponse(render_html(path, page, passage))


@router.get("/{doc_id}/file", dependencies=ACCESS)
def download_document(doc_id: str, library: LibraryDep) -> FileResponse:
    found = library.original(doc_id)
    if found is None:
        raise HTTPException(404, NOT_FOUND)
    doc, path = found
    return FileResponse(path, filename=doc.source)
