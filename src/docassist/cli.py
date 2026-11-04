"""Command-line interface.

uv run python -m docassist.cli index data/sample_docs
uv run python -m docassist.cli search "Koliki je moment pritezanja za M8?"
uv run python -m docassist.cli ask "Koliko dana godišnjeg odmora imam?" [--provider X] [--agent]
uv run python -m docassist.cli docs
uv run python -m docassist.cli delete <doc_id>
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from docassist.agent import run_agent
from docassist.config import Settings
from docassist.factory import (
    MissingAPIKeyError,
    build_library,
    build_llm,
    build_store,
    build_tool_context,
)
from docassist.ingest import SUPPORTED_EXTENSIONS, DocumentLoadError
from docassist.llm import LLMError
from docassist.rag import answer
from docassist.retrieval import EmbedderMismatchError, retrieve


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    try:
        return args.handler(args, settings)
    except (MissingAPIKeyError, EmbedderMismatchError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docassist", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(required=True, metavar="command")

    index = commands.add_parser("index", help="add files or folders to the index")
    index.add_argument("paths", nargs="+", type=Path)
    index.add_argument("--rebuild", action="store_true", help="delete the index first")
    index.set_defaults(handler=cmd_index)

    search = commands.add_parser("search", help="show the chunks most similar to a question")
    search.add_argument("question")
    search.add_argument("-k", type=int, default=None, help="number of results (default TOP_K)")
    search.set_defaults(handler=cmd_search)

    ask = commands.add_parser("ask", help="answer a question from the documents, with citations")
    ask.add_argument("question")
    ask.add_argument("-k", type=int, default=None, help="chunks given to the LLM (default TOP_K)")
    ask.add_argument("--provider", choices=["claude", "openai", "fake"], default=None)
    ask.add_argument("--agent", action="store_true", help="let the model use tools")
    ask.set_defaults(handler=cmd_ask)

    docs = commands.add_parser("docs", help="list indexed documents")
    docs.set_defaults(handler=cmd_docs)

    delete = commands.add_parser("delete", help="remove a document from the index")
    delete.add_argument("doc_id")
    delete.set_defaults(handler=cmd_delete)

    return parser


def cmd_index(args: argparse.Namespace, settings: Settings) -> int:
    library = build_library(settings, rebuild=args.rebuild)
    store = library.store
    failures = 0
    for path in _iter_files(args.paths):
        try:
            added = library.add_file(path).chunks_added
        except (DocumentLoadError, OSError) as exc:
            print(f"  FAILED     {path}: {exc}", file=sys.stderr)
            failures += 1
            continue
        status = f"{added:3d} chunks" if added else "unchanged "
        print(f"  {status}  {path}")
    print(f"Index: {len(store.documents())} documents, {store.count()} chunks")
    return 1 if failures else 0


def cmd_search(args: argparse.Namespace, settings: Settings) -> int:
    store = build_store(settings)
    results = retrieve(store, args.question, k=args.k or settings.top_k)
    if not results:
        print("No results (is the index empty? run: index data/sample_docs)")
    for rank, result in enumerate(results, start=1):
        chunk = result.chunk
        print(f"\n#{rank}  score {result.score:.3f}  [{chunk.source}, p. {chunk.page}]")
        print(_indent(_shorten(chunk.text, 400)))
    return 0


def cmd_ask(args: argparse.Namespace, settings: Settings) -> int:
    store = build_store(settings)
    llm = build_llm(settings, args.provider)
    if args.agent:
        result = run_agent(args.question, build_tool_context(settings, store), llm)
        for call in result.tool_calls:
            status = "error" if call.is_error else "ok"
            print(f"  → {call.name}({call.input}) [{status}]")
        if result.tool_calls:
            print()
    else:
        result = answer(args.question, store, llm, k=args.k or settings.top_k)

    print(result.text)
    if result.citations:
        print()
        print("Sources:")
        for citation in result.citations:
            print(f"  - {citation.source}, p. {citation.page}")
    if result.invalid_citations:
        print()
        print(f"Warning: citations not found in the context: {result.invalid_citations}")
    print()
    print(
        f"[{result.provider} {result.model} · {result.latency_s:.1f} s · "
        f"{result.input_tokens} in / {result.output_tokens} out tokens]"
    )
    return 0


def cmd_docs(args: argparse.Namespace, settings: Settings) -> int:
    documents = build_store(settings).documents()
    if not documents:
        print("The index is empty.")
    for doc in documents:
        print(f"{doc.doc_id}  {doc.pages:3d} pages  {doc.chunks:4d} chunks  {doc.source}")
    return 0


def cmd_delete(args: argparse.Namespace, settings: Settings) -> int:
    removed = build_library(settings).delete(args.doc_id)
    if not removed:
        print(f"No document with id {args.doc_id}", file=sys.stderr)
        return 1
    print(f"Removed {removed} chunks.")
    return 0


def _iter_files(paths: Sequence[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            )
        else:
            yield path


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
