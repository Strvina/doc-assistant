"""Doc Assistant — Streamlit UI.

uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from docassist.agent import run_agent
from docassist.config import Settings
from docassist.factory import MissingAPIKeyError, build_llm, build_store, build_tool_context
from docassist.ingest import SUPPORTED_EXTENSIONS, DocumentLoadError, ingest
from docassist.llm import LLMError
from docassist.rag import Answer, answer
from docassist.retrieval import EmbedderMismatchError, VectorStore

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DOCS = ROOT / "data" / "sample_docs"
UPLOAD_DIR = ROOT / "data" / "uploads"

PROVIDERS = {"claude": "Claude (Anthropic)", "openai": "ChatGPT (OpenAI)", "fake": "Offline demo"}
MAX_QUESTION_CHARS = 1000
EXAMPLE_QUESTIONS = [
    "Koliko dana godišnjeg odmora imam?",
    "Koliki je moment pritezanja za M8 vijke na sabirnicama?",
    "What is the acceptance criterion for the insulation resistance test?",
]

st.set_page_config(page_title="Doc Assistant", page_icon="📄", layout="wide")


@st.cache_resource
def get_settings() -> Settings:
    return Settings.from_env()


@st.cache_resource
def get_store(_settings: Settings) -> VectorStore:
    return build_store(_settings)


def provider_available(settings: Settings, provider: str) -> bool:
    if provider == "claude":
        return bool(settings.anthropic_api_key)
    if provider == "openai":
        return bool(settings.openai_api_key and settings.openai_model)
    return True


def add_files(store: VectorStore, paths: list[Path]) -> None:
    for path in paths:
        try:
            added = store.add_document(ingest(path))
        except (DocumentLoadError, OSError) as exc:
            st.sidebar.error(f"{path.name}: {exc}")
            continue
        if added:
            st.sidebar.success(f"{path.name}: {added} chunks indexed")
        else:
            st.sidebar.info(f"{path.name}: already indexed")


def sidebar(settings: Settings, store: VectorStore) -> tuple[str, int, bool]:
    st.sidebar.header("Model")
    options = list(PROVIDERS)
    default = options.index(settings.llm_provider)
    provider = st.sidebar.radio(
        "Answer with",
        options,
        index=default,
        format_func=lambda p: (
            PROVIDERS[p] + ("" if provider_available(settings, p) else " — no key")
        ),
        key="provider",
    )
    top_k = st.sidebar.slider("Chunks given to the model", 1, 10, settings.top_k, key="top_k")
    agent_mode = st.sidebar.toggle(
        "Agent mode (tools)",
        key="agent_mode",
        help="The model decides itself when to search, read a whole document, "
        "escalate a question to a person or draft a leave request.",
    )

    st.sidebar.header("Documents")
    uploads = None
    if settings.allow_uploads:
        uploads = st.sidebar.file_uploader(
            "Add documents",
            type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
            accept_multiple_files=True,
            key=f"uploader_{st.session_state.get('upload_round', 0)}",
        )
    elif store.count() == 0:
        # Public demo: documents are fixed, so index the demo set on first start.
        add_files(store, sorted(p for p in SAMPLE_DOCS.iterdir() if p.is_file()))
    if uploads:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        saved = []
        for upload in uploads:
            path = UPLOAD_DIR / Path(upload.name).name  # never trust a client-side path
            path.write_bytes(upload.getvalue())
            saved.append(path)
        with st.sidebar.status("Indexing…"):
            add_files(store, saved)
        st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1

    if settings.allow_uploads and st.sidebar.button("Load demo documents", key="load_demo"):
        with st.sidebar.status("Indexing demo documents…"):
            add_files(store, sorted(p for p in SAMPLE_DOCS.iterdir() if p.is_file()))

    documents = store.documents()
    if not documents:
        st.sidebar.caption("No documents yet.")
    for doc in documents:
        if not settings.allow_uploads:  # public demo: the document set is fixed
            st.sidebar.markdown(f"**{doc.source}**  \n{doc.pages} pages · {doc.chunks} chunks")
            continue
        left, right = st.sidebar.columns([5, 1])
        left.markdown(f"**{doc.source}**  \n{doc.pages} pages · {doc.chunks} chunks")
        if right.button("🗑", key=f"delete_{doc.doc_id}", help=f"Delete {doc.source}"):
            store.delete_document(doc.doc_id)
            uploaded = UPLOAD_DIR / doc.source
            if uploaded.is_file():
                uploaded.unlink()  # also remove our copy of the file
            st.rerun()

    return provider, top_k, agent_mode


def render_answer(result: Answer) -> None:
    if result.tool_calls:
        with st.expander(f"🛠 {len(result.tool_calls)} tool call(s)"):
            for call in result.tool_calls:
                icon = "⚠️" if call.is_error else "✅"
                st.markdown(f"{icon} `{call.name}` {call.input}")
                st.text(call.output[:1500])
    st.markdown(result.text)
    for citation in result.citations:
        with st.expander(f"📄 {citation.source} — str. {citation.page}"):
            for chunk in citation.chunks:
                st.text(chunk.text)
    if result.invalid_citations:
        st.warning(
            "The answer cites pages that were not among the retrieved passages: "
            + ", ".join(result.invalid_citations)
        )
    meta = f"{PROVIDERS.get(result.provider, result.provider)}"
    if result.model:
        meta += f" · {result.model}"
    meta += f" · {result.latency_s:.1f} s"
    if result.input_tokens or result.output_tokens:
        meta += f" · {result.input_tokens} in / {result.output_tokens} out tokens"
    st.caption(meta)


def main() -> None:
    settings = get_settings()
    try:
        store = get_store(settings)
    except (MissingAPIKeyError, EmbedderMismatchError) as exc:
        st.error(str(exc))
        st.stop()

    provider, top_k, agent_mode = sidebar(settings, store)

    st.title("📄 Doc Assistant")
    st.caption(
        "Answers only from your documents, with a source for every claim. "
        "Ask in Serbian, German or English."
    )

    history: list[Answer] = st.session_state.setdefault("history", [])
    if not history:
        st.markdown("**Try:** " + " · ".join(f"_{q}_" for q in EXAMPLE_QUESTIONS))

    for past in history:
        with st.chat_message("user"):
            st.markdown(past.question)
        with st.chat_message("assistant"):
            render_answer(past)

    limit_reached = len(history) >= settings.max_questions_per_session
    if limit_reached:
        st.info("Question limit for this session reached. Reload the page to start over.")
    question = st.chat_input("Ask a question about the documents", disabled=limit_reached)
    if not question:
        return
    if len(question) > MAX_QUESTION_CHARS:
        st.warning(f"Please keep the question under {MAX_QUESTION_CHARS} characters.")
        return

    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            llm = build_llm(settings, provider)
            recent = [(h.question, h.text) for h in history[-3:]]
            if agent_mode:
                with st.spinner("Working…"):
                    ctx = build_tool_context(settings, store)
                    ctx.top_k = top_k
                    result = run_agent(question, ctx, llm, history=recent)
            else:
                with st.spinner("Searching the documents…"):
                    result = answer(question, store, llm, k=top_k, history=recent)
        except (MissingAPIKeyError, LLMError) as exc:
            st.error(str(exc))
            return
        render_answer(result)
    history.append(result)


main()
