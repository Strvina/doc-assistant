"""Weekly report of the gaps in the documentation, one e-mail per team (ADR-035).

A gap is a question the documents did not answer, or an answer a user rated 👎. The report
groups the same question asked several times, gives every question to a team and writes the
e-mail for each team; an n8n workflow fetches it once a week and sends the e-mails.

Which team: the one the question was already forwarded to, else (optionally) the one an AI
model picks from the team names, else the first team (e.g. whoever maintains the documents).
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from docassist.config import Team
from docassist.llm import LLMError, LLMProvider

DEFAULT_DAYS = 7
MAX_QUESTIONS_TO_CLASSIFY = 100

# How a question got its team.
ESCALATED, AI, DEFAULT = "escalated", "ai", "default"

CLASSIFY_PROMPT = """\
You sort employee questions that the company documents could not answer. Give every question \
to the team that should add the missing information to the documents.

Teams (id: name):
{teams}

Answer only with a JSON object that maps each question number to a team id, for example \
{{"1": "{example}"}}. Use only the team ids above."""


@dataclass(frozen=True)
class Gap:
    # The question as it was asked the last time; a follow-up ("a u petu?") in the standalone
    # form the search used (ADR-031), so it makes sense without the conversation.
    question: str
    times: int
    last_asked: str  # ISO timestamp
    not_found: int  # times the documents had no answer
    not_helpful: int  # times the answer was rated 👎
    assigned_by: str = DEFAULT  # ESCALATED | AI | DEFAULT
    asked_as: str = ""  # the follow-up as the user wrote it, when `question` is its rewrite


@dataclass(frozen=True)
class TeamReport:
    team: Team
    gaps: list[Gap]
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class GapReport:
    start: date
    end: date
    teams: list[TeamReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(t.gaps) for t in self.teams)


Classifier = Callable[[Sequence[str], Sequence[Team]], dict[str, str]]


def build_report(
    rows: Iterable[dict[str, object]],
    teams: Sequence[Team],
    *,
    end: date,
    days: int = DEFAULT_DAYS,
    classify: Classifier | None = None,
) -> GapReport:
    """The report for the `days` up to `end` from the logged gap `rows` (QueryLog.gaps_since).

    `classify` gives unassigned questions to teams (see `llm_classifier`); questions it
    leaves out go to the first team.
    """
    if not teams:
        raise ValueError("A gap report needs at least one team")
    grouped = _group(rows)
    known = {team.id for team in teams}
    unassigned = [q for q, (_, team) in grouped.items() if team not in known]
    by_ai: dict[str, str] = {}
    if classify is not None and unassigned:
        by_ai = classify(unassigned[:MAX_QUESTIONS_TO_CLASSIFY], teams)

    per_team: dict[str, list[Gap]] = {team.id: [] for team in teams}
    for question, (gap, escalated_to) in grouped.items():
        if escalated_to in known:
            per_team[str(escalated_to)].append(_with(gap, ESCALATED))
        elif by_ai.get(question) in known:
            per_team[by_ai[question]].append(_with(gap, AI))
        else:
            per_team[teams[0].id].append(_with(gap, DEFAULT))

    start = end - timedelta(days=days - 1)
    reports = []
    for team in teams:
        # Most asked first; among equally often asked, the most recent first.
        latest_first = sorted(per_team[team.id], key=lambda g: g.last_asked, reverse=True)
        gaps = sorted(latest_first, key=lambda g: g.times, reverse=True)
        if gaps:
            reports.append(_team_report(team, gaps, start, end))
    return GapReport(start, end, reports)


def llm_classifier(llm: LLMProvider) -> Classifier:
    """A `Classifier` that asks the model; on any failure the questions stay unassigned."""

    def classify(questions: Sequence[str], teams: Sequence[Team]) -> dict[str, str]:
        listed = "\n".join(f"- {team.id}: {team.label}" for team in teams)
        system = CLASSIFY_PROMPT.format(teams=listed, example=teams[0].id)
        numbered = "\n".join(f"{n}. {q}" for n, q in enumerate(questions, start=1))
        try:
            reply = llm.complete(system, [{"role": "user", "content": numbered}]).text
        except LLMError:
            return {}
        picked = _json_object(reply)
        result = {}
        for n, question in enumerate(questions, start=1):
            team = str(picked.get(str(n), "")).strip().lower()
            if team:
                result[question] = team
        return result

    return classify


# --- grouping ----------------------------------------------------------------------------


def _group(rows: Iterable[dict[str, object]]) -> dict[str, tuple[Gap, str | None]]:
    """Gaps by question (the same question asked again counts once), oldest rows first."""
    groups: dict[str, tuple[Gap, str | None]] = {}
    for row in sorted(rows, key=lambda r: str(r["created_at"])):
        asked = str(row["question"]).strip()
        question = str(row.get("search_query") or asked).strip()
        key = _normalized(question)
        previous, team = groups.get(key, (None, None))
        gap = Gap(
            question=question,
            times=(previous.times if previous else 0) + 1,
            last_asked=str(row["created_at"]),
            not_found=(previous.not_found if previous else 0) + (not row["found"]),
            not_helpful=(previous.not_helpful if previous else 0) + (row["feedback"] == -1),
            asked_as=asked if _normalized(asked) != key else "",
        )
        escalated = row.get("escalation_team")
        groups[key] = (gap, str(escalated) if escalated else team)
    return {gap.question: (gap, team) for gap, team in groups.values()}


def _normalized(question: str) -> str:
    return re.sub(r"[\s?!.,;:]+", " ", question.lower()).strip()


def _with(gap: Gap, assigned_by: str) -> Gap:
    return replace(gap, assigned_by=assigned_by)


def _json_object(text: str) -> dict[str, object]:
    found = re.search(r"\{.*\}", text, re.DOTALL)
    if found is None:
        return {}
    try:
        value = json.loads(found.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


# --- the e-mail --------------------------------------------------------------------------


def _team_report(team: Team, gaps: list[Gap], start: date, end: date) -> TeamReport:
    period = f"{_day(start)} – {_day(end)}"
    count = len(gaps)
    noun = "pitanje" if _is_singular(count) else "pitanja"
    subject = f"[Doc Assistant] {team.label}: {count} {noun} bez odgovora ({period})"
    intro = (
        f"Zdravo, tim {team.label},\n\n"
        f"u periodu {period} zaposleni su postavili pitanja na koja Doc Assistant nije našao "
        "(dobar) odgovor u dokumentima:"
    )
    advice = (
        "Šta da uradite: dodajte u biblioteku Doc Assistant-a dokument koji odgovara na ova "
        "pitanja, ili dopunite postojeći. Sledeći put asistent zna odgovor."
    )
    signature = "— Doc Assistant (automatski nedeljni izveštaj)"
    lines = [intro, ""]
    for n, gap in enumerate(gaps, start=1):
        lines += [f"{n}. {gap.question}", f"   {_details(gap)}"]
    lines += ["", advice, "", signature]

    items = "".join(
        f"<li><b>{html.escape(gap.question)}</b><br><small>{html.escape(_details(gap))}</small></li>"
        for gap in gaps
    )
    body = (
        f"<p>{html.escape(intro).replace(chr(10), '<br>')}</p><ol>{items}</ol>"
        f"<p>{html.escape(advice)}</p><p><small>{html.escape(signature)}</small></p>"
    )
    return TeamReport(team, gaps, subject, "\n".join(lines), body)


def _details(gap: Gap) -> str:
    asked = f"pitano {gap.times} {'put' if _is_singular(gap.times) else 'puta'}"
    parts = [asked, f"poslednji put {_day(datetime.fromisoformat(gap.last_asked).date())}"]
    if gap.asked_as:
        parts.append(f"pitano kao nastavak razgovora: „{gap.asked_as}“")
    if gap.not_found:
        parts.append("nema u dokumentima")
    if gap.not_helpful:
        parts.append(f"odgovor ocenjen kao nekoristan ({gap.not_helpful}×)")
    if gap.assigned_by == ESCALATED:
        parts.append("već prosleđeno timu")
    return " · ".join(parts)


def _is_singular(count: int) -> bool:
    """Serbian takes the singular after 1, 21, 31 … (but not 11): "1 pitanje", "21 put"."""
    return count % 10 == 1 and count % 100 != 11


def _day(value: date) -> str:
    return f"{value.day}. {value.month}. {value.year}."
