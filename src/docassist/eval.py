"""Evaluate answers against eval/questions.yaml.

    uv run python -m docassist.eval                       # configured provider
    uv run python -m docassist.eval --provider openai --agent
    uv run python -m docassist.eval --retrieval-only      # no LLM: is the right page retrieved?

Writes a JSON report to eval/results/ and prints a Markdown summary.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from docassist.agent import run_agent
from docassist.config import Settings
from docassist.factory import MissingAPIKeyError, build_llm, build_store, build_tool_context
from docassist.ingest import ingest
from docassist.llm import LLMError, LLMProvider
from docassist.rag import Answer, answer
from docassist.retrieval import EmbedderMismatchError, VectorStore, retrieve

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "eval" / "questions.yaml"
RESULTS_DIR = ROOT / "eval" / "results"
SAMPLE_DOCS = ROOT / "data" / "sample_docs"

# USD per million tokens (input, output). Check current prices before quoting the numbers.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


@dataclass(frozen=True)
class Question:
    id: str
    lang: str
    question: str
    source: str | None
    page: int | None
    facts: list[str] = field(default_factory=list)

    @property
    def answerable(self) -> bool:
        return self.source is not None


@dataclass
class QuestionResult:
    id: str
    lang: str
    answerable: bool
    answer: str = ""
    found: bool = False
    page_hit: bool = False  # cited the expected source and page
    doc_hit: bool = False  # cited the expected source (any page)
    retrieved_hit: bool = False  # expected page was among the retrieved chunks
    fact_recall: float = 0.0
    invalid_citations: int = 0
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def missed(self) -> bool:
        return self.answerable and self.error is None and not self.found

    @property
    def correct(self) -> bool:
        """Answerable: right page cited and all facts present. Unanswerable: said "not found"."""
        if not self.answerable:
            return not self.found and self.error is None
        return self.page_hit and self.fact_recall == 1.0


def load_questions(path: Path = QUESTIONS) -> list[Question]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Question(
            id=item["id"],
            lang=item["lang"],
            question=item["question"],
            source=item.get("source"),
            page=item.get("page"),
            facts=list(item.get("facts") or []),
        )
        for item in raw
    ]


def fact_recall(text: str, facts: Sequence[str]) -> float:
    if not facts:
        return 1.0
    haystack = " ".join(text.lower().split())
    hits = sum(any(alt.lower() in haystack for alt in fact.split("|")) for fact in facts)
    return hits / len(facts)


def score(question: Question, result: Answer) -> QuestionResult:
    cited = {(c.source, c.page) for c in result.citations}
    retrieved = {(r.chunk.source, r.chunk.page) for r in result.retrieved}
    expected = (question.source, question.page)
    return QuestionResult(
        id=question.id,
        lang=question.lang,
        answerable=question.answerable,
        answer=result.text,
        found=result.found,
        page_hit=expected in cited,
        doc_hit=any(source == question.source for source, _ in cited),
        retrieved_hit=expected in retrieved,
        fact_recall=fact_recall(result.text, question.facts) if question.answerable else 0.0,
        invalid_citations=len(result.invalid_citations),
        latency_s=result.latency_s,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        tool_calls=[call.name for call in result.tool_calls],
    )


def evaluate(
    questions: Sequence[Question],
    store: VectorStore,
    llm: LLMProvider | None,
    *,
    settings: Settings,
    agent: bool = False,
    progress: bool = False,
) -> list[QuestionResult]:
    """Run every question. `llm=None` evaluates retrieval only (no LLM calls)."""
    results = []
    for number, question in enumerate(questions, start=1):
        if progress:
            print(f"[{number}/{len(questions)}] {question.id}", file=sys.stderr)
        if llm is None:
            hits = retrieve(store, question.question, k=settings.top_k)
            pages = {(r.chunk.source, r.chunk.page) for r in hits}
            results.append(
                QuestionResult(
                    id=question.id,
                    lang=question.lang,
                    answerable=question.answerable,
                    retrieved_hit=(question.source, question.page) in pages,
                )
            )
            continue
        try:
            if agent:
                ctx = build_tool_context(settings, store)
                result = run_agent(question.question, ctx, llm)
            else:
                result = answer(question.question, store, llm, k=settings.top_k)
        except LLMError as exc:
            results.append(
                QuestionResult(question.id, question.lang, question.answerable, error=str(exc))
            )
            continue
        results.append(score(question, result))
    return results


def summarize(results: Sequence[QuestionResult], model: str = "") -> dict[str, Any]:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    latencies = [r.latency_s for r in results if r.error is None]
    input_tokens = sum(r.input_tokens for r in results)
    output_tokens = sum(r.output_tokens for r in results)

    def rate(items: Sequence[QuestionResult], attr: str) -> float | None:
        return round(sum(bool(getattr(r, attr)) for r in items) / len(items), 3) if items else None

    summary: dict[str, Any] = {
        "questions": len(results),
        "errors": sum(r.error is not None for r in results),
        "correct": rate(results, "correct"),
        "retrieval_hit_rate": rate(answerable, "retrieved_hit"),
        "page_citation_accuracy": rate(answerable, "page_hit"),
        "document_citation_accuracy": rate(answerable, "doc_hit"),
        "fact_recall": (
            round(statistics.mean(r.fact_recall for r in answerable), 3) if answerable else None
        ),
        # Share of answerable questions where the assistant wrongly said "not found".
        "false_not_found": rate(answerable, "missed"),
        "not_found_accuracy": rate(unanswerable, "correct"),
        "invalid_citations": sum(r.invalid_citations for r in results),
        "latency_avg_s": round(statistics.mean(latencies), 2) if latencies else None,
        "latency_p95_s": round(_percentile(latencies, 0.95), 2) if latencies else None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": None,
    }
    if model in PRICES:
        price_in, price_out = PRICES[model]
        summary["cost_usd"] = round(
            (input_tokens * price_in + output_tokens * price_out) / 1_000_000, 4
        )
    return summary


def to_markdown(summary: dict[str, Any], results: Sequence[QuestionResult], title: str) -> str:
    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value * 100:.0f} %"

    cost = "—" if summary["cost_usd"] is None else summary["cost_usd"]

    lines = [
        f"### {title}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Correct overall | {pct(summary['correct'])} |",
        f"| Expected page retrieved (top-k) | {pct(summary['retrieval_hit_rate'])} |",
        f"| Expected page cited | {pct(summary['page_citation_accuracy'])} |",
        f"| Expected document cited | {pct(summary['document_citation_accuracy'])} |",
        f"| Fact recall | {pct(summary['fact_recall'])} |",
        f'| "Not found" on unanswerable questions | {pct(summary["not_found_accuracy"])} |',
        f'| Wrong "not found" on answerable questions | {pct(summary["false_not_found"])} |',
        f"| Invalid (made-up) citations | {summary['invalid_citations']} |",
        f"| Latency avg / p95 | {summary['latency_avg_s']} s / {summary['latency_p95_s']} s |",
        f"| Tokens in / out | {summary['input_tokens']} / {summary['output_tokens']} |",
        f"| Estimated cost (USD) | {cost} |",
        f"| Errors | {summary['errors']} |",
        "",
        "Correct overall = expected page cited and all key facts present; for questions the",
        'documents cannot answer: the assistant said "not found".',
        "",
    ]
    failed = [r for r in results if not r.correct]
    if failed:
        lines.append("Questions not fully correct: " + ", ".join(f"`{r.id}`" for r in failed))
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="docassist.eval", description="Evaluate answers.")
    parser.add_argument("--provider", choices=["claude", "openai", "fake"], default=None)
    parser.add_argument("--agent", action="store_true", help="evaluate the tool-using agent")
    parser.add_argument("--retrieval-only", action="store_true", help="no LLM calls")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    try:
        store = build_store(settings)
        for path in sorted(SAMPLE_DOCS.iterdir()):  # no-op for documents already indexed
            store.add_document(ingest(path))
        llm = None if args.retrieval_only else build_llm(settings, args.provider)
    except (MissingAPIKeyError, EmbedderMismatchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    questions = load_questions(args.questions)[: args.limit]
    results = evaluate(questions, store, llm, settings=settings, agent=args.agent, progress=True)

    if llm is None:
        label, model = "retrieval", ""
    else:
        label = llm.name + ("-agent" if args.agent else "")
        model = {"claude": settings.anthropic_model, "openai": settings.openai_model}.get(
            llm.name, llm.name
        )
    summary = summarize(results, model)
    embeddings = settings.embedding_model if settings.embedding_provider == "openai" else "fake"
    title = f"{label} · {model or '-'} · embeddings {embeddings} · {datetime.now():%Y-%m-%d %H:%M}"

    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / f"{label}-{datetime.now():%Y%m%d-%H%M%S}.json"
    report.write_text(
        json.dumps(
            {"title": title, "summary": summary, "results": [asdict(r) for r in results]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if llm is None:
        print(f"Expected page among top-{settings.top_k} chunks: {summary['retrieval_hit_rate']}")
        missed = [r.id for r in results if r.answerable and not r.retrieved_hit]
        print("Missed: " + (", ".join(missed) or "none"))
    else:
        print(to_markdown(summary, results, title))
    print(f"Full report: {report}")
    return 0


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main())
