"""Monitoring and follow-up of answers: feedback, forwarding to a team, statistics, the
question log and the gaps in the documentation."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query

from docassist.agent.tools import ToolError, new_escalation
from docassist.api.deps import LogDep, SettingsDep
from docassist.api.schemas import EscalateRequest, EscalateResponse, FeedbackRequest, fields_of
from docassist.api.security import ACCESS, ADMIN, ADMIN_CHANGES, EDITOR, RoleDep
from docassist.factory import build_escalator
from docassist.users import has_role

router = APIRouter(tags=["monitoring"])

RATINGS = (1, -1, 0)
LIST_LIMIT = 15
INSIGHT_FIELDS = (
    *("id", "created_at", "question", "found", "feedback", "latency_ms", "model"),
    *("mode", "cost_usd", "input_tokens", "output_tokens"),
)


@router.post("/feedback", dependencies=ACCESS)
def feedback(body: FeedbackRequest, log: LogDep) -> dict[str, bool]:
    if body.rating not in RATINGS:
        raise HTTPException(422, "rating must be 1, -1 or 0")
    if not log.set_feedback(body.log_id, body.rating or None):
        raise _no_query(body.log_id)
    return {"ok": True}


@router.post("/escalate", dependencies=ACCESS)
def escalate(body: EscalateRequest, settings: SettingsDep, log: LogDep) -> EscalateResponse:
    """Forward an unanswered question to a person (n8n webhook or the local outbox)."""
    query = log.get(body.log_id)
    if query is None:
        raise _no_query(body.log_id)
    if query["escalation"]:  # one e-mail per question, however often the button is clicked
        return EscalateResponse(reference=str(query["escalation"]), already_sent=True)
    reason = (
        "The documents have no answer to this question."
        if not query["found"]
        else "The user found the answer insufficient."
    )
    teams = {team.id: team for team in settings.escalation_teams}
    team_id = (body.team or settings.escalation_teams[0].id).lower()
    if team_id not in teams:
        raise HTTPException(422, f"Unknown team {body.team!r}. Use one of: {', '.join(teams)}")
    payload = new_escalation(
        str(query["question"]), reason, body.contact or "unknown", teams[team_id]
    )
    try:
        reference = build_escalator(settings).escalate(payload)
    except (ToolError, OSError) as exc:  # webhook unreachable, outbox not writable
        raise HTTPException(502, f"Could not forward the question: {exc}") from exc
    log.set_escalation(body.log_id, reference, team_id)
    return EscalateResponse(reference=reference, team=team_id)


@router.get("/stats", dependencies=ADMIN)
def stats(log: LogDep) -> dict[str, Any]:
    return fields_of(log.stats())


@router.get("/queries", dependencies=EDITOR)
def list_queries(
    log: LogDep,
    role: RoleDep,
    kind: Annotated[Literal["recent", "gaps"], Query(description="All questions or the gaps")],
    before: Annotated[int | None, Query(ge=1, description="Only older than this id")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """One page of the monitoring lists, newest first: `next` is the `before` of the next
    page, or None after the last one (the UI loads it when the list is scrolled down)."""
    if kind == "recent" and not has_role(role, "admin"):  # everyone's questions: usage
        raise HTTPException(403, "This needs the admin role")
    rows = (log.recent if kind == "recent" else log.gaps)(limit, before)
    items = _slim(rows)
    return {"items": items, "next": items[-1]["id"] if len(items) == limit else None}


@router.delete("/queries/{query_id}", dependencies=ADMIN_CHANGES)
def delete_query(query_id: int, log: LogDep) -> dict[str, bool]:
    """Remove one question from the log (monitoring lists and statistics)."""
    if not log.delete(query_id):
        raise _no_query(query_id)
    return {"ok": True}


@router.delete("/queries", dependencies=ADMIN_CHANGES)
def clear_queries(log: LogDep) -> dict[str, int]:
    """Remove every question from the log. Saved conversations are kept."""
    return {"deleted": log.clear()}


@router.get("/insights", dependencies=EDITOR)
def insights(log: LogDep, role: RoleDep) -> dict[str, Any]:
    """Monitoring: the gaps in the documentation, and for an administrator also the usage
    numbers and the latest questions (the first page of each; more with GET /queries)."""
    gaps = {"gaps": _slim(log.gaps(LIST_LIMIT))}
    if not has_role(role, "admin"):
        return gaps
    return {"stats": fields_of(log.stats()), "recent": _slim(log.recent(LIST_LIMIT)), **gaps}


def _slim(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{key: row.get(key) for key in INSIGHT_FIELDS} for row in rows]


def _no_query(log_id: int) -> HTTPException:
    return HTTPException(404, f"No query with log_id {log_id}")
