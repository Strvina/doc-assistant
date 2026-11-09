"""Questions: answered at once (/ask) or as the model writes (/ask/stream), and cancelled."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from docassist.agent import run_agent
from docassist.api.deps import (
    ConversationsDep,
    LLMFactory,
    LLMFactoryDep,
    LogDep,
    SettingsDep,
    StoreDep,
)
from docassist.api.schemas import AskRequest, AskResponse, CancelRequest, to_response
from docassist.api.security import ACCESS, ClientDep, OwnerDep, UserDep, check_limits
from docassist.config import Settings
from docassist.conversations import ConversationStore
from docassist.factory import MissingAPIKeyError, build_tool_context
from docassist.llm import LLMError, LLMProvider
from docassist.logs import QueryLog
from docassist.rag import Answer, answer, answer_stream
from docassist.retrieval import VectorStore
from docassist.users import User

router = APIRouter(prefix="/ask", tags=["questions"])

MAX_CANCELLED_IDS = 1000  # ids of requests that ended before their cancel arrived


@router.post("")
def ask(
    body: AskRequest,
    request: Request,
    kind: ClientDep,
    who: OwnerDep,
    user: UserDep,
    settings: SettingsDep,
    store: StoreDep,
    log: LogDep,
    conversations: ConversationsDep,
    llm_factory: LLMFactoryDep,
) -> AskResponse:
    llm, history = _prepare(body, request, kind, who, user, settings, conversations, llm_factory)
    scope = _scope(body, store)
    try:
        if body.agent:
            result = _run_agent(body, settings, store, llm, history, scope)
        else:
            k = _top_k(body, settings)
            doc_ids = None if scope is None else scope.keys()
            result = answer(body.question, store, llm, k=k, history=history, doc_ids=doc_ids)
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    response = _finish(result, body, request, kind, who, log, conversations, scope)
    if response is None:
        raise HTTPException(409, "The question was cancelled")
    return response


@router.post("/stream", response_class=StreamingResponse)
def ask_stream(
    body: AskRequest,
    request: Request,
    kind: ClientDep,
    who: OwnerDep,
    user: UserDep,
    settings: SettingsDep,
    store: StoreDep,
    log: LogDep,
    conversations: ConversationsDep,
    llm_factory: LLMFactoryDep,
) -> StreamingResponse:
    """Like /ask, but the answer arrives as it is written: newline-delimited JSON events
    `{"type": "delta", "text": …}` …, then `{"type": "done", "response": <as /ask>}`
    (or `{"type": "error", "status", "message"}`). Agent mode sends only "done".

    If the client goes away (Stop button), the stream is closed: the model stops
    generating and nothing is saved.
    """
    llm, history = _prepare(body, request, kind, who, user, settings, conversations, llm_factory)
    scope = _scope(body, store)

    def events() -> Iterator[bytes]:
        result: Answer | None = None
        try:
            if body.agent:
                result = _run_agent(body, settings, store, llm, history, scope)
            else:
                stream = answer_stream(
                    body.question,
                    store,
                    llm,
                    k=_top_k(body, settings),
                    history=history,
                    doc_ids=None if scope is None else scope.keys(),
                )
                for item in stream:
                    if isinstance(item, str):
                        yield _event({"type": "delta", "text": item})
                    else:
                        result = item
        except LLMError as exc:
            yield _event({"type": "error", "status": 502, "message": str(exc)})
            return
        if result is None:  # the answer stream always ends with an Answer
            yield _event({"type": "error", "status": 500, "message": "no answer"})
            return
        response = _finish(result, body, request, kind, who, log, conversations, scope)
        if response is None:
            yield _event({"type": "error", "status": 409, "message": "cancelled"})
            return
        yield _event({"type": "done", "response": response.model_dump(mode="json")})

    # No buffering by proxies, so the text really arrives as it is written.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="application/x-ndjson", headers=headers)


@router.post("/cancel", dependencies=ACCESS)
def cancel(body: CancelRequest, request: Request) -> dict[str, bool]:
    """Cancel a question that is still being answered (its answer is then not saved)."""
    cancelled = request.app.state.cancelled
    if len(cancelled) > MAX_CANCELLED_IDS:
        cancelled.clear()
    cancelled.add(body.request_id)
    return {"ok": True}


# --- shared by /ask and /ask/stream ------------------------------------------------------


def _prepare(
    body: AskRequest,
    request: Request,
    kind: str,
    who: str,
    user: User | None,
    settings: Settings,
    conversations: ConversationStore,
    llm_factory: LLMFactory,
) -> tuple[LLMProvider, list[tuple[str, str]]]:
    """Checks before any model call; the model and the conversation so far."""
    if body.conversation_id and not conversations.exists(who, body.conversation_id):
        raise HTTPException(404, f"No conversation {body.conversation_id!r}")
    if kind != "api":
        check_limits(request, settings, user)
    history = list(body.history)
    if body.conversation_id and not history:  # follow-ups use the saved conversation
        history = conversations.history(who, body.conversation_id)
    try:
        return llm_factory(settings, body.provider), history
    except ValueError as exc:  # unknown provider name
        raise HTTPException(422, str(exc)) from exc
    except MissingAPIKeyError as exc:
        raise HTTPException(503, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc


def _top_k(body: AskRequest, settings: Settings) -> int:
    return body.top_k or settings.top_k


def _scope(body: AskRequest, store: VectorStore) -> dict[str, str] | None:
    """The documents the user chose to search (doc id -> file name), or None for all."""
    if body.doc_ids is None:
        return None
    names = {doc.doc_id: doc.source for doc in store.documents()}
    unknown = sorted(set(body.doc_ids) - names.keys())
    if unknown:
        raise HTTPException(422, f"No document with id {', '.join(unknown)}")
    return {doc_id: names[doc_id] for doc_id in body.doc_ids}


def _run_agent(
    body: AskRequest,
    settings: Settings,
    store: VectorStore,
    llm: LLMProvider,
    history: list[tuple[str, str]],
    scope: dict[str, str] | None,
) -> Answer:
    ctx = build_tool_context(settings, store)
    ctx.top_k = _top_k(body, settings)
    ctx.doc_ids = None if scope is None else frozenset(scope)
    return run_agent(body.question, ctx, llm, history=history)


def _finish(
    result: Answer,
    body: AskRequest,
    request: Request,
    kind: str,
    who: str,
    log: QueryLog,
    conversations: ConversationStore,
    scope: dict[str, str] | None,
) -> AskResponse | None:
    """Log the answer and save it in the conversation; None if the user cancelled it."""
    cancelled = request.app.state.cancelled
    if body.request_id and body.request_id in cancelled:
        # Cancelled while the model was answering: nothing is saved or logged.
        cancelled.discard(body.request_id)
        return None
    response = to_response(result)
    response.searched_documents = sorted(scope.values(), key=str.lower) if scope else []
    channel = "web" if kind == "web" else "api"
    mode = "agent" if body.agent else "rag"
    response.log_id = log.log(result, channel=channel, mode=mode)
    conversation_id = body.conversation_id or conversations.create(who, body.question)
    response.conversation_id = conversation_id
    conversations.add_turn(
        who, conversation_id, body.question, mode, response.model_dump(mode="json")
    )
    return response


def _event(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False) + "\n").encode()
