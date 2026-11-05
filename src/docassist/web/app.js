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
  close: icon('<path d="M6 6l12 12M18 6L6 18"/>'),
  pencil: icon('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>'),
  trash: icon('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  copy: icon('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>'),
  up: icon('<path d="M7 10v10H4V10zM7 10l4-7c1.7 0 2.5 1 2.2 2.6L12.6 9H19a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.8 20H7"/>'),
  send: icon('<path d="M4 12l16-8-6 16-2.5-6.5z"/><path d="M11.5 13.5 20 4"/>'),
  check: icon('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
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
const CITATION = /\[\s*([^\[\],]+?\.(?:pdf|docx|txt|md|png|jpe?g|tiff?|bmp))\s*,\s*(?:str\.?|p\.|pp\.|page|s\.|seite)\s*(\d+)\s*\]/gi;

const CITE_LABEL = 24;  // characters of a file name shown in a citation chip
const CITE_LABEL_TABLE = 16;  // … and inside a table cell

const state = {
  config: null,
  documents: [],
  history: [], // { question, answer }
  conversationId: null, // saved conversation the next question continues (null = new)
  conversations: [],
  pending: null, // { controller, requestId, question } while a question is being answered
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
      el("option", { value: p.id }, p.available && state.config.models[p.id] ? `${p.label} · ${shortModel(state.config.models[p.id])}` : p.label + (p.available || p.id === "fake" ? "" : " (nema ključa)")),
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
  const count = `${files.length} ${files.length === 1 ? "dokument" : "dokumenta"}`;
  // A status that stays until the upload is done: scans are read page by page (OCR).
  const status = el("div", { class: "toast info progress", role: "status" }, `Indeksiram ${count}…`);
  $("#toasts").append(status);
  const poll = setInterval(async () => {
    try {
      const running = await api("/documents/progress");
      if (running.length) {
        status.textContent = running
          .map((p) => `Čitam skenirane strane (OCR): ${p.source} — ${p.done} / ${p.total}`)
          .join(" · ");
      }
    } catch {}
  }, 1000);
  try {
    const results = await api("/documents", { method: "POST", body: form });
    for (const r of results) {
      if (r.status === "failed") toast(`${r.source}: ${r.error}`, "error");
      else toast(r.status === "indexed" ? `${r.source}: dodato (${r.chunks_added} pasusa)` : `${r.source}: već postoji`);
    }
  } catch (error) {
    toast(`Upload nije uspeo: ${error.message}`, "error");
  } finally {
    clearInterval(poll);
    status.remove();
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
function renderAnswerText(text, citations, invalid, streaming = false) {
  const byKey = new Map(citations.map((c) => [`${c.source.toLowerCase()}|${c.page}`, c]));
  const invalidSet = new Set(invalid);
  // Long file names would make every citation chip a wide pill; the full name is in the tooltip.
  // In a table cell the chip is shorter still, so the source column stays narrow.
  const label = (source, max) => {
    const name = source.trim().replace(/\.[^.]+$/, "");
    return escapeHtml(name.length > max ? `${name.slice(0, max - 1).trimEnd()}…` : name);
  };
  const inline = (line, max = CITE_LABEL) =>
    escapeHtml(line)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(CITATION, (match, source, page) => {
        const citation = byKey.get(`${source.trim().toLowerCase()}|${Number(page)}`);
        if (streaming) return `<span class="cite pending" title="${escapeHtml(source.trim())}">${label(source, max)} · ${page}</span>`;
        if (!citation) {
          const title = invalidSet.size ? "Ovaj izvor nije među pronađenim pasusima" : "";
          return `<span class="cite invalid" title="${title}">${label(source, max)} · ${page}</span>`;
        }
        return `<button class="cite" type="button" data-doc="${citation.doc_id}" data-page="${citation.page}" data-key="${escapeHtml(source.trim().toLowerCase())}|${citation.page}" title="Otvori ${escapeHtml(citation.source)}, strana ${citation.page}">${label(citation.source, max)} · ${citation.page}</button>`;
      });

  // Markdown that models use in answers: tables, headings, lists, **bold**, `code`.
  const blocks = [];
  let list = null;
  let table = null;
  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => inline(c.trim(), CITE_LABEL_TABLE));
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) {
      list = null;
      if (/^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-")) {  // |---|:---:| separator
        if (table && table.rows.length === 1 && !table.head) table.head = table.rows.pop();
        continue;
      }
      if (!table) {
        table = { head: null, rows: [] };
        blocks.push(table);
      }
      table.rows.push(cells(line));
      continue;
    }
    table = null;
    const heading = line.match(/^\s*(#{1,4})\s+(.*)$/);
    if (heading) {
      list = null;
      blocks.push({ h: inline(heading[2].replace(/\s*#+\s*$/, "")) });
      continue;
    }
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
  const renderTable = (t) => {
    const width = Math.max(...[t.head || [], ...t.rows].map((r) => r.length));
    // Prices and measures ("3.605,00 RSD", "20 Nm") stay on one line, aligned right.
    const numeric = (cell) => /^[\s\d.,:%+\-–€$]*\d[\s\d.,:%+\-–€$]*(?:[a-zA-Z€]{1,4}\.?)?$/.test(cell.replace(/<[^>]+>/g, ""));
    const numericColumn = Array.from({ length: width }, (_, i) => {
      const cells = t.rows.map((r) => r[i] ?? "").filter(Boolean);
      return cells.length > 0 && cells.every(numeric);
    });
    const row = (r, tag) => `<tr>${Array.from({ length: width }, (_, i) =>
      `<${tag}${numericColumn[i] ? ' class="num"' : ""}>${r[i] ?? ""}</${tag}>`).join("")}</tr>`;
    const head = t.head ? `<thead>${row(t.head, "th")}</thead>` : "";
    return `<div class="table-wrap"><table>${head}<tbody>${t.rows.map((r) => row(r, "td")).join("")}</tbody></table></div>`;
  };
  return blocks
    .map((b) => {
      if (b.rows) return renderTable(b);
      if (b.h) return `<h4>${b.h}</h4>`;
      if (b.items) return `<${b.ordered ? "ol" : "ul"}>${b.items.map((i) => `<li>${i}</li>`).join("")}</${b.ordered ? "ol" : "ul"}>`;
      return `<p>${b.p}</p>`;
    })
    .join("");
}

// An unanswered question goes to a team with one click (n8n → e-mail). One button per team
// from ESCALATION_TEAMS; one escalation per question.
function escalatePanel(logId) {
  const teams = state.config.escalation_teams || [];
  const panel = el("section", { class: "escalate", "aria-label": "Prosledi pitanje" });
  const hint = el("p", { class: "escalate-hint" }, "Odgovor nije u dokumentima. Izaberite ko treba da odgovori — dobiće pitanje mejlom.");
  const actions = el("div", { class: "escalate-actions" });

  const send = async (team, button) => {
    actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
    button.classList.add("busy");
    try {
      const result = await api("/escalate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ log_id: logId, team: team.id }) });
      const sentTo = teams.find((t) => t.id === result.team)?.label || team.label;
      panel.classList.add("sent");
      panel.replaceChildren(
        el("span", { class: "escalate-icon", html: ICONS.check }),
        el("div", { class: "escalate-done" },
          el("strong", {}, result.already_sent ? "Pitanje je već prosleđeno" : `Prosleđeno: ${sentTo}`),
          el("span", {}, "Referenca ", el("code", {}, result.reference), " · javiće vam se nadležna osoba."),
        ),
      );
    } catch (error) {
      actions.querySelectorAll("button").forEach((b) => (b.disabled = false));
      button.classList.remove("busy");
      toast(`Pitanje nije prosleđeno: ${error.message}`, "error");
    }
  };

  for (const team of teams) {
    const button = el("button", { class: "btn small escalate-btn", type: "button", title: `Prosledi pitanje: ${team.label}` },
      el("span", { class: "btn-icon", html: ICONS.send }), team.label);
    button.addEventListener("click", () => send(team, button));
    actions.append(button);
  }
  panel.append(el("h4", {}, "Prosledi pitanje"), hint, actions);
  return panel;
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

  const agentEscalated = data.tool_calls.some((c) => c.name === "escalate_to_human" && !c.is_error);

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

  if (!data.found && data.log_id && !agentEscalated && (state.config.escalation_teams || []).length) {
    card.append(escalatePanel(data.log_id));
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
  if (!question || state.pending) return;
  if (!state.documents.length) {
    toast("Prvo dodaj dokumente u biblioteku (ili učitaj demo dokumente).", "error");
    return;
  }
  const requestId = window.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  const pending = { controller: new AbortController(), requestId, question };
  state.pending = pending;
  setBusy(true);
  $("#welcome").hidden = true;
  const thread = $("#thread");
  const message = el("div", { class: "msg" }, el("div", { class: "msg-user" }, question));
  const mode = state.mode;
  const waiting = el("div", { class: "answer thinking" },
    el("span", { class: "pulse", "aria-hidden": "true" }, el("i"), el("i"), el("i")),
    el("span", {}, mode === "agent" ? "Agent bira korake i pretražuje dokumente…" : "Pretražujem dokumente i pišem odgovor…"),
    el("button", { class: "link cancel-link", type: "button", onclick: cancelAsk }, "Zaustavi"));
  message.append(waiting);
  thread.append(message);
  scrollToBottom();

  // While the model writes, the text shows up in a light card; the full card with sources
  // replaces it when the answer is complete.
  let live = null;
  let written = "";
  const showPiece = (piece) => {
    written += piece;
    if (!live) {
      live = el("article", { class: "answer streaming" }, el("div", { class: "answer-body" }));
      waiting.replaceWith(live);
    }
    const body = live.firstChild;
    body.innerHTML = renderAnswerText(written, [], [], true);
    body.append(el("span", { class: "caret", "aria-hidden": "true" }));
    scrollToBottom();
  };

  try {
    const data = await askStream({
      question,
      provider: state.provider,
      agent: mode === "agent",
      conversation_id: state.conversationId,
      request_id: requestId,
      history: state.history.slice(-3).map((h) => [h.question, h.answer]),
    }, pending.controller.signal, showPiece);
    (live || waiting).replaceWith(renderAnswer(question, data, mode));
    state.history.push({ question, answer: data.answer });
    state.conversationId = data.conversation_id;
    loadConversations();
  } catch (error) {
    if (error.name === "AbortError" || error.status === 409) {
      // Cancelled: the question disappears and comes back into the input to be corrected.
      message.remove();
      if (!$("#thread .msg")) $("#welcome").hidden = false;
      const input = $("#composer-input");
      if (!input.value.trim()) { input.value = question; autosize(input); }
      input.focus();
      toast("Pitanje je zaustavljeno — ispravi ga i pošalji ponovo.");
    } else {
      const text = error.status === 429 ? "Dostignut je limit pitanja. Pokušaj kasnije." :
        error.status === 503 ? `Model nije dostupan: ${error.message}` : `Greška: ${error.message}`;
      (live || waiting).replaceWith(el("div", { class: "msg-error" }, text));
    }
  } finally {
    if (state.pending === pending) state.pending = null;
    setBusy(false);
    scrollToBottom();
  }
}

// POST /ask/stream: newline-delimited JSON events — "delta" pieces, then "done" or "error".
async function askStream(body, signal, onPiece) {
  const response = await fetch("/ask/stream", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {  // refused before the answer started (limits, unknown conversation…)
    let detail = `${response.status}`;
    try { const data = await response.json(); detail = typeof data.detail === "string" ? data.detail : detail; } catch {}
    throw Object.assign(new Error(detail), { status: response.status });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.type === "delta") onPiece(event.text);
      else if (event.type === "done") return event.response;
      else if (event.type === "error") throw Object.assign(new Error(event.message), { status: event.status });
    }
  }
  throw new Error("Odgovor je prekinut.");
}

function setBusy(busy) {
  const send = $("#send");
  send.classList.toggle("stop", busy);
  send.setAttribute("aria-label", busy ? "Zaustavi" : "Pošalji");
  send.title = busy ? "Zaustavi pitanje (Esc)" : "Pošalji";
}

function cancelAsk() {
  const pending = state.pending;
  if (!pending) return;
  // Tell the server first, so the answer that may still arrive is not saved; then stop waiting.
  api("/ask/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: pending.requestId }),
  }).catch(() => {});
  pending.controller.abort();
}

// --- saved conversations ----------------------------------------------------------------

function setSideTab(tab) {
  const convs = tab === "convs";
  $("#tab-docs").setAttribute("aria-selected", String(!convs));
  $("#tab-convs").setAttribute("aria-selected", String(convs));
  $("#docs-panel").hidden = convs;
  $("#conv-panel").hidden = !convs;
  try { localStorage.setItem("da-side-tab", tab); } catch {}
}

async function loadConversations() {
  const query = $("#conv-filter").value.trim();
  try {
    state.conversations = await api(`/conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`);
  } catch {
    return;
  }
  state.conversationQuery = query;
  renderConversations();
}

function when(iso) {
  const date = new Date(iso);
  const now = new Date();
  const time = date.toLocaleTimeString("sr-RS", { hour: "2-digit", minute: "2-digit" });
  const days = Math.round((new Date(now.toDateString()) - new Date(date.toDateString())) / 86400000);
  if (days === 0) return `danas ${time}`;
  if (days === 1) return `juče ${time}`;
  return date.toLocaleDateString("sr-RS", { day: "numeric", month: "numeric", year: days > 300 ? "numeric" : undefined });
}

function renderConversations() {
  const list = $("#conv-list");
  if (!state.conversationQuery) $("#conv-count").textContent = state.conversations.length;
  const empty = $("#conv-empty");
  empty.hidden = state.conversations.length > 0;
  empty.innerHTML = state.conversationQuery
    ? "Nijedan razgovor ne sadrži tu reč."
    : "Još nema sačuvanih razgovora.<br>Svako pitanje se čuva automatski.";
  list.replaceChildren(...state.conversations.map((c) => {
    const remove = el("span", { class: "conv-actions" },
      el("button", { class: "icon-btn conv-export", type: "button", title: "Preuzmi kao PDF ili Word",
        "aria-label": `Preuzmi razgovor: ${c.title}`, "aria-haspopup": "menu", html: ICONS.download,
        onclick: (event) => { event.stopPropagation(); toggleExportMenu(event.currentTarget, c); } }),
      el("button", { class: "icon-btn conv-edit", type: "button", title: "Preimenuj",
        "aria-label": `Preimenuj razgovor: ${c.title}`, html: ICONS.pencil,
        onclick: (event) => { event.stopPropagation(); startRename(event.currentTarget.closest(".conv"), c); } }),
      el("button", { class: "icon-btn conv-delete", type: "button", title: "Obriši razgovor",
        "aria-label": `Obriši razgovor: ${c.title}`, html: ICONS.trash,
        onclick: (event) => { event.stopPropagation(); deleteConversation(c); } }));
    const item = el("li", { class: `conv${c.id === state.conversationId ? " active" : ""}`, tabindex: "0",
      title: c.title, onclick: () => openConversation(c.id),
      onkeydown: (event) => { if (event.key === "Enter") openConversation(c.id); } },
      el("span", { class: "conv-title" }, c.title),
      el("span", { class: "conv-meta" }, `${when(c.updated_at)} · ${c.turns} ${c.turns === 1 ? "pitanje" : "pitanja"}`),
      remove);
    return item;
  }));
}

// Export (ADR-027): a small menu under the download button; the links download the file.
function closeExportMenu() {
  const menu = document.querySelector(".export-menu");
  if (!menu) return null;
  menu.remove();
  document.querySelector(".conv-export[aria-expanded]")?.removeAttribute("aria-expanded");
  return menu.dataset.id;
}

function toggleExportMenu(button, conversation) {
  if (closeExportMenu() === conversation.id) return;  // second click closes it
  const item = (format, label) => el("button", { type: "button", role: "menuitem",
    onclick: (event) => { event.stopPropagation(); closeExportMenu(); downloadExport(conversation, format); } }, label);
  const menu = el("div", { class: "export-menu", role: "menu", dataset: { id: conversation.id } },
    item("pdf", "PDF"), item("docx", "Word (.docx)"));
  const box = button.getBoundingClientRect();
  menu.style.top = `${box.bottom + 4}px`;
  menu.style.left = `${Math.max(8, box.right - 150)}px`;
  document.body.append(menu);
  button.setAttribute("aria-expanded", "true");
  menu.querySelector("button").focus();
}

// Fetched rather than a plain link: an error (e.g. a server started before export existed)
// becomes a message instead of a downloaded "export.json" with the error in it.
async function downloadExport(conversation, format) {
  let response;
  try {
    response = await fetch(`/conversations/${encodeURIComponent(conversation.id)}/export?format=${format}`,
      { credentials: "same-origin" });
  } catch {
    toast("Izvoz nije uspeo: server nije dostupan.", "error");
    return;
  }
  if (!response.ok) {
    const hint = response.status === 404 ? " Ako je server pokrenut pre ove verzije, restartuj ga." : "";
    toast(`Izvoz nije uspeo (${response.status}).${hint}`, "error");
    return;
  }
  const header = response.headers.get("content-disposition") || "";
  const utf8 = header.match(/filename\*=UTF-8''([^;]+)/i);
  const name = utf8 ? decodeURIComponent(utf8[1]) : `razgovor.${format}`;
  const url = URL.createObjectURL(await response.blob());
  const link = el("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// Rename in place: the title becomes an input; Enter saves, Esc or leaving the field cancels.
function startRename(item, conversation) {
  const title = item.querySelector(".conv-title");
  const input = el("input", { class: "conv-rename", value: conversation.title, maxlength: "200",
    "aria-label": "Novi naziv razgovora" });
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const value = input.value.trim();
    if (save && value && value !== conversation.title) {
      try {
        await api(`/conversations/${encodeURIComponent(conversation.id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: value }),
        });
      } catch (error) {
        toast(`Preimenovanje nije uspelo: ${error.message}`, "error");
      }
    }
    await loadConversations();
  };
  input.addEventListener("click", (event) => event.stopPropagation());
  input.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  title.replaceWith(input);
  input.focus();
  input.select();
}

function clearThread() {
  for (const node of [...$("#thread").querySelectorAll(".msg")]) node.remove();
}

function newConversation() {
  if (state.pending) cancelAsk();
  state.conversationId = null;
  state.history = [];
  clearThread();
  $("#welcome").hidden = false;
  renderConversations();
  closeLibraryDrawer();
  $("#composer-input").focus();
}

async function openConversation(id) {
  if (state.pending) {
    toast("Sačekaj odgovor ili ga zaustavi, pa otvori drugi razgovor.", "error");
    return;
  }
  let conversation;
  try {
    conversation = await api(`/conversations/${encodeURIComponent(id)}`);
  } catch (error) {
    toast(`Razgovor nije otvoren: ${error.message}`, "error");
    await loadConversations();
    return;
  }
  clearThread();
  $("#welcome").hidden = conversation.turns.length > 0;
  const thread = $("#thread");
  for (const turn of conversation.turns) {
    thread.append(el("div", { class: "msg" },
      el("div", { class: "msg-user" }, turn.question),
      renderAnswer(turn.question, turn.response, turn.mode)));
  }
  state.conversationId = conversation.id;
  state.history = conversation.turns.map((t) => ({ question: t.question, answer: t.response.answer }));
  renderConversations();
  closeLibraryDrawer();
  scrollToBottom();
}

async function deleteConversation(conversation) {
  if (!confirm(`Obrisati razgovor „${conversation.title}“? Ovo ne može da se vrati.`)) return;
  try {
    await api(`/conversations/${encodeURIComponent(conversation.id)}`, { method: "DELETE" });
  } catch (error) {
    toast(`Brisanje nije uspelo: ${error.message}`, "error");
    return;
  }
  if (conversation.id === state.conversationId) newConversation();
  toast("Razgovor je obrisan.");
  await loadConversations();
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
    const canDelete = state.config?.allow_uploads;  // off in the public demo
    const item = (q) => el("li", {
        class: `clickable${canDelete ? " removable" : ""}`, tabindex: 0, title: "Postavi ponovo",
        onclick: () => { $("#insights").hidden = true; const input = $("#composer-input"); input.value = q.question; autosize(input); input.focus(); },
      },
      q.question,
      el("small", {}, [new Date(q.created_at).toLocaleString("sr-RS"), q.found ? "odgovoreno" : "nema u dokumentima",
        q.feedback === -1 ? "👎" : q.feedback === 1 ? "👍" : "", q.mode === "agent" ? "agent" : ""].filter(Boolean).join(" · ")),
      canDelete && el("button", { class: "icon-btn q-delete", type: "button", title: "Obriši pitanje",
        "aria-label": `Obriši pitanje: ${q.question}`, html: ICONS.close,
        onclick: (event) => { event.stopPropagation(); deleteQuery(q); } }));
    $("#queries-clear").hidden = !canDelete || !s.queries;
    const fill = (id, rows, empty) => $(id).replaceChildren(...(rows.length ? rows.map(item) : [el("li", { class: "empty" }, empty)]));
    fill("#gaps", data.gaps, "Nema praznina — svako pitanje je imalo odgovor.");
    fill("#recent", data.recent, "Još nema pitanja.");
  } catch (error) {
    toast(`Statistika nije dostupna: ${error.message}`, "error");
  }
}

async function deleteQuery(query) {
  try {
    await api(`/queries/${query.id}`, { method: "DELETE" });
  } catch (error) {
    toast(`Brisanje nije uspelo: ${error.message}`, "error");
    return;
  }
  await openInsights();
}

async function clearQueries() {
  if (!confirm("Obrisati sva pitanja iz praćenja i statistike? Sačuvani razgovori ostaju. Ovo ne može da se vrati.")) return;
  try {
    const result = await api("/queries", { method: "DELETE" });
    toast(`Obrisano pitanja: ${result.deleted}.`);
  } catch (error) {
    toast(`Brisanje nije uspelo: ${error.message}`, "error");
    return;
  }
  await openInsights();
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
    if (state.pending) {  // the send button is a stop button while waiting
      cancelAsk();
      return;
    }
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
      if (closeExportMenu()) return;
      if (state.pending) cancelAsk();
      else if (!$("#insights").hidden) $("#insights").hidden = true;
      else if (!$("#viewer").hidden) closeViewer();
      else closeLibraryDrawer();
    }
    if (event.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      event.preventDefault();
      input.focus();
    }
  });
  document.addEventListener("click", (event) => {  // a click elsewhere closes the export menu
    if (!event.target.closest(".export-menu")) closeExportMenu();
  });
  $("#conv-list").addEventListener("scroll", closeExportMenu);

  for (const button of document.querySelectorAll(".mode-switch button")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }
  $("#doc-filter").addEventListener("input", renderDocuments);
  $("#tab-docs").addEventListener("click", () => setSideTab("docs"));
  $("#tab-convs").addEventListener("click", () => setSideTab("convs"));
  $("#new-chat").addEventListener("click", newConversation);
  let searchTimer;
  $("#conv-filter").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadConversations, 250);  // search while typing, not on every key
  });
  $("#new-chat-top").addEventListener("click", newConversation);
  $("#viewer-close").addEventListener("click", closeViewer);
  $("#load-demo").addEventListener("click", loadDemo);
  $("#insights-open").addEventListener("click", openInsights);
  $("#insights-close").addEventListener("click", () => ($("#insights").hidden = true));
  $("#queries-clear").addEventListener("click", clearQueries);
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
  try { setSideTab(localStorage.getItem("da-side-tab") === "convs" ? "convs" : "docs"); } catch { setSideTab("docs"); }
  try {
    await loadConfig();
    await loadDocuments();
    await loadConversations();
  } catch (error) {
    toast(`Server nije dostupan: ${error.message}`, "error");
  }
}

main();
