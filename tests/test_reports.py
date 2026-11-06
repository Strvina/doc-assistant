"""The weekly report of the gaps in the documentation (ADR-035)."""

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from docassist.api import create_app
from docassist.config import Settings, Team
from docassist.llm import FakeProvider, LLMError
from docassist.logs import QueryLog
from docassist.rag import Answer, Citation
from docassist.reports import AI, DEFAULT, ESCALATED, build_report, llm_classifier
from docassist.retrieval import FakeEmbedder, VectorStore

TEAMS = (
    Team("hr", "HR", "hr@example.com"),
    Team("maintenance", "Održavanje", "odrzavanje@example.com"),
)
END = date(2026, 11, 9)


def row(question, when="2026-11-05T10:00:00+00:00", found=0, feedback=None, team=None):
    return {
        "question": question,
        "created_at": when,
        "found": found,
        "feedback": feedback,
        "escalation_team": team,
    }


def gaps_of(report, team_id):
    return next((t.gaps for t in report.teams if t.team.id == team_id), [])


# --- grouping and teams ------------------------------------------------------------------


def test_the_same_question_asked_again_counts_once():
    rows = [
        row("Koliki je bonus?", "2026-11-03T08:00:00+00:00"),
        row("koliki je  bonus", "2026-11-07T09:00:00+00:00"),
        row("Kada radi kantina?", "2026-11-04T08:00:00+00:00", found=1, feedback=-1),
    ]

    report = build_report(rows, TEAMS, end=END)

    [bonus, canteen] = gaps_of(report, "hr")
    assert (bonus.question, bonus.times, bonus.not_found) == ("koliki je  bonus", 2, 2)
    assert bonus.last_asked.startswith("2026-11-07")
    assert (canteen.times, canteen.not_found, canteen.not_helpful) == (1, 0, 1)
    assert report.total == 2
    assert (report.start, report.end) == (date(2026, 11, 3), END)


def test_questions_go_to_the_escalated_team_then_the_ai_choice_then_the_first_team():
    rows = [
        row("Kako se menja filter?", team="maintenance"),
        row("Koliko traje garancija?"),
        row("Koliki je bonus?"),
    ]

    def classify(questions, teams):
        assert questions == ["Koliko traje garancija?", "Koliki je bonus?"]  # not escalated
        return {"Koliko traje garancija?": "maintenance", "Koliki je bonus?": "nepostojeci"}

    report = build_report(rows, TEAMS, end=END, classify=classify)

    maintenance = {g.question: g.assigned_by for g in gaps_of(report, "maintenance")}
    hr = {g.question: g.assigned_by for g in gaps_of(report, "hr")}
    assert maintenance == {"Kako se menja filter?": ESCALATED, "Koliko traje garancija?": AI}
    assert hr == {"Koliki je bonus?": DEFAULT}  # unknown team from the model → first team


def test_most_asked_questions_come_first_and_teams_without_gaps_are_left_out():
    rows = [row("Jednom?"), *[row("Triput?", f"2026-11-0{d}T08:00:00+00:00") for d in (4, 5, 6)]]

    report = build_report(rows, TEAMS, end=END)

    assert [t.team.id for t in report.teams] == ["hr"]
    assert [g.question for g in report.teams[0].gaps] == ["Triput?", "Jednom?"]


def test_no_gaps_no_e_mails():
    assert build_report([], TEAMS, end=END).teams == []
    with pytest.raises(ValueError, match="at least one team"):
        build_report([], (), end=END)


# --- the e-mail --------------------------------------------------------------------------


def test_the_e_mail_names_the_period_and_what_to_do():
    rows = [row("Koliki je <bonus>?"), row("Koliki je <bonus>?"), row("Ima li parking?")]

    [hr] = build_report(rows, TEAMS, end=END).teams

    assert hr.subject == "[Doc Assistant] HR: 2 pitanja bez odgovora (3. 11. 2026. – 9. 11. 2026.)"
    assert "1. Koliki je <bonus>?\n   pitano 2 puta · poslednji put 5. 11. 2026." in hr.text
    assert "2. Ima li parking?\n   pitano 1 put" in hr.text
    assert "dodajte u biblioteku" in hr.text
    assert "Koliki je &lt;bonus&gt;?" in hr.html and "<bonus>" not in hr.html


@pytest.mark.parametrize(("count", "noun"), [(1, "pitanje"), (2, "pitanja"), (11, "pitanja")])
def test_subject_uses_serbian_plural(count, noun):
    rows = [row(f"Pitanje broj {n}?") for n in range(count)]

    [hr] = build_report(rows, TEAMS, end=END).teams

    assert f"HR: {count} {noun} bez odgovora" in hr.subject


# --- the AI classifier -------------------------------------------------------------------


def test_the_model_answer_is_read_as_json():
    llm = FakeProvider('Evo: {"1": "Maintenance", "2": "hr", "3": ""}')

    picked = llm_classifier(llm)(["Filter?", "Bonus?", "Parking?"], TEAMS)

    assert picked == {"Filter?": "maintenance", "Bonus?": "hr"}
    [(system, messages)] = llm.calls
    assert "- maintenance: Održavanje" in system
    assert messages[0]["content"] == "1. Filter?\n2. Bonus?\n3. Parking?"


@pytest.mark.parametrize("reply", ["ne znam", "{nije json", "[1, 2]"])
def test_an_unusable_model_answer_assigns_nothing(reply):
    assert llm_classifier(FakeProvider(reply))(["Bonus?"], TEAMS) == {}


def test_a_failing_model_assigns_nothing():
    class Failing(FakeProvider):
        def complete(self, system, messages):
            raise LLMError("rate limit")

    assert llm_classifier(Failing())(["Bonus?"], TEAMS) == {}


# --- the log -----------------------------------------------------------------------------


def answer(question, found):
    citations = [Citation("faq.txt", 1, ())] if found else []  # found = backed by a citation
    return Answer(question, "odgovor", citations, [], "fake", "fake", 0.1)


def test_gaps_since_and_the_escalated_team(tmp_path):
    log = QueryLog(tmp_path / "logs.sqlite")
    missing = log.log(answer("Bonus?", found=False), channel="web")
    log.log(answer("Kantina?", found=True), channel="web")
    log.set_escalation(missing, "ESC-1", "hr")

    [gap] = log.gaps_since("2000-01-01T00:00:00")

    assert (gap["question"], gap["escalation"], gap["escalation_team"]) == ("Bonus?", "ESC-1", "hr")
    assert log.gaps_since("2999-01-01T00:00:00") == []


def test_an_older_log_file_gets_the_team_column(tmp_path):
    path = tmp_path / "old.sqlite"
    QueryLog(path).stats()
    with sqlite3.connect(path) as db:  # as written before the column existed
        db.execute("ALTER TABLE queries DROP COLUMN escalation_team")

    QueryLog(path).stats()

    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(queries)")}
    assert "escalation_team" in columns


# --- the API -----------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("ESCALATION_TEAMS", "hr:HR:hr@example.com,quality:Kvalitet:q@example.com")
    settings = Settings.from_env(load_dotenv_file=False)
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    app = create_app(settings=settings, store=store, llm_factory=lambda s, p: FakeProvider())
    return TestClient(app)


def test_report_api_after_unanswered_and_forwarded_questions(client):
    first = client.post("/ask", json={"question": "Koliki je bonus?"}).json()
    client.post("/ask", json={"question": "Ima li parking?"})
    client.post("/escalate", json={"log_id": first["log_id"], "team": "quality"})

    report = client.get("/reports/gaps", params={"days": 7}).json()

    assert report["total"] == 2 and report["days"] == 7
    by_team = {t["team"]: t for t in report["teams"]}
    assert [g["question"] for g in by_team["quality"]["gaps"]] == ["Koliki je bonus?"]
    assert by_team["quality"]["gaps"][0]["assigned_by"] == "escalated"
    assert by_team["hr"]["email"] == "hr@example.com"
    assert by_team["hr"]["subject"].startswith("[Doc Assistant] HR: 1 pitanje bez odgovora")
    assert "Ima li parking?" in by_team["hr"]["text"]


def test_report_as_a_page_and_with_an_unusable_classifier(client):
    client.post("/ask", json={"question": "Koliki je bonus?"})

    page = client.get("/reports/gaps", params={"format": "html"})
    classified = client.get("/reports/gaps", params={"classify": True}).json()

    assert page.headers["content-type"].startswith("text/html")
    assert "Izveštaj o prazninama u dokumentaciji" in page.text and "Koliki je bonus?" in page.text
    assert classified["teams"][0]["gaps"][0]["assigned_by"] == "default"  # fake reply: no JSON
    assert client.get("/reports/gaps", params={"days": 0}).status_code == 422
    assert client.get("/reports/gaps", params={"format": "xml"}).status_code == 422


def test_a_follow_up_is_shown_and_grouped_as_its_standalone_question():
    standalone = "Koje bolesti spadaju u petu kategoriju zaraznih bolesti?"
    rows = [
        {**row("a u petu?"), "search_query": standalone},
        {**row(standalone, "2026-11-06T10:00:00+00:00"), "search_query": None},
    ]

    [gap] = build_report(rows, TEAMS, end=END).teams[0].gaps

    assert (gap.question, gap.times) == (standalone, 2)
    assert gap.asked_as == ""  # the last time it was asked on its own


def test_the_e_mail_says_how_a_follow_up_was_asked():
    rows = [{**row("a u petu?"), "search_query": "Koje bolesti spadaju u petu kategoriju?"}]

    [hr] = build_report(rows, TEAMS, end=END).teams

    assert "1. Koje bolesti spadaju u petu kategoriju?" in hr.text
    assert "pitano kao nastavak razgovora: „a u petu?“" in hr.text


def test_the_log_keeps_the_rewritten_follow_up(tmp_path):
    log = QueryLog(tmp_path / "logs.sqlite")
    follow_up = Answer("a u petu?", "x", [], [], "fake", "fake", 0.1, search_query="Peta?")

    log.log(follow_up, channel="web")

    [gap] = log.gaps_since("2000-01-01T00:00:00")
    assert gap["search_query"] == "Peta?"
