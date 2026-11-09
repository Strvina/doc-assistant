"""Reports for automations: the weekly report of the gaps in the documentation (ADR-035)."""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from fastapi.responses import HTMLResponse

from docassist import reports
from docassist.api.deps import LLMFactoryDep, LogDep, SettingsDep
from docassist.api.security import EDITOR
from docassist.factory import MissingAPIKeyError
from docassist.llm import LLMError

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/gaps", dependencies=EDITOR, response_model=None)
def gap_report(
    log: LogDep,
    settings: SettingsDep,
    llm_factory: LLMFactoryDep,
    days: Annotated[int, Query(ge=1, le=90, description="How many days back")] = 7,
    classify: Annotated[
        bool, Query(description="Let the AI model give unassigned questions to teams")
    ] = False,
    fmt: Annotated[str, Query(alias="format", pattern="^(json|html)$")] = "json",
) -> dict[str, Any] | Response:
    """Questions without a (good) answer in the last `days`, grouped by team, with the
    e-mail for each team: the n8n workflow `gap_report_workflow.json` sends them weekly.
    `format=html` shows the same report as a page (preview in the web UI)."""
    now = datetime.now(UTC)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    classifier = None
    if classify:
        try:
            classifier = reports.llm_classifier(llm_factory(settings, None))
        except (MissingAPIKeyError, LLMError):
            classifier = None  # no model available: unassigned questions go to the first team
    report = reports.build_report(
        log.gaps_since(since),
        settings.escalation_teams,
        end=now.date(),
        days=days,
        classify=classifier,
    )
    if fmt == "html":
        return HTMLResponse(_page(report))
    return {
        "from": report.start.isoformat(),
        "to": report.end.isoformat(),
        "days": days,
        "total": report.total,
        "teams": [
            {
                "team": item.team.id,
                "label": item.team.label,
                "email": item.team.email,
                "count": len(item.gaps),
                "subject": item.subject,
                "text": item.text,
                "html": item.html,
                "gaps": [vars(gap) for gap in item.gaps],
            }
            for item in report.teams
        ],
    }


def _page(report: reports.GapReport) -> str:
    sections = "".join(
        f"<section><h2>{html.escape(item.subject)}</h2>"
        f"<p class='to'>Za: {html.escape(item.team.email or item.team.label)}</p>"
        f"{item.html}</section>"
        for item in report.teams
    )
    empty = "<p>Nema pitanja bez odgovora u ovom periodu.</p>"
    return (
        "<!doctype html><html lang='sr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Izveštaj o prazninama u dokumentaciji</title><style>"
        "body{font:15px/1.55 system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;"
        "color:#0f1b2b;background:#f4f6f9}section{background:#fff;border:1px solid #e2e8f0;"
        "border-radius:12px;padding:4px 20px 12px;margin-bottom:18px}h1{font-size:22px}"
        "h2{font-size:16px}.to,small{color:#64748b}li{margin-bottom:8px}"
        "</style></head><body><h1>Izveštaj o prazninama u dokumentaciji</h1>"
        f"{sections or empty}</body></html>"
    )
