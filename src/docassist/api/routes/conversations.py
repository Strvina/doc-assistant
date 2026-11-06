"""Saved conversations (ADR-025): list and search, open, rename, export, delete."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response

from docassist import export
from docassist.api.deps import ConversationsDep
from docassist.api.schemas import RenameRequest
from docassist.api.security import OwnerDep

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def list_conversations(
    who: OwnerDep,
    conversations: ConversationsDep,
    q: Annotated[str, Query(max_length=200, description="Search titles, questions, answers")] = "",
) -> list[dict[str, Any]]:
    """Your saved conversations, most recently used first."""
    return conversations.summaries(who, query=q)


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: str, body: RenameRequest, who: OwnerDep, conversations: ConversationsDep
) -> dict[str, bool]:
    if not conversations.rename(who, conversation_id, body.title):
        raise _not_found(conversation_id)
    return {"ok": True}


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str, who: OwnerDep, conversations: ConversationsDep
) -> dict[str, Any]:
    found = conversations.get(who, conversation_id)
    if found is None:
        raise _not_found(conversation_id)
    return found


@router.get("/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    who: OwnerDep,
    conversations: ConversationsDep,
    fmt: Annotated[
        str, Query(alias="format", pattern="^(pdf|docx)$", description="pdf | docx")
    ] = "pdf",
) -> Response:
    """The conversation as a PDF or Word file: questions, answers and cited passages."""
    found = conversations.get(who, conversation_id)
    if found is None:
        raise _not_found(conversation_id)
    return Response(
        export.export(found, fmt),
        media_type=export.FORMATS[fmt],
        headers={"Content-Disposition": export.content_disposition(found, fmt)},
    )


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str, who: OwnerDep, conversations: ConversationsDep
) -> dict[str, bool]:
    if not conversations.delete(who, conversation_id):
        raise _not_found(conversation_id)
    return {"ok": True}


def _not_found(conversation_id: str) -> HTTPException:
    return HTTPException(404, f"No conversation {conversation_id!r}")
