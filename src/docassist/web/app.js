// Doc Assistant web UI — plain ES module, no build step.

const $ = (selector, root = document) => root.querySelector(selector);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
};
const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (path) => `<svg viewBox="0 0 24 24">${path}</svg>`;
const ICONS = {
  open: icon('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  download: icon('<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>'),
  trash: icon('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  copy: icon('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>'),
  up: icon('<path d="M7 10v10H4V10zM7 10l4-7c1.7 0 2.5 1 2.2 2.6L12.6 9H19a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.8 20H7"/>'),
  down: icon('<path d="M17 14V4h3v10zM17 14l-4 7c-1.7 0-2.5-1-2.2-2.6l.6-3.4H5a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 6.2 4H17"/>'),
};

const EXAMPLES = [
  { q: "Koliko dana godišnjeg odmora imam?", tag: "SR → SR pravilnik" },
  { q: "Koliki je moment pritezanja za M8 vijke na sabirnicama?", tag: "SR → DE uputstvo" },
  { q: "Šta da radim ako se povredim na poslu?", tag: "SR → SR bezbednost" },
  { q: "What is the acceptance criterion for the insulation test?", tag: "EN → EN procedura" },
];
const TOOL_LABELS = {
  search_documents: "Pretraga dokumenata",
  list_documents: "Spisak dokumenata",
  summarize_document: "Čitanje celog dokumenta",
  escalate_to_human: "Prosleđivanje pitanja nadležnom",
  create_leave_request: "Nacrt zahteva za odmor",
};
const CITATION = /\[\s*([^\[\],]+?\.(?:pdf|docx|txt|md))\s*,\s*(?:str\.?|p\.|pp\.|page|s\.|seite)\s*(\d+)\s*\]/gi;

const state = {
  config: null,
  documents: [],
  history: [], // { question, answer }
  mode: "rag",
  provider: null,
  busy: false,
  viewing: null, // doc_id shown in the viewer
};

// --- api --------------------------------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("json") ? response.json() : response.text();
}

function toast(message, kind = "info") {
  const node = el("div", { class: `toast ${kind}`, role: "status" }, message);
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 7000 : 3500);
}

// --- config & model ---------------------------------------------------------------------

async function loadConfig() {
  state.config = await api("/api/config");
  const select = $("#provider-select");
  select.replaceChildren(
    ...state.config.providers.map((p) =>
      el("option", { value: p.id }, p.id === "claude" ? `Claude · ${shortModel(state.config.models.claude)}` : p.label + (p.available ? "" : " (nema ključa)")),
    ),
  );
  const preferred = state.config.providers.find((p) => p.id === state.config.default_provider);
  state.provider = preferred ? preferred.id : state.config.providers[0].id;
  select.value = state.provider;
  updateModelDot();
  select.addEventListener("change", () => {
    state.provider = select.value;
    updateModelDot();
  });
  const zone = $("#dropzone");
  zone.hidden = !state.config.allow_uploads;
  $("#dropzone-hint").textContent = `PDF, Word, TXT, MD · do ${state.config.max_upload_mb} MB`;
}

function shortModel(model) {
  return (model || "").replace(/^claude-/, "").replace(/-(\d)-(\d)$/, " $1.$2");
}

function updateModelDot() {
  const provider = state.config.providers.find((p) => p.id === state.provider);
  const dot = $("#model-dot");
  dot.className = "dot" + (provider.id === "fake" ? " demo" : provider.available ? "" : " off");
  dot.title = provider.id === "fake" ? "Offline demo: probni odgovori bez modela" : provider.available ? "Spremno" : "Nema API ključa";
}

// --- library ----------------------------------------------------------------------------

const ext = (name) => (name.split(".").pop() || "").toLowerCase();

async function loadDocuments() {
  state.documents = await api("/documents");
  renderDocuments();
}

function renderDocuments() {
  const filter = $("#doc-filter").value.trim().toLowerCase();
  const list = $("#doc-list");
  const docs = state.documents.filter((d) => d.source.toLowerCase().includes(filter));
  $("#doc-count").textContent = state.documents.length;
  $("#library-empty").hidden = state.documents.length > 0 || !state.config.allow_uploads;
  list.replaceChildren(
    ...docs.map((doc) => {
      const actions = el("div", { class: "doc-actions" },
        el("button", { class: "icon-btn", title: "Otvori", "aria-label": `Otvori ${doc.source}`, html: ICONS.open,
          onclick: (e) => { e.stopPropagation(); openViewer({ docId: doc.doc_id, source: doc.source }); } }),
        el("a", { class: "icon-btn", title: "Preuzmi original", "aria-label": `Preuzmi ${doc.source}`, html: ICONS.download,
          href: `/documents/${doc.doc_id}/file`, onclick: (e) => e.stopPropagation() }),
        state.config.allow_uploads && el("button", { class: "icon-btn danger", title: "Obriši", "aria-label": `Obriši ${doc.source}`, html: ICONS.trash,
          onclick: (e) => { e.stopPropagation(); deleteDocument(doc); } }),
      );
      return el("li", {
          class: "doc" + (state.viewing === doc.doc_id ? " active" : ""), tabindex: 0,
          onclick: () => openViewer({ docId: doc.doc_id, source: doc.source }),
          onkeydown: (e) => { if (e.key === "Enter") openViewer({ docId: doc.doc_id, source: doc.source }); },
        },
        el("span", { class: `file-badge ${ext(doc.source)}` }, ext(doc.source).toUpperCase()),
        el("div", {},
          el("div", { class: "doc-name", title: doc.source }, prettyName(doc.source)),
          el("div", { class: "doc-meta" },
            doc.language && el("span", { class: "lang" }, doc.language.toUpperCase()),
            `${doc.pages} str. · ${doc.chunks} pasusa`),
        ),
        actions,
      );
    }),
  );
}

function prettyName(source) {
  const base = source.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
  if (base.length <= 3) return base.toUpperCase(); // faq → FAQ
  return base.charAt(0).toUpperCase() + base.slice(1);
}

async function deleteDocument(doc) {
  if (!confirm(`Obrisati „${doc.source}“ iz biblioteke? Briše se i sačuvani original.`)) return;
  try {
    await api(`/documents/${doc.doc_id}`, { method: "DELETE" });
    if (state.viewing === doc.doc_id) closeViewer();
    toast(`Obrisano: ${doc.source}`);
    await loadDocuments();
  } catch (error) {
    toast(`Brisanje nije uspelo: ${error.message}`, "error");
  }
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const file of files) form.append("files", file);
  toast(`Indeksiram ${files.length} ${files.length === 1 ? "dokument" : "dokumenta"}…`);
  try {
    const results = await api("/documents", { method: "POST", body: form });
    for (const r of results) {
      if (r.status === "failed") toast(`${r.source}: ${r.error}`, "error");
      else toast(r.status === "indexed" ? `${r.source}: dodato (${r.chunks_added} pasusa)` : `${r.source}: već postoji`);
    }
  } catch (error) {
    toast(`Upload nije uspeo: ${error.message}`, "error");
  }
  await loadDocuments();
}

async function loadDemo() {
  const button = $("#load-demo");
  button.disabled = true;
  button.textContent = "Indeksiram…";
  try {
    await api("/documents/demo", { method: "POST" });
    toast("Demo dokumenti su učitani.");
  } catch (error) {
    toast(`Nije uspelo: ${error.message}`, "error");
  }
  button.disabled = false;
  button.textContent = "Učitaj demo dokumente";
  await loadDocuments();
}

// --- viewer -----------------------------------------------------------------------------

function openViewer({ docId, source, page, chunkIds = [] }) {
  const params = new URLSearchParams();
  if (page) params.set("page", page);
  for (const id of chunkIds) params.append("chunk", id);
  const base = `/documents/${docId}/view${params.size ? "?" + params : ""}`;
  const isPdf = ext(source) === "pdf";
  const src = isPdf && page ? `${base}#page=${page}&view=FitH` : base;
  const frame = $("#viewer-frame");
  frame.src = "about:blank";
  requestAnimationFrame(() => (frame.src = src));
  $("#viewer-name").textContent = prettyName(source);
  $("#viewer-name").title = source;
  $("#viewer-page").textContent = page ? `${source} · strana ${page}${chunkIds.length ? " · citat označen" : ""}` : `${source} · ceo dokument`;
  const badge = $("#viewer-badge");
  badge.textContent = ext(source).toUpperCase();
  badge.className = `file-badge ${ext(source)}`;
  $("#viewer-newtab").href = src;
  $("#viewer-download").href = `/documents/${docId}/file`;
  $("#viewer").hidden = false;
  $("#layout").classList.add("viewing");
  state.viewing = docId;
  closeLibraryDrawer();
  renderDocuments();
}

function closeViewer() {
  $("#viewer").hidden = true;
  $("#layout").classList.remove("viewing");
  $("#viewer-frame").src = "about:blank";
  state.viewing = null;
  renderDocuments();
}

// --- chat -------------------------------------------------------------------------------

function renderExamples() {
  $("#examples").replaceChildren(
    ...EXAMPLES.map(({ q, tag }) =>
      el("button", { class: "example", type: "button", onclick: () => ask(q) }, el("small", {}, tag), q),
    ),
  );
}

/** Minimal, safe Markdown: paragraphs, bullet/numbered lists, **bold**, citation chips. */
function renderAnswerText(text, citations, invalid) {
  const byKey = new Map(citations.map((c) => [`${c.source.toLowerCase()}|${c.page}`, c]));
  const invalidSet = new Set(invalid);
  const inline = (line) =>
    escapeHtml(line)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(CITATION, (match, source, page) => {
        const citation = byKey.get(`${source.trim().toLowerCase()}|${Number(page)}`);
        if (!citation) {
          const title = invalidSet.size ? "Ovaj izvor nije među pronađenim pasusima" : "";
          return `<span class="cite invalid" title="${title}">${escapeHtml(source)} · ${page}</span>`;
        }
        return `<button class="cite" type="button" data-doc="${citation.doc_id}" data-page="${citation.page}" data-key="${escapeHtml(source.trim().toLowerCase())}|${citation.page}" title="Otvori ${escapeHtml(citation.source)}, strana ${citation.page}">${escapeHtml(citation.source.replace(/\.[^.]+$/, ""))} · ${citation.page}</button>`;
      });

  const blocks = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      const ordered = /^\s*\d/.test(line);
      if (!list || list.ordered !== ordered) {
        list = { ordered, items: [] };
        blocks.push(list);
      }
      list.items.push(inline(bullet[1]));
      continue;
    }
    list = null;
    if (line.trim()) blocks.push({ p: inline(line) });
  }
  return blocks
    .map((b) => (b.items ? `<${b.ordered ? "ol" : "ul"}>${b.items.map((i) => `<li>${i}</li>`).join("")}</${b.ordered ? "ol" : "ul"}>` : `<p>${b.p}</p>`))
    .join("");
}

function renderAnswer(question, data, mode) {
  const card = el("article", { class: "answer" });
  const label = el("div", { class: "answer-label" },
    "Odgovor",
    el("span", { class: `badge ${data.found ? "found" : "notfound"}` }, data.found ? "iz dokumenata" : "nema u dokumentima"),
    mode === "agent" && el("span", { class: "badge agent" }, "agent"),
  );
  const body = el("div", { class: "answer-body" }, label);
  body.insertAdjacentHTML("beforeend", renderAnswerText(data.answer, data.citations, data.invalid_citations));
  card.append(body);

  if (data.tool_calls.length) {
    const steps = el("details", { class: "steps" },
      el("summary", {}, `Koraci agenta (${data.tool_calls.length})`),
      el("ol", {}, ...data.tool_calls.map((call) => {
        const detail = call.input.query || call.input.source ||
          (call.name === "create_leave_request" ? `${call.input.start_date} – ${call.input.end_date}` : "");
        return el("li", { class: call.is_error ? "err" : "" },
          el("strong", {}, TOOL_LABELS[call.name] || call.name), detail ? ` — „${detail}“` : "", call.is_error ? " (greška)" : "");
      })),
    );
    card.append(steps);
  }

  if (data.invalid_citations.length) {
    card.append(el("div", { class: "warning" }, `Odgovor navodi izvore koji nisu među pronađenim pasusima: ${data.invalid_citations.join(", ")}`));
  }

  if (data.citations.length) {
    card.append(el("div", { class: "sources" },
      el("h4", {}, `Izvori · ${data.citations.length}`),
      el("div", { class: "source-cards" }, ...data.citations.map((c) =>
        el("button", { class: "source", type: "button",
            onclick: () => openViewer({ docId: c.doc_id, source: c.source, page: c.page, chunkIds: c.chunk_ids }) },
          el("span", { class: `file-badge ${ext(c.source)}` }, ext(c.source).toUpperCase()),
          el("div", {},
            el("div", { class: "source-title" }, prettyName(c.source), el("span", { class: "pg" }, `str. ${c.page}`)),
            el("div", { class: "source-snippet" }, c.text.replace(/\s+/g, " ")),
          ),
          el("span", { class: "open-hint" }, "Otvori ›"),
        ),
      )),
    ));
  }

  const meta = [data.provider === "fake" ? "offline demo" : data.model, `${data.latency_s.toFixed(1)} s`];
  if (data.input_tokens + data.output_tokens) meta.push(`${(data.input_tokens + data.output_tokens).toLocaleString("sr-RS")} tokena`);
  if (data.cost_usd !== null && data.cost_usd !== undefined) meta.push(`≈ $${data.cost_usd.toFixed(4)}`);
  const up = el("button", { class: "icon-btn", title: "Koristan odgovor", "aria-label": "Koristan odgovor", html: ICONS.up });
  const down = el("button", { class: "icon-btn", title: "Nije koristan", "aria-label": "Nije koristan", html: ICONS.down });
  const rate = async (rating) => {
    const current = up.classList.contains("on") ? 1 : down.classList.contains("on") ? -1 : 0;
    const next = current === rating ? 0 : rating;
    try {
      await api("/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ log_id: data.log_id, rating: next }) });
      up.classList.toggle("on", next === 1);
      down.classList.toggle("on", next === -1);
      if (next === -1) toast("Hvala — pitanje je dodato u „praznine u dokumentaciji“.");
    } catch (error) {
      toast(`Ocena nije sačuvana: ${error.message}`, "error");
    }
  };
  up.addEventListener("click", () => rate(1));
  down.addEventListener("click", () => rate(-1));
  const copy = el("button", { class: "icon-btn", title: "Kopiraj odgovor", "aria-label": "Kopiraj odgovor", html: ICONS.copy,
    onclick: async () => { await navigator.clipboard.writeText(data.answer); toast("Odgovor je kopiran."); } });
  card.append(el("div", { class: "answer-foot" },
    el("span", { class: "meta" }, ...meta.map((m) => el("span", {}, m))),
    copy, data.log_id ? up : null, data.log_id ? down : null));

  card.addEventListener("click", (event) => {
    const chip = event.target.closest(".cite[data-doc]");
    if (!chip) return;
    const citation = data.citations.find((c) => `${c.source.toLowerCase()}|${c.page}` === chip.dataset.key);
    if (citation) openViewer({ docId: citation.doc_id, source: citation.source, page: citation.page, chunkIds: citation.chunk_ids });
  });
  return card;
}

function scrollToBottom() {
  const thread = $("#thread");
  thread.scrollTop = thread.scrollHeight;
}

async function ask(question) {
  question = question.trim();
  if (!question || state.busy) return;
  if (!state.documents.length) {
    toast("Prvo dodaj dokumente u biblioteku (ili učitaj demo dokumente).", "error");
    return;
  }
  state.busy = true;
  $("#send").disabled = true;
  $("#welcome").hidden = true;
  const thread = $("#thread");
  const message = el("div", { class: "msg" }, el("div", { class: "msg-user" }, question));
  const mode = state.mode;
  const waiting = el("div", { class: "answer thinking" },
    el("span", { class: "pulse", "aria-hidden": "true" }, el("i"), el("i"), el("i")),
    el("span", {}, mode === "agent" ? "Agent bira korake i pretražuje dokumente…" : "Pretražujem dokumente i pišem odgovor…"));
  message.append(waiting);
  thread.append(message);
  scrollToBottom();

  try {
    const data = await api("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        provider: state.provider,
        agent: mode === "agent",
        history: state.history.slice(-3).map((h) => [h.question, h.answer]),
      }),
    });
    waiting.replaceWith(renderAnswer(question, data, mode));
    state.history.push({ question, answer: data.answer });
  } catch (error) {
    const text = error.status === 429 ? "Dostignut je limit pitanja. Pokušaj kasnije." :
      error.status === 503 ? `Model nije dostupan: ${error.message}` : `Greška: ${error.message}`;
    waiting.replaceWith(el("div", { class: "msg-error" }, text));
  } finally {
    state.busy = false;
    $("#send").disabled = false;
    scrollToBottom();
  }
}

// --- insights ---------------------------------------------------------------------------

async function openInsights() {
  const modal = $("#insights");
  modal.hidden = false;
  try {
    const data = await api("/insights");
    const s = data.stats;
    const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)} %`);
    $("#stat-grid").replaceChildren(
      ...[
        ["Pitanja", s.queries],
        ["Iz dokumenata", pct(s.found_rate)],
        ["Prosečno vreme", s.avg_latency_s ? `${s.avg_latency_s} s` : "—"],
        ["Trošak", `$${(s.cost_usd || 0).toFixed(3)}`],
        ["Ocene", `👍 ${s.thumbs_up} · 👎 ${s.thumbs_down}`],
      ].map(([label, value]) => el("div", { class: "stat" }, el("small", {}, label), el("strong", {}, value))),
    );
    const item = (q) => el("li", {
        class: "clickable", tabindex: 0, title: "Postavi ponovo",
        onclick: () => { $("#insights").hidden = true; const input = $("#composer-input"); input.value = q.question; autosize(input); input.focus(); },
      },
      q.question,
      el("small", {}, [new Date(q.created_at).toLocaleString("sr-RS"), q.found ? "odgovoreno" : "nema u dokumentima",
        q.feedback === -1 ? "👎" : q.feedback === 1 ? "👍" : "", q.mode === "agent" ? "agent" : ""].filter(Boolean).join(" · ")));
    const fill = (id, rows, empty) => $(id).replaceChildren(...(rows.length ? rows.map(item) : [el("li", { class: "empty" }, empty)]));
    fill("#gaps", data.gaps, "Nema praznina — svako pitanje je imalo odgovor.");
    fill("#recent", data.recent, "Još nema pitanja.");
  } catch (error) {
    toast(`Statistika nije dostupna: ${error.message}`, "error");
  }
}

// --- layout helpers ---------------------------------------------------------------------

function setMode(mode) {
  state.mode = mode;
  for (const button of document.querySelectorAll(".mode-switch button")) {
    button.setAttribute("aria-checked", String(button.dataset.mode === mode));
  }
  $("#mode-hint").textContent = mode === "agent"
    ? "Agent: sam bira alate — pretraga, ceo dokument, eskalacija, zahtev za odmor"
    : "Odgovor iz pronađenih pasusa";
}

function closeLibraryDrawer() {
  $("#library").classList.remove("open");
  $("#scrim").hidden = true;
}

function autosize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
}

// --- wiring -----------------------------------------------------------------------------

function bindEvents() {
  const input = $("#composer-input");
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value;
    input.value = "";
    autosize(input);
    ask(text);
  });
  input.addEventListener("input", () => autosize(input));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#composer").requestSubmit();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!$("#insights").hidden) $("#insights").hidden = true;
      else if (!$("#viewer").hidden) closeViewer();
      else closeLibraryDrawer();
    }
    if (event.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      event.preventDefault();
      input.focus();
    }
  });

  for (const button of document.querySelectorAll(".mode-switch button")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }
  $("#doc-filter").addEventListener("input", renderDocuments);
  $("#viewer-close").addEventListener("click", closeViewer);
  $("#load-demo").addEventListener("click", loadDemo);
  $("#insights-open").addEventListener("click", openInsights);
  $("#insights-close").addEventListener("click", () => ($("#insights").hidden = true));
  $("#insights").addEventListener("click", (event) => { if (event.target.id === "insights") event.currentTarget.hidden = true; });
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("da-theme", next); } catch {}
  });
  $("#library-toggle").addEventListener("click", () => {
    $("#library").classList.add("open");
    $("#scrim").hidden = false;
  });
  $("#scrim").addEventListener("click", closeLibraryDrawer);

  const zone = $("#dropzone");
  const fileInput = $("#file-input");
  $("#browse").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => { uploadFiles([...fileInput.files]); fileInput.value = ""; });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.add("over"); });
  }
  for (const type of ["dragleave", "drop"]) {
    zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.remove("over"); });
  }
  zone.addEventListener("drop", (event) => uploadFiles([...event.dataTransfer.files]));
}

async function main() {
  bindEvents();
  renderExamples();
  setMode("rag");
  try {
    await loadConfig();
    await loadDocuments();
  } catch (error) {
    toast(`Server nije dostupan: ${error.message}`, "error");
  }
}

main();
