"""Command-line interface.

uv run python -m docassist.cli index data/sample_docs
uv run python -m docassist.cli search "Koliki je moment pritezanja za M8?"
uv run python -m docassist.cli ask "Koliko dana godišnjeg odmora imam?" [--provider X] [--agent]
uv run python -m docassist.cli docs
uv run python -m docassist.cli delete <doc_id>
uv run python -m docassist.cli users add <name> --role admin   (also: list, role, password, remove)
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from docassist import reindex
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
from docassist.users import ROLES, User, UserError, UserStore


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    try:
        return args.handler(args, settings)
    except (MissingAPIKeyError, EmbedderMismatchError, LLMError, UserError) as exc:
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
    ask.add_argument(
        "--provider", choices=["claude", "openai", "gemini", "groq", "fake"], default=None
    )
    ask.add_argument("--agent", action="store_true", help="let the model use tools")
    ask.set_defaults(handler=cmd_ask)

    docs = commands.add_parser("docs", help="list indexed documents")
    docs.set_defaults(handler=cmd_docs)

    delete = commands.add_parser("delete", help="remove a document from the index")
    delete.add_argument("doc_id")
    delete.set_defaults(handler=cmd_delete)

    users = commands.add_parser("users", help="people who may sign in (LOGIN_REQUIRED=true)")
    actions = users.add_subparsers(required=True, metavar="action")
    from_stdin = "read the password from standard input instead of asking for it"
    add = actions.add_parser("add", help="create a user")
    add.add_argument("username")
    add.add_argument("--role", choices=ROLES, default="employee")
    add.add_argument("--password-stdin", action="store_true", help=from_stdin)
    add.set_defaults(handler=cmd_users_add)
    actions.add_parser("list", help="show the users").set_defaults(handler=cmd_users_list)
    role = actions.add_parser("role", help="give a user another role")
    role.add_argument("username")
    role.add_argument("role", choices=ROLES)
    role.set_defaults(handler=cmd_users_role)
    password = actions.add_parser("password", help="set a new password (signs the user out)")
    password.add_argument("username")
    password.add_argument("--password-stdin", action="store_true", help=from_stdin)
    password.set_defaults(handler=cmd_users_password)
    remove = actions.add_parser("remove", help="delete a user and their sign-ins")
    remove.add_argument("username")
    remove.set_defaults(handler=cmd_users_remove)

    return parser


def cmd_users_add(args: argparse.Namespace, settings: Settings) -> int:
    user = UserStore(settings.log_db).add(args.username, _new_password(args), args.role)
    print(f"Added {user.username} ({user.role}).")
    if not settings.login_required:
        print("Accounts are off: set LOGIN_REQUIRED=true in .env and restart the app.")
    return 0


def cmd_users_list(args: argparse.Namespace, settings: Settings) -> int:
    users = UserStore(settings.log_db).all()
    if not users:
        print("No users yet. Create the first administrator: users add <name> --role admin")
    for user in users:
        state = "  (disabled)" if user.disabled else ""
        print(f"{user.username:<32} {user.role:<9} since {user.created_at[:10]}{state}")
    return 0


def cmd_users_role(args: argparse.Namespace, settings: Settings) -> int:
    store = UserStore(settings.log_db)
    store.set_role(_existing(store, args.username).id, args.role)
    print(f"{args.username} is now {args.role}.")
    return 0


def cmd_users_password(args: argparse.Namespace, settings: Settings) -> int:
    store = UserStore(settings.log_db)
    user = _existing(store, args.username)
    store.set_password(user.id, _new_password(args))
    print(f"New password set for {user.username}; they are signed out everywhere.")
    return 0


def cmd_users_remove(args: argparse.Namespace, settings: Settings) -> int:
    store = UserStore(settings.log_db)
    store.remove(_existing(store, args.username).id)
    print(f"Removed {args.username}.")
    return 0


def _existing(store: UserStore, username: str) -> User:
    user = store.by_name(username)
    if user is None:
        raise UserError(f"No user named {username!r}")
    return user


def _new_password(args: argparse.Namespace) -> str:
    """Asked for twice without showing it; `--password-stdin` is for scripts (the password
    never appears in the command line, where the shell history would keep it)."""
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Again: ") != password:
        raise UserError("The two passwords are not the same")
    return password


def cmd_index(args: argparse.Namespace, settings: Settings) -> int:
    if args.rebuild:  # an interrupted rebuild is noticed and redone by the web app (ADR-032)
        reindex.mark_started(settings.index_dir)
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
    if args.rebuild:
        reindex.mark_finished(settings.index_dir)
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
