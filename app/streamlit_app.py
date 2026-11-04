"""Doc Assistant — Streamlit UI.

uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import streamlit as st

from docassist.agent import run_agent
from docassist.config import Settings
from docassist.factory import MissingAPIKeyError, build_llm, build_store, build_tool_context
from docassist.ingest import SUPPORTED_EXTENSIONS, DocumentLoadError, ingest
from docassist.llm import LLMError
from docassist.llm.base import ToolCall
from docassist.logs import QueryLog
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
    "Šta da radim ako se povredim na poslu?",
    "What is the acceptance criterion for the insulation test?",
]
FILE_ICONS = {".pdf": "📕", ".docx": "📘", ".txt": "📄", ".md": "📝"}
TOOL_LABELS = {
    "search_documents": "Pretraga dokumenata",
    "list_documents": "Spisak dokumenata",
    "summarize_document": "Čitanje celog dokumenta",
    "escalate_to_human": "Prosleđivanje pitanja nadležnom",
    "create_leave_request": "Nacrt zahteva za odmor",
}
_CITATION = re.compile(r"\[\s*([^\[\],]+?\.(?:pdf|docx|txt|md))\s*,\s*([^\]]+?)\s*\]", re.I)

CSS = """
<style>
#MainMenu, footer, [data-testid="stDecoration"] { visibility: hidden; height: 0; }
.block-container { padding-top: 2.2rem; max-width: 820px; }
.da-hero h1 { font-size: 1.9rem; margin: 0 0 .2rem 0; letter-spacing: -.01em; }
.da-hero p { color: #5A6578; margin: 0 0 .8rem 0; font-size: .98rem; }
.da-chips { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: 1.2rem; }
.da-chip { background: #F2F5FA; border: 1px solid #E1E7F0; border-radius: 999px;
           padding: .15rem .65rem; font-size: .8rem; color: #3A4658; }
.da-card { border: 1px solid #E1E7F0; border-radius: 14px; padding: 1.1rem 1.2rem;
           background: linear-gradient(180deg, #FAFBFD 0%, #FFFFFF 100%); margin-bottom: 1rem; }
.da-card h3 { font-size: 1.05rem; margin: 0 0 .6rem 0; padding: 0; }
.da-steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: .8rem; }
.da-step { font-size: .86rem; color: #3A4658; }
.da-step b { display: block; color: #1F4E8C; margin-bottom: .15rem; }
.da-cite { display: inline-block; background: #E8EFFA; color: #1F4E8C; border-radius: 6px;
           padding: 0 .4rem; font-size: .75rem; font-weight: 600; margin: 0 .1rem;
           white-space: nowrap; vertical-align: 1px; }
.da-quote { border-left: 3px solid #1F4E8C; background: #F7F9FC; padding: .6rem .8rem;
            border-radius: 0 8px 8px 0; font-size: .86rem; color: #2A3445; line-height: 1.5;
            margin-bottom: .5rem; }
.da-meta { color: #8792A2; font-size: .76rem; }
.da-doc { font-size: .9rem; line-height: 1.3; white-space: nowrap; overflow: hidden;
          text-overflow: ellipsis; }
.da-doc small { color: #8792A2; }
@media (max-width: 640px) { .da-steps { grid-template-columns: 1fr; } }
</style>
"""

st.set_page_config(page_title="Doc Assistant", page_icon="📄", layout="centered")
st.markdown(CSS, unsafe_allow_html=True)


# --- cached resources --------------------------------------------------------------------


@st.cache_resource
def get_settings() -> Settings:
    return Settings.from_env()


@st.cache_resource
def get_log(_settings: Settings) -> QueryLog:
    return QueryLog(_settings.log_db)


@st.cache_resource
def get_store(_settings: Settings) -> VectorStore:
    return build_store(_settings)


def provider_available(settings: Settings, provider: str) -> bool:
    if provider == "claude":
        return bool(settings.anthropic_api_key)
    if provider == "openai":
        return bool(settings.openai_api_key and settings.openai_model)
    return True


def save_feedback(log: QueryLog, log_id: int) -> None:
    value = st.session_state.get(f"feedback_{log_id}")  # 1 = 👍, 0 = 👎, None = cleared
    log.set_feedback(log_id, None if value is None else (1 if value == 1 else -1))


def add_files(store: VectorStore, paths: list[Path]) -> None:
    for path in paths:
        try:
            added = store.add_document(ingest(path))
        except (DocumentLoadError, OSError) as exc:
            st.sidebar.error(f"{path.name}: {exc}")
            continue
        if added:
            st.toast(f"{path.name}: indeksirano ({added} pasusa)", icon="✅")
        else:
            st.toast(f"{path.name}: već postoji", icon="ℹ️")


def demo_files() -> list[Path]:
    return sorted(p for p in SAMPLE_DOCS.iterdir() if p.is_file())


# --- sidebar -----------------------------------------------------------------------------


def sidebar(settings: Settings, store: VectorStore, log: QueryLog) -> tuple[str, int, bool]:
    if not settings.allow_uploads and store.count() == 0:
        # Public demo: the document set is fixed, so index the demo set on first start.
        add_files(store, demo_files())
    documents = store.documents()

    with st.sidebar:
        st.markdown(f"### 📚 Dokumenti · {len(documents)}")

        if settings.allow_uploads:
            with st.expander("➕ Dodaj dokumente", expanded=not documents):
                uploads = st.file_uploader(
                    "PDF, Word ili tekst",
                    type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
                    accept_multiple_files=True,
                    key=f"uploader_{st.session_state.get('upload_round', 0)}",
                )
                if uploads:
                    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    saved = []
                    for upload in uploads:
                        path = UPLOAD_DIR / Path(upload.name).name  # never trust client paths
                        path.write_bytes(upload.getvalue())
                        saved.append(path)
                    with st.spinner("Indeksiranje…"):
                        add_files(store, saved)
                    st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
                    st.rerun()
                if st.button("Učitaj demo dokumente", key="load_demo", use_container_width=True):
                    with st.spinner("Indeksiranje demo dokumenata…"):
                        add_files(store, demo_files())
                    st.rerun()

        if not documents:
            st.caption("Još nema dokumenata. Dodaj svoje ili učitaj demo dokumente.")
        for doc in documents:
            icon = FILE_ICONS.get(Path(doc.source).suffix.lower(), "📄")
            name = html.escape(doc.source)
            label = (
                f'<div class="da-doc" title="{name}">{icon} {name}<br>'
                f"<small>{doc.pages} str. · {doc.chunks} pasusa</small></div>"
            )
            if not settings.allow_uploads:  # public demo: the document set is fixed
                st.markdown(label, unsafe_allow_html=True)
                continue
            left, right = st.columns([6, 1], vertical_alignment="center")
            left.markdown(label, unsafe_allow_html=True)
            if right.button(
                "",
                icon=":material/delete:",
                key=f"delete_{doc.doc_id}",
                help=f"Obriši {doc.source}",
                type="tertiary",
            ):
                store.delete_document(doc.doc_id)
                uploaded = UPLOAD_DIR / doc.source
                if uploaded.is_file():
                    uploaded.unlink()  # also remove our copy of the file
                st.rerun()

        st.divider()
        with st.expander("⚙️ Podešavanja"):
            # ChatGPT is optional (ADR-018): only offered when an OpenAI key and model are set.
            options = [p for p in PROVIDERS if p != "openai" or provider_available(settings, p)]
            default = (
                options.index(settings.llm_provider) if settings.llm_provider in options else 0
            )
            provider = st.radio(
                "Model",
                options,
                index=default,
                format_func=lambda p: (
                    PROVIDERS[p] + ("" if provider_available(settings, p) else " — nema ključa")
                ),
                key="provider",
            )
            agent_mode = st.toggle(
                "Agent (alati)",
                key="agent_mode",
                help="Model sam bira korake: pretragu, čitanje celog dokumenta, prosleđivanje "
                "pitanja nadležnom ili nacrt zahteva za odmor.",
            )
            top_k = st.slider("Broj pasusa za odgovor", 1, 10, settings.top_k, key="top_k")

        with st.expander("📊 Statistika"):
            stats = log.stats()
            found = "—" if stats.found_rate is None else f"{stats.found_rate * 100:.0f} %"
            a, b = st.columns(2)
            a.metric("Pitanja", stats.queries)
            b.metric("Iz dokumenata", found)
            a.metric("Prosečno", f"{stats.avg_latency_s or 0} s")
            b.metric("Ocene", f"👍{stats.thumbs_up} 👎{stats.thumbs_down}")
            st.caption(f"Tokeni: {stats.input_tokens} ulaz / {stats.output_tokens} izlaz")

        if st.session_state.get("history") and st.button(
            "🧹 Nova konverzacija", key="new_chat", use_container_width=True
        ):
            st.session_state["history"] = []
            st.session_state["log_ids"] = []
            st.rerun()

    return provider, top_k, agent_mode


# --- rendering ---------------------------------------------------------------------------


def as_html_lines(text: str) -> str:
    """Escaped text with line breaks as <br>; blank lines would end the HTML block."""
    paragraphs = [html.escape(p.strip()).replace("\n", "<br>") for p in text.split("\n\n")]
    return '<div style="height:.45rem"></div>'.join(p for p in paragraphs if p)


def with_citation_chips(text: str) -> str:
    """Escape the answer and turn [file.pdf, str. 2] into small inline chips."""
    escaped = html.escape(text)
    chips = _CITATION.sub(
        lambda m: f'<span class="da-cite">{m.group(1)} · {m.group(2)}</span>', escaped
    )
    return chips.replace("\n\n", "<br>").replace("\n", "<br>")


def describe_tool_call(call: ToolCall) -> str:
    label = TOOL_LABELS.get(call.name, call.name)
    detail = call.input.get("query") or call.input.get("source") or ""
    if call.name == "create_leave_request":
        detail = f"{call.input.get('start_date')} – {call.input.get('end_date')}"
    status = "⚠️" if call.is_error else "✅"
    return f"{status} **{label}**" + (f" — _{detail}_" if detail else "")


def render_answer(result: Answer, log: QueryLog | None = None, log_id: int | None = None) -> None:
    if result.tool_calls:
        with st.expander(f"🛠 Koraci agenta ({len(result.tool_calls)})"):
            for call in result.tool_calls:
                st.markdown(describe_tool_call(call))
    st.markdown(with_citation_chips(result.text), unsafe_allow_html=True)

    for citation in result.citations:
        icon = FILE_ICONS.get(Path(citation.source).suffix.lower(), "📄")
        with st.expander(f"{icon} {citation.source} — str. {citation.page}"):
            st.markdown(
                f'<div class="da-quote">{as_html_lines(citation.passage)}</div>',
                unsafe_allow_html=True,
            )
    if result.invalid_citations:
        st.warning(
            "Odgovor navodi izvore koji nisu među pronađenim pasusima: "
            + ", ".join(result.invalid_citations)
        )

    parts = [PROVIDERS.get(result.provider, result.provider)]
    if result.model and result.model != result.provider:
        parts.append(result.model)
    parts.append(f"{result.latency_s:.1f} s")
    if result.input_tokens or result.output_tokens:
        parts.append(f"{result.input_tokens + result.output_tokens} tokena")
    meta_col, feedback_col = st.columns([4, 1], vertical_alignment="center")
    meta_col.markdown(f'<span class="da-meta">{" · ".join(parts)}</span>', unsafe_allow_html=True)
    if log is not None and log_id is not None:
        with feedback_col:
            st.feedback(
                "thumbs", key=f"feedback_{log_id}", on_change=save_feedback, args=(log, log_id)
            )


def hero(settings: Settings, store: VectorStore, provider: str) -> None:
    model = settings.anthropic_model if provider == "claude" else PROVIDERS[provider]
    count = len(store.documents())
    chips = [
        f"🤖 {model}",
        f"📚 {count} {'dokument' if count == 1 else 'dokumenata'}",
        "🔒 pretraga radi lokalno",
        "🌐 SR · DE · EN",
    ]
    st.markdown(
        '<div class="da-hero"><h1>📄 Doc Assistant</h1>'
        "<p>Interni asistent koji odgovara samo iz dokumenata firme — "
        "uz izvor (dokument i stranu) za svaku tvrdnju.</p></div>"
        '<div class="da-chips">'
        + "".join(f'<span class="da-chip">{html.escape(c)}</span>' for c in chips)
        + "</div>",
        unsafe_allow_html=True,
    )


def welcome() -> str | None:
    st.markdown(
        '<div class="da-card"><h3>Kako radi</h3><div class="da-steps">'
        '<div class="da-step"><b>1. Pitaj</b>na srpskom, nemačkom ili engleskom.</div>'
        '<div class="da-step"><b>2. Pretraga</b>pronalazi prave pasuse, i na drugom jeziku.</div>'
        '<div class="da-step"><b>3. Odgovor</b>sa izvorom; klikni izvor da vidiš tačan tekst.'
        "</div></div></div>",
        unsafe_allow_html=True,
    )
    st.markdown("**Probaj neko od pitanja:**")
    columns = st.columns(2)
    for index, example in enumerate(EXAMPLE_QUESTIONS):
        if columns[index % 2].button(example, key=f"example_{index}", use_container_width=True):
            return example
    return None


# --- page --------------------------------------------------------------------------------


def main() -> None:
    settings = get_settings()
    try:
        store = get_store(settings)
    except (MissingAPIKeyError, EmbedderMismatchError) as exc:
        st.error(str(exc))
        st.stop()
    log = get_log(settings)

    provider, top_k, agent_mode = sidebar(settings, store, log)
    hero(settings, store, provider)

    history: list[Answer] = st.session_state.setdefault("history", [])
    log_ids: list[int] = st.session_state.setdefault("log_ids", [])

    example = welcome() if not history else None

    for past, past_id in zip(history, log_ids, strict=False):
        with st.chat_message("user", avatar="👤"):
            st.markdown(past.question)
        with st.chat_message("assistant", avatar="📄"):
            render_answer(past, log, past_id)

    limit_reached = len(history) >= settings.max_questions_per_session
    if limit_reached:
        st.info("Dostignut je limit pitanja za ovu sesiju. Osveži stranicu za novi početak.")
    typed = st.chat_input("Postavi pitanje o dokumentima…", disabled=limit_reached)
    question = typed or example
    if not question:
        return
    if len(question) > MAX_QUESTION_CHARS:
        st.warning(f"Pitanje može imati najviše {MAX_QUESTION_CHARS} znakova.")
        return
    if not store.count():
        st.warning("Prvo dodaj dokumente (levo) ili učitaj demo dokumente.")
        return

    with st.chat_message("user", avatar="👤"):
        st.markdown(question)
    with st.chat_message("assistant", avatar="📄"):
        try:
            llm = build_llm(settings, provider)
            recent = [(h.question, h.text) for h in history[-3:]]
            if agent_mode:
                with st.spinner("Agent radi…"):
                    ctx = build_tool_context(settings, store)
                    ctx.top_k = top_k
                    result = run_agent(question, ctx, llm, history=recent)
            else:
                with st.spinner("Tražim u dokumentima…"):
                    result = answer(question, store, llm, k=top_k, history=recent)
        except (MissingAPIKeyError, LLMError) as exc:
            st.error(str(exc))
            return
        log_id = log.log(result, channel="ui", mode="agent" if agent_mode else "rag")
        history.append(result)
        log_ids.append(log_id)
        if example or len(history) == 1:
            st.rerun()  # redraw: drop the welcome card, show "new conversation"
        render_answer(result, log, log_id)


main()
