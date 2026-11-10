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
// Like node.replaceChildren(...), but skips null/undefined/false as el() does (instead of
// printing them as text), so children can be written as `condition && el(...)`.
const fill = (node, ...children) =>
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
// --- scroll to reveal ---------------------------------------------------------------------
// Long lists show one page and load the next when the user scrolls near the end, like a chat:
// no page buttons. `onReach` runs when `marker` comes within 300 px of `root`'s visible area,
// and again after each load while the marker is still in view (a short first page).
function whenReached(marker, root, onReach) {
  let busy = false;
  const observer = new IntersectionObserver(async (entries) => {
    if (busy || !entries.some((entry) => entry.isIntersecting)) return;
    busy = true;
    try {
      await onReach();
    } finally {
      busy = false;
    }
    if (!marker.isConnected) return observer.disconnect();
    observer.unobserve(marker); // observing again reports whether it is still in view
    observer.observe(marker);
  }, { root, rootMargin: "300px" });
  observer.observe(marker);
  return observer;
}

// A list filled page by page from the server: `loadPage(cursor)` returns { items, next }, and
// next is null after the last page. Calling it again starts the list from the top.
function revealList(list, { root, loadPage, render, empty }) {
  list.reveal?.disconnect();
  let cursor = null;
  const marker = el("li", { class: "reveal-more" }, "Učitavam…");
  list.replaceChildren(marker);
  list.reveal = whenReached(marker, root, async () => {
    let page;
    try {
      page = await loadPage(cursor);
    } catch (error) {
      marker.replaceWith(el("li", { class: "empty" }, `Nije učitano: ${error.message}`));
      return;
    }
    marker.before(...page.items.map(render));
    cursor = page.next;
    if (cursor === null || cursor === undefined) {
      marker.remove();
      if (!list.children.length) list.append(el("li", { class: "empty" }, empty));
    }
  });
}

// Page sizes: monitoring lists, saved conversations, documents in the library, and the turns
// of an opened conversation shown at first (older ones appear when scrolling up).
const LIST_PAGE = 20;
const CONVERSATION_PAGE = 30;
const DOCUMENT_PAGE = 50;
const THREAD_PAGE = 10;

const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (path) => `<svg viewBox="0 0 24 24">${path}</svg>`;
const ICONS = {
  open: icon('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  download: icon('<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>'),
  close: icon('<path d="M6 6l12 12M18 6L6 18"/>'),
  pencil: icon('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>'),
  sparkle: icon('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>'),
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
const ROLE_LABELS = { employee: "Zaposleni", editor: "Urednik", admin: "Administrator" };
const TOOL_LABELS = {
  search_documents: "Pretraga dokumenata",
  list_documents: "Spisak dokumenata",
  summarize_document: "Čitanje celog dokumenta",
  escalate_to_human: "Prosleđivanje pitanja nadležnom",
  create_leave_request: "Nacrt zahteva za odmor",
};
// [pravilnik.pdf, str. 2] or several in one bracket: [a.docx, str. 2; b.xlsx, str. 1].
// Any file extension; read the same way as docassist/citations.py — change both together.
const CITE_BRACKET = /\[([^\[\]\n]+)\]/g;
const CITE_PART = /^\s*([^\[\],;\n]+?\.[A-Za-z0-9]{2,5})\s*,\s*(?:str\.?|p\.|pp\.|page|s\.|seite)\s*(\d+)\s*$/i;

function citationParts(inner) {
  const refs = [];
  for (const part of inner.split(";")) {
    const match = part.match(CITE_PART);
    if (!match) return null;  // not (only) citations: leave the bracket as text
    refs.push({ source: match[1].trim(), page: Number(match[2]) });
  }
  return refs;
}

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
  inbox: null, // GET /inbox: the folder whose files are imported automatically
  pickingFolder: false, // the Windows folder dialog is open
  uploading: false, // files are being uploaded one by one (the upload loads the list itself)
  conversationsMore: false, // more saved conversations to load when the list is scrolled
  docFilter: "", // the library search the list was drawn for
  docsShown: DOCUMENT_PAGE, // documents drawn so far (more appear when scrolling)
  olderTurns: [], // turns of the opened conversation not drawn yet, oldest first
  users: [], // GET /users, for the administrator's panel
  scope: loadScope(), // doc_ids to search; empty = all documents
};

// --- search scope: which documents the questions search ---------------------------------

function loadScope() {
  try {
    return new Set(JSON.parse(localStorage.getItem("da-scope") || "[]"));
  } catch {
    return new Set();
  }
}

function saveScope() {
  try { localStorage.setItem("da-scope", JSON.stringify([...state.scope])); } catch {}
}

function toggleScope(docId) {
  if (state.scope.has(docId)) state.scope.delete(docId);
  else state.scope.add(docId);
  saveScope();
  renderDocuments();
}

function clearScope() {
  state.scope.clear();
  saveScope();
  renderDocuments();
}

// The chosen documents above the question box; documents deleted meanwhile are dropped.
function renderScope() {
  const known = new Map(state.documents.map((d) => [d.doc_id, d]));
  for (const id of [...state.scope]) if (!known.has(id)) state.scope.delete(id);
  const bar = $("#scope-bar");
  bar.hidden = state.scope.size === 0;
  bar.replaceChildren(
    el("span", { class: "scope-label" }, "Pretraga samo u:"),
    ...[...state.scope].map((id) => el("span", { class: "scope-chip", title: known.get(id).source },
      prettyName(known.get(id).source),
      el("button", { type: "button", "aria-label": `Ukloni ${known.get(id).source} iz pretrage`, onclick: () => toggleScope(id) }, "×"))),
    el("button", { class: "link", type: "button", onclick: clearScope }, "svi dokumenti"),
  );
}

// --- api --------------------------------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (!response.ok) {
    let detail = `${response.status}`;
    let code;
    try {
      const body = await response.json();
      // A detail of { code, message } names the reason, so the UI can word it itself.
      if (body.detail?.message) ({ code, message: detail } = body.detail);
      else detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    const error = new Error(detail);
    error.status = response.status;
    error.code = code;
    // With accounts: signed out elsewhere, disabled or a new password. Show the sign-in page.
    if (response.status === 401 && state.config?.login) location.reload();
    if (response.status === 503 && detail.includes("being rebuilt")) watchRebuild();
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
  zone.hidden = !state.config.can.edit_documents;
  $("#insights-open").hidden = !state.config.can.see_gaps;
  $("#users-open").hidden = !state.config.can.manage_users;
  const user = state.config.user;  // null without accounts (LOGIN_REQUIRED=false)
  $("#user-chip").hidden = !user;
  if (user) {
    $("#user-name").textContent = user.username;
    $("#user-role").textContent = ROLE_LABELS[user.role] || user.role;
    $("#logout").title = `Odjavi se (${user.username})`;
  }
  $("#dropzone-hint").textContent = `PDF, Word, Excel, PowerPoint, TXT, slike · do ${state.config.max_upload_mb} MB`;
  if (state.config.extensions) $("#file-input").accept = state.config.extensions.join(",");
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
// Documents whose pages can hold pictures: PDFs and images (ADR-029).
const PICTURE_TYPES = new Set(["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"]);

// Progress of documents being read: OCR during upload, or the AI model on request.
function progressText(running) {
  return running
    .map((p) => `${p.kind === "vision" ? "AI model čita slike" : "Čitam skenirane strane (OCR)"}: ${p.source} — ${p.done} / ${p.total}`)
    .join(" · ");
}

// After an update the server rebuilds a stale index by itself (ADR-032): show how far it is,
// then load the documents again.
let rebuildWatch = null;

function watchRebuild() {
  if (rebuildWatch) return;
  const status = el("div", { class: "toast info progress", role: "status" }, "Osvežavam indeks posle nadogradnje…");
  $("#toasts").append(status);
  rebuildWatch = setInterval(async () => {
    let running;
    try {
      running = await api("/documents/progress");
    } catch {
      return; // the server may be restarting: try again
    }
    const rebuild = running.find((p) => p.kind === "reindex");
    if (rebuild) {
      const ocr = running.filter((p) => p.kind === "ocr");
      const step = rebuild.total
        ? `: dokument ${Math.min(rebuild.done + 1, rebuild.total)} / ${rebuild.total}${rebuild.source ? ` (${rebuild.source})` : ""}`
        : "…";
      status.textContent = `Osvežavam indeks posle nadogradnje${step}${ocr.length ? ` · ${progressText(ocr)}` : ""}`;
      // Documents already indexed again are listed right away (ADR-047), not only at the end.
      try { await refreshDocuments(); } catch {}
      return;
    }
    clearInterval(rebuildWatch);
    rebuildWatch = null;
    status.remove();
    try {
      await loadDocuments();
      toast("Indeks je osvežen — možete da postavljate pitanja.");
    } catch (error) {
      // 503: the server restarted and is rebuilding again; api() is already watching it.
      if (error.status !== 503) toast(`Indeks: ${error.message}`, "error");
    }
  }, 1500);
}

async function loadDocuments() {
  state.documents = await api("/documents");
  renderDocuments();
}

// Load the list again, but redraw it only when a document came, went or changed: a redraw
// starts the list from the top.
async function refreshDocuments() {
  const documents = await api("/documents");
  const key = (docs) => docs.map((d) => `${d.doc_id}:${d.chunks}`).join(",");
  if (key(documents) === key(state.documents)) return [];
  const known = new Set(state.documents.map((d) => d.doc_id));
  state.documents = documents;
  renderDocuments();
  return documents.filter((d) => !known.has(d.doc_id));
}

// Documents can also arrive without this page: the inbox folder (ADR-036) or another user.
// While the page is visible, the list is checked now and then and new documents announced.
// The list comes from memory on the server (ADR-042), so asking often costs next to nothing.
const LIBRARY_REFRESH_MS = 5000;

function watchLibrary() {
  setInterval(async () => {
    if (document.hidden || state.pending || rebuildWatch || state.uploading) return;
    loadInbox();
    let added;
    try {
      added = await refreshDocuments();
    } catch {
      return; // the server may be restarting: try again next time
    }
    for (const doc of added) toast(`Novi dokument u biblioteci: ${prettyName(doc.source)}`);
  }, LIBRARY_REFRESH_MS);
}

// --- automatic import from a folder (ADR-037) -------------------------------------------

const JSON_HEADERS = { "Content-Type": "application/json" };
const folderName = (path) => path.split(/[\\/]/).filter(Boolean).pop() || path;

// Serbian plural: count("dokument", "dokumenta", "dokumenata") → 1 dokument, 3 dokumenta, 5 dokumenata.
function plural(n, one, few, many) {
  if (n % 10 === 1 && n % 100 !== 11) return one;
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return few;
  return many;
}

const failedFiles = (n) => `${n} ${plural(n, "fajl nije uspeo", "fajla nisu uspela", "fajlova nije uspelo")}`;

async function loadInbox() {
  if (!state.config.can.manage_inbox) return;  // an administrator's panel
  try {
    state.inbox = await api("/inbox");
  } catch {
    state.inbox = null;
  }
  renderInboxCard();
  if (!$("#inbox").hidden) renderInbox();
}

// The card in the library: is it on, which folder, did something fail.
function renderInboxCard() {
  const card = $("#inbox-card");
  const data = state.inbox;
  card.hidden = !data?.watched || !state.config.can.manage_inbox;
  if (card.hidden) return;
  let status = `Uključen · ${folderName(data.folder)}`;
  let tone = "ok";
  if (!data.enabled) [status, tone] = ["Isključen", "off"];
  else if (!data.available) [status, tone] = [`Folder „${folderName(data.folder)}“ nije dostupan`, "bad"];
  else if (data.failed.length) [status, tone] = [`${folderName(data.folder)} · ${failedFiles(data.failed.length)}`, "warn"];
  $("#inbox-card-status").textContent = status;
  $("#inbox-dot").className = `inbox-dot ${tone}`;
  card.title = data.folder;
}

async function openInbox() {
  openModal($("#inbox"));
  await loadInbox();
}

function renderInbox() {
  const data = state.inbox;
  const body = $("#inbox-body");
  if (!data?.watched) {
    body.replaceChildren(el("p", { class: "muted" }, "Automatski uvoz je isključen u podešavanjima servera (INBOX_POLL_S=0)."));
    return;
  }
  const toggle = el("input", { type: "checkbox", onchange: (e) => updateInbox({ enabled: e.target.checked }) });
  toggle.checked = data.enabled;

  const folderActions = data.desktop
    ? [el("button", { class: "btn small", type: "button", onclick: () => inboxPost("/inbox/open") }, "Otvori folder"),
       el("button", { class: "btn small primary", type: "button", onclick: pickInboxFolder, disabled: state.pickingFolder },
         state.pickingFolder ? "Otvaram prozor…" : "Izaberi drugi folder…")]
    : data.local
      ? [typedFolderForm(data.folder)]
      : [el("small", { class: "muted" }, "Folder se menja na računaru na kom radi aplikacija.")];

  const waiting = !data.available
    ? "Folder trenutno nije dostupan (npr. mrežni disk nije povezan)."
    : data.waiting
      ? `U folderu čeka ${data.waiting} ${plural(data.waiting, "dokument", "dokumenta", "dokumenata")}.`
      : "U folderu nema novih dokumenata — sve je uvezeno.";
  const scan = el("button", { class: "btn small", type: "button", onclick: scanInboxNow }, "Proveri sada");
  scan.disabled = !data.enabled || !data.available;

  fill(body,
    el("label", { class: "inbox-switch" }, toggle, el("span", {}, "Uvoz je uključen")),
    el("div", { class: "inbox-folder" },
      el("div", { class: "inbox-folder-path" }, el("small", {}, "Folder koji se prati"), el("code", {}, data.folder)),
      el("div", { class: "inbox-folder-actions" }, ...folderActions)),
    el("div", { class: "inbox-status" },
      el("span", {}, waiting, el("small", {}, ` Proverava se na ${data.every_s} s.`)), scan),
    data.failed.length > 0 && el("section", {},
      el("h3", {}, "Nije uspelo"),
      el("ul", { class: "q-list" }, ...data.failed.map((f) => el("li", { class: "inbox-failed" },
        el("strong", {}, f.name),
        el("span", {}, f.reason || "Fajl nije mogao da se pročita."),
        el("div", { class: "inbox-row-actions" },
          el("small", {}, when(f.at)),
          el("button", { class: "link", type: "button", onclick: () => retryFailed(f.name) }, "Pokušaj ponovo"),
          el("button", { class: "link danger", type: "button", onclick: () => discardFailed(f.name) }, "Ukloni")))))),
    el("section", {},
      el("h3", {}, "Poslednji uvozi"),
      el("ul", { class: "q-list" }, ...(data.recent.length ? data.recent.map(renderImport)
        : [el("li", { class: "empty" }, "Još ništa nije uvezeno iz foldera.")]))),
  );
}

function renderImport(item) {
  const doc = state.documents.find((d) => d.source === item.source);
  const outcome = item.status === "indexed"
    ? `dodato · ${item.chunks_added} ${plural(item.chunks_added, "pasus", "pasusa", "pasusa")}`
    : item.status === "unchanged" ? "već je bilo u biblioteci"
      : item.error || "nije uspelo";
  const mark = { indexed: "✓", unchanged: "=", failed: "✕" }[item.status] || "·";
  return el("li", {
      class: `inbox-import ${item.status}` + (doc ? " clickable" : ""),
      title: doc ? "Otvori dokument" : undefined,
      onclick: doc ? () => { closeModal($("#inbox"), { restoreFocus: false }); openViewer({ docId: doc.doc_id, source: doc.source }); } : undefined,
    },
    el("span", { class: "inbox-mark", "aria-hidden": "true" }, mark),
    el("span", {}, el("strong", {}, prettyName(item.source)), el("small", {}, `${when(item.at)} · ${outcome}`)));
}

// Without a desktop dialog (e.g. the server runs in Docker on this computer): type the path.
function typedFolderForm(current) {
  const input = el("input", { type: "text", class: "inbox-path-input", value: current, "aria-label": "Putanja foldera" });
  return el("form", { class: "inbox-path-form", onsubmit: (e) => { e.preventDefault(); chooseInboxFolder(input.value); } },
    input, el("button", { class: "btn small primary", type: "submit" }, "Sačuvaj"));
}

async function inboxPost(path) {
  try {
    return await api(path, { method: "POST" });
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

async function updateInbox(change) {
  try {
    state.inbox = await api("/inbox", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(change) });
  } catch (error) {
    toast(error.message, "error");
  }
  await loadInbox();
}

async function chooseInboxFolder(folder) {
  let checked;
  try {
    checked = await api("/inbox/check", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ folder }) });
  } catch (error) {
    toast(error.message, "error"); // e.g. "To je folder same aplikacije…"
    return;
  }
  if (checked.folder === state.inbox.folder) return;
  if (checked.waiting && !confirm(`U folderu „${folderName(checked.folder)}“ već ima ${checked.waiting} dokumenata. `
      + "Biće dodati u biblioteku i premešteni u podfolder „Uvezeno“. Nastaviti?")) return;
  await updateInbox({ folder: checked.folder });
  toast(`Sada se prati folder „${folderName(checked.folder)}“.`);
}

// The Windows dialog takes a second or two to appear: the button says so and cannot open a
// second one meanwhile; it stays busy until the dialog is closed (also when the panel redraws).
async function pickInboxFolder() {
  state.pickingFolder = true;
  renderInbox();
  try {
    const picked = await inboxPost("/inbox/pick");
    if (picked?.folder) await chooseInboxFolder(picked.folder);
  } finally {
    state.pickingFolder = false;
    renderInbox();
  }
}

async function scanInboxNow() {
  const result = await inboxPost("/inbox/scan");
  if (!result) return;
  state.inbox = result;
  const added = result.imported.filter((i) => i.status === "indexed").length;
  const failed = result.imported.filter((i) => i.status === "failed").length;
  const parts = [
    added && `Dodato u biblioteku: ${added} ${plural(added, "dokument", "dokumenta", "dokumenata")}`,
    failed && failedFiles(failed),
  ].filter(Boolean);
  toast(parts.length ? parts.join(" · ") : "Nema novih dokumenata u folderu.", failed ? "error" : "info");
  await loadDocuments();
  renderInboxCard();
  renderInbox();
}

async function retryFailed(name) {
  if (await inboxPost(`/inbox/failed/${encodeURIComponent(name)}/retry`)) await scanInboxNow();
}

async function discardFailed(name) {
  if (!confirm(`Obrisati „${name}“ iz foldera „Nije uspelo“? Fajl se briše i sa diska.`)) return;
  try {
    await api(`/inbox/failed/${encodeURIComponent(name)}`, { method: "DELETE" });
  } catch (error) {
    toast(error.message, "error");
  }
  await loadInbox();
}

function renderDocuments() {
  const filter = $("#doc-filter").value.trim().toLowerCase();
  const list = $("#doc-list");
  if (filter !== state.docFilter) {  // a new search starts from the first page
    state.docFilter = filter;
    state.docsShown = DOCUMENT_PAGE;
  }
  const matching = state.documents.filter((d) => d.source.toLowerCase().includes(filter));
  const docs = matching.slice(0, state.docsShown);
  $("#doc-count").textContent = state.documents.length;
  $("#library-empty").hidden = state.documents.length > 0 || !state.config.can.edit_documents;
  list.reveal?.disconnect();
  list.replaceChildren(
    ...docs.map((doc) => {
      const actions = el("div", { class: "doc-actions" },
        el("button", { class: "icon-btn", title: "Otvori", "aria-label": `Otvori ${doc.source}`, html: ICONS.open,
          onclick: (e) => { e.stopPropagation(); openViewer({ docId: doc.doc_id, source: doc.source }); } }),
        el("a", { class: "icon-btn", title: "Preuzmi original", "aria-label": `Preuzmi ${doc.source}`, html: ICONS.download,
          href: `/documents/${doc.doc_id}/file`, onclick: (e) => e.stopPropagation() }),
        state.config.vision && PICTURE_TYPES.has(ext(doc.source)) && el("button", { class: "icon-btn", title: "Pročitaj slike AI modelom",
          "aria-label": `Pročitaj slike u ${doc.source} AI modelom`, html: ICONS.sparkle,
          onclick: (e) => { e.stopPropagation(); readPictures(doc); } }),
        state.config.can.edit_documents && el("button", { class: "icon-btn danger", title: "Obriši", "aria-label": `Obriši ${doc.source}`, html: ICONS.trash,
          onclick: (e) => { e.stopPropagation(); deleteDocument(doc); } }),
      );
      const picked = state.scope.has(doc.doc_id);
      const pick = el("input", {
        type: "checkbox", class: "doc-pick", title: "Pretraži samo izabrane dokumente",
        "aria-label": `Pretraži samo u ${doc.source}`,
        onclick: (e) => e.stopPropagation(),
        onchange: () => toggleScope(doc.doc_id),
      });
      pick.checked = picked;
      return el("li", {
          class: "doc" + (state.viewing === doc.doc_id ? " active" : "") + (picked ? " picked" : ""), tabindex: 0,
          onclick: () => openViewer({ docId: doc.doc_id, source: doc.source }),
          onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) openViewer({ docId: doc.doc_id, source: doc.source }); },
        },
        pick,
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
  if (matching.length > docs.length) {  // the rest is drawn when the list is scrolled down
    const marker = el("li", { class: "reveal-more" }, `Još ${matching.length - docs.length} dokumenata…`);
    list.append(marker);
    list.reveal = whenReached(marker, list, async () => {
      state.docsShown += DOCUMENT_PAGE;
      renderDocuments();
    });
  }
  renderScope();
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

// Pictures leave the server (Google Gemini), so this is asked for, per document, every time.
async function readPictures(doc) {
  const question = `Strane sa slikama iz „${doc.source}“ biće poslate Google-u (Gemini), da ih AI model pročita ` +
    "bolje od OCR-a (fotografije, cenovnici, tabele u slikama). Besplatni nivo dozvoljava oko 20 strana dnevno; " +
    "već pročitane strane se ne šalju ponovo. Nastaviti?";
  if (!confirm(question)) return;
  const status = el("div", { class: "toast info progress", role: "status" }, `AI model čita slike: ${doc.source}…`);
  $("#toasts").append(status);
  const poll = setInterval(async () => {
    try {
      const running = (await api("/documents/progress")).filter((p) => p.source === doc.source);
      if (running.length) status.textContent = progressText(running);
    } catch {}
  }, 1000);
  try {
    const r = await api(`/documents/${doc.doc_id}/pictures`, { method: "POST" });
    if (r.pages_total === 0) toast(`${r.source}: nema strana sa slikama.`);
    else if (r.error) toast(`${r.source}: AI model je pročitao ${r.pages_read} od ${r.pages_total} strana sa slikama. ` +
      `Ostalo kasnije — ${r.error}`, "error");
    else toast(`${r.source}: AI model je pročitao ${r.pages_read} ${r.pages_read === 1 ? "stranu" : "strana"} sa slikama (${r.chunks} pasusa).`);
  } catch (error) {
    toast(`Čitanje slika nije uspelo: ${error.message}`, "error");
  } finally {
    clearInterval(poll);
    status.remove();
  }
  await loadDocuments();
  if (state.viewing === doc.doc_id) openViewer({ docId: doc.doc_id, source: doc.source });
}

async function uploadFiles(files) {
  if (!files.length) return;
  // One file per request, so each document shows up in the list as soon as it is indexed
  // (ADR-047): a quick text file does not wait for a scan that takes minutes of OCR.
  const status = el("div", { class: "toast info progress", role: "status" });
  $("#toasts").append(status);
  const step = (i) => (files.length > 1 ? `${i + 1} / ${files.length}: ` : "");
  let current = 0;
  const poll = setInterval(async () => {
    try {
      const running = await api("/documents/progress");
      if (running.length) status.textContent = `${step(current)}${progressText(running)}`;
    } catch {}
  }, 1000);
  state.uploading = true;
  try {
    for (const [i, file] of files.entries()) {
      current = i;
      status.textContent = `Indeksiram ${step(i)}${file.name}…`;
      const form = new FormData();
      form.append("files", file);
      try {
        for (const r of await api("/documents", { method: "POST", body: form })) {
          if (r.status === "failed") toast(`${r.source}: ${r.error}`, "error");
          else toast(r.status === "indexed" ? `${r.source}: dodato (${r.chunks_added} pasusa)` : `${r.source}: već postoji`);
        }
      } catch (error) {
        toast(`${file.name}: upload nije uspeo — ${error.message}`, "error");
      }
      try { await loadDocuments(); } catch {}
    }
  } finally {
    state.uploading = false;
    clearInterval(poll);
    status.remove();
  }
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

function openViewer({ docId, source, page, chunkIds = [], quote = "" }) {
  const params = new URLSearchParams();
  if (page) params.set("page", page);
  for (const id of chunkIds) params.append("chunk", id);
  // The start of the cited text: lets the server check old chunk ids after a rebuild (ADR-032).
  const start = quote.replace(/\s+/g, " ").trim().slice(0, 60);
  if (chunkIds.length && start) params.set("q", start);
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
  const chip = (source, page, max) => {
    const citation = byKey.get(`${source.toLowerCase()}|${page}`);
    if (streaming) return `<span class="cite pending" title="${escapeHtml(source)}">${label(source, max)} · ${page}</span>`;
    if (!citation) {
      const title = invalidSet.size ? "Ovaj izvor nije među pronađenim pasusima" : "";
      return `<span class="cite invalid" title="${title}">${label(source, max)} · ${page}</span>`;
    }
    return `<button class="cite" type="button" data-doc="${citation.doc_id}" data-page="${citation.page}" data-key="${escapeHtml(source.toLowerCase())}|${citation.page}" title="Otvori ${escapeHtml(citation.source)}, strana ${citation.page}">${label(citation.source, max)} · ${citation.page}</button>`;
  };
  // Citations become placeholders first, so Markdown and escaping never touch them.
  const inline = (line, max = CITE_LABEL) => {
    const chips = [];
    const marked = line.replace(CITE_BRACKET, (match, inner) => {
      const refs = citationParts(inner);
      if (!refs) return match;
      chips.push(refs.map((r) => chip(r.source, r.page, max)).join(" "));
      return `\u0000${chips.length - 1}\u0000`;
    });
    return escapeHtml(marked)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\u0000(\d+)\u0000/g, (_, i) => chips[Number(i)]);
  };

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
            onclick: () => openViewer({ docId: c.doc_id, source: c.source, page: c.page, chunkIds: c.chunk_ids, quote: c.text }) },
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
  if (data.search_query && data.search_query !== question) meta.push(`tražio sam: „${data.search_query}“`);
  if (data.searched_documents?.length) meta.push(`samo u: ${data.searched_documents.map(prettyName).join(", ")}`);
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
    if (citation) openViewer({ docId: citation.doc_id, source: citation.source, page: citation.page, chunkIds: citation.chunk_ids, quote: citation.text });
  });
  return card;
}

// Smooth while an answer is being written; `instant` when a conversation opens, so the view is
// at the end before "scroll to reveal" checks whether the top of the thread is visible.
function scrollToBottom({ instant = false } = {}) {
  const thread = $("#thread");
  thread.scrollTo({ top: thread.scrollHeight, behavior: instant ? "instant" : "auto" });
}

// After opening a conversation: messages out of view are drawn only when needed (CSS
// content-visibility), so the last ones get their real height only once they are on screen,
// which moves the end. Scroll again after they are drawn.
function settleAtBottom() {
  scrollToBottom({ instant: true });
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
    scrollToBottom({ instant: true });
    resolve();
  })));
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
  // Pieces arrive faster than the screen refreshes (a fast model sends dozens per second), and
  // each render parses the whole answer again: draw at most once per frame, with what is there.
  let frame = 0;
  const draw = () => {
    frame = 0;
    if (!live) {
      live = el("article", { class: "answer streaming" }, el("div", { class: "answer-body" }));
      waiting.replaceWith(live);
    }
    const body = live.firstChild;
    body.innerHTML = renderAnswerText(written, [], [], true);
    body.append(el("span", { class: "caret", "aria-hidden": "true" }));
    scrollToBottom();
  };
  const showPiece = (piece) => {
    written += piece;
    if (!frame) frame = requestAnimationFrame(draw);
  };

  try {
    const data = await askStream({
      question,
      provider: state.provider,
      agent: mode === "agent",
      conversation_id: state.conversationId,
      request_id: requestId,
      history: state.history.slice(-3).map((h) => [h.question, h.answer]),
      doc_ids: state.scope.size ? [...state.scope] : undefined, // undefined: all documents
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
    cancelAnimationFrame(frame); // the finished answer (or the error) is already in place
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
  // Screen readers wait for the whole answer instead of reading every piece as it arrives.
  $("#thread").setAttribute("aria-busy", String(busy));
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

// The first page of saved conversations; the next ones load when the list is scrolled down.
async function loadConversations() {
  const query = $("#conv-filter").value.trim();
  let page;
  try {
    page = await api(conversationsUrl(query, 0));
  } catch {
    return;
  }
  state.conversations = page;
  state.conversationsMore = page.length === CONVERSATION_PAGE;
  state.conversationQuery = query;
  renderConversations();
}

async function loadMoreConversations() {
  const query = state.conversationQuery;
  let page;
  try {
    page = await api(conversationsUrl(query, state.conversations.length));
  } catch {
    return;
  }
  if (query !== state.conversationQuery) return; // the search changed meanwhile
  const known = new Set(state.conversations.map((c) => c.id));
  state.conversations.push(...page.filter((c) => !known.has(c.id)));
  state.conversationsMore = page.length === CONVERSATION_PAGE;
  renderConversations();
}

function conversationsUrl(query, offset) {
  const params = new URLSearchParams({ limit: CONVERSATION_PAGE, offset });
  if (query) params.set("q", query);
  return `/conversations?${params}`;
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
  list.reveal?.disconnect();
  if (!state.conversationQuery) {
    $("#conv-count").textContent = `${state.conversations.length}${state.conversationsMore ? "+" : ""}`;
  }
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
  if (state.conversationsMore) {
    const marker = el("li", { class: "reveal-more" }, "Učitavam starije razgovore…");
    list.append(marker);
    list.reveal = whenReached(marker, list, loadMoreConversations);
  }
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
  const thread = $("#thread");
  thread.reveal?.disconnect();
  state.olderTurns = [];
  for (const node of [...thread.querySelectorAll(".msg, .reveal-older")]) node.remove();
}

function turnNode(turn) {
  return el("div", { class: "msg" },
    el("div", { class: "msg-user" }, turn.question),
    renderAnswer(turn.question, turn.response, turn.mode));
}

// A long conversation opens with its last turns; older ones appear when scrolling up, and the
// view stays on the same message while they are added above it.
function showOlderTurns() {
  const thread = $("#thread");
  const marker = thread.querySelector(".reveal-older");
  const turns = state.olderTurns.splice(-THREAD_PAGE);
  const height = thread.scrollHeight;
  marker.after(...turns.map(turnNode));
  // Stay on the same message: move down by what was added above, without animation.
  thread.scrollTo({ top: thread.scrollTop + thread.scrollHeight - height, behavior: "instant" });
  if (!state.olderTurns.length) {
    thread.reveal?.disconnect();
    marker.remove();
  }
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
  state.olderTurns = conversation.turns.slice(0, -THREAD_PAGE);
  if (state.olderTurns.length) {
    thread.append(el("div", { class: "reveal-older" }, "Starija pitanja se učitavaju dok skroluješ nagore…"));
  }
  thread.append(...conversation.turns.slice(-THREAD_PAGE).map(turnNode));
  state.conversationId = conversation.id;
  state.history = conversation.turns.map((t) => ({ question: t.question, answer: t.response.answer }));
  renderConversations();
  closeLibraryDrawer();
  await settleAtBottom();
  const marker = thread.querySelector(".reveal-older");  // watched once the view is at the end
  if (marker) thread.reveal = whenReached(marker, thread, async () => showOlderTurns());
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
  openModal(modal);
  try {
    const data = await api("/insights");
    // An editor sees only the gaps (what to add to the documents); usage is for administrators.
    const usage = state.config.can.see_usage;
    $("#insights-title").textContent = usage ? "Kako se asistent koristi" : "Šta nedostaje u dokumentima";
    $("#stat-grid").hidden = !usage;
    $("#recent-section").hidden = !usage;
    const s = data.stats || {};
    const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)} %`);
    if (usage) $("#stat-grid").replaceChildren(
      ...[
        ["Pitanja", s.queries],
        ["Iz dokumenata", pct(s.found_rate)],
        ["Prosečno vreme", s.avg_latency_s ? `${s.avg_latency_s} s` : "—"],
        ["Trošak", `$${(s.cost_usd || 0).toFixed(3)}`],
        ["Ocene", `👍 ${s.thumbs_up} · 👎 ${s.thumbs_down}`],
      ].map(([label, value]) => el("div", { class: "stat" }, el("small", {}, label), el("strong", {}, value))),
    );
    const canDelete = state.config.can.delete_questions;  // administrators; off in the public demo
    const item = (q) => el("li", {
        class: `clickable${canDelete ? " removable" : ""}`, tabindex: 0, title: "Postavi ponovo",
        onclick: () => { closeModal($("#insights"), { restoreFocus: false }); const input = $("#composer-input"); input.value = q.question; autosize(input); input.focus(); },
      },
      q.question,
      el("small", {}, [new Date(q.created_at).toLocaleString("sr-RS"), q.found ? "odgovoreno" : "nema u dokumentima",
        q.feedback === -1 ? "👎" : q.feedback === 1 ? "👍" : "", q.mode === "agent" ? "agent" : ""].filter(Boolean).join(" · ")),
      canDelete && el("button", { class: "icon-btn q-delete", type: "button", title: "Obriši pitanje",
        "aria-label": `Obriši pitanje: ${q.question}`, html: ICONS.close,
        onclick: (event) => { event.stopPropagation(); deleteQuery(q); } }));
    $("#queries-clear").hidden = !canDelete || !s.queries;
    // Both lists load page by page while the panel is scrolled (GET /queries).
    const root = $("#insights .modal-card");
    const pages = (kind) => (before) => api(`/queries?kind=${kind}&limit=${LIST_PAGE}${before ? `&before=${before}` : ""}`);
    revealList($("#gaps"), { root, loadPage: pages("gaps"), render: item, empty: "Nema praznina — svako pitanje je imalo odgovor." });
    if (usage) revealList($("#recent"), { root, loadPage: pages("recent"), render: item, empty: "Još nema pitanja." });
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

// --- users and roles, for administrators (ADR-049) --------------------------------------

const USER_ERRORS = {
  taken: "Korisnik sa tim imenom već postoji.",
  username: "Korisničko ime ima 3 do 64 slova, cifara ili znakova . _ - @, bez razmaka.",
  password: "Lozinka mora imati najmanje 8 znakova.",
  last_admin: "Ovo je jedini administrator — prvo napravi još jednog.",
  self: "Svoju ulogu i svoj nalog ne možeš da menjaš ovde.",
  missing: "Taj korisnik više ne postoji.",
};
const userError = (error) => USER_ERRORS[error.code] || error.message;

function roleSelect(value, attrs = {}) {
  const select = el("select", attrs,
    ...Object.entries(ROLE_LABELS).map(([id, label]) => el("option", { value: id }, label)));
  select.value = value;
  return select;
}

async function openUsers() {
  openModal($("#users"));
  await loadUsers();
}

async function loadUsers() {
  try {
    state.users = await api("/users");
  } catch (error) {
    toast(`Korisnici: ${userError(error)}`, "error");
    return;
  }
  renderUsers();
}

function renderUsers() {
  $("#user-list").replaceChildren(...state.users.map((user) => {
    const role = roleSelect(user.role, {
      "aria-label": `Uloga: ${user.username}`, disabled: user.me,
      title: user.me ? "Svoju ulogu ne možeš da promeniš" : undefined,
      onchange: (e) => changeUser(user, { role: e.target.value }, `${user.username} je sada ${ROLE_LABELS[e.target.value].toLowerCase()}.`),
    });
    return el("li", { class: "user-row" + (user.disabled ? " off" : "") },
      el("span", { class: "user-who" },
        el("strong", {}, user.username),
        el("small", {}, [user.me && "to si ti", user.disabled && "isključen — ne može da se prijavi",
          `od ${new Date(user.created_at).toLocaleDateString("sr-RS")}`].filter(Boolean).join(" · "))),
      role,
      el("div", { class: "inbox-row-actions" },
        // Not for oneself: a new password signs its owner out everywhere, this browser too.
        !user.me && el("button", { class: "link", type: "button", onclick: (e) => passwordForm(e.currentTarget.closest("li"), user) }, "Nova lozinka"),
        !user.me && el("button", { class: "link", type: "button",
          onclick: () => changeUser(user, { disabled: !user.disabled },
            user.disabled ? `${user.username} ponovo može da se prijavi.` : `${user.username} je isključen i odjavljen.`) },
          user.disabled ? "Uključi" : "Isključi"),
        !user.me && el("button", { class: "link danger", type: "button", onclick: () => removeUser(user) }, "Obriši")));
  }));
}

// "Nova lozinka" turns the row into a small form; the user is then signed out everywhere.
function passwordForm(row, user) {
  const input = el("input", { type: "password", autocomplete: "new-password", minlength: 8, maxlength: 200, required: true,
    placeholder: "Nova lozinka (najmanje 8 znakova)", "aria-label": `Nova lozinka: ${user.username}` });
  const form = el("form", { class: "user-password",
      onsubmit: (e) => { e.preventDefault(); changeUser(user, { password: input.value }, `${user.username}: nova lozinka je sačuvana, odjavljen je sa svih uređaja.`); } },
    input,
    el("button", { class: "btn small primary", type: "submit" }, "Sačuvaj"),
    el("button", { class: "btn small", type: "button", onclick: renderUsers }, "Otkaži"));
  row.replaceChildren(el("span", { class: "user-who" }, el("strong", {}, user.username)), form);
  input.focus();
}

async function changeUser(user, change, done) {
  try {
    await api(`/users/${user.id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(change) });
    toast(done);
  } catch (error) {
    toast(userError(error), "error");
  }
  await loadUsers();  // also puts a refused choice back
}

async function removeUser(user) {
  if (!confirm(`Obrisati nalog „${user.username}“ i sve njegove sačuvane razgovore? Ovo ne može da se vrati.\n\n` +
    "Ako samo treba da mu se zabrani pristup, izaberi „Isključi“.")) return;
  try {
    await api(`/users/${user.id}`, { method: "DELETE" });
    toast(`Nalog ${user.username} je obrisan.`);
  } catch (error) {
    toast(userError(error), "error");
  }
  await loadUsers();
}

async function addUser(form) {
  const name = $("#new-username");
  const password = $("#new-password");
  const role = $("#new-role");
  try {
    const user = await api("/users", { method: "POST", headers: JSON_HEADERS,
      body: JSON.stringify({ username: name.value, password: password.value, role: role.value }) });
    toast(`Dodat je ${user.username} (${ROLE_LABELS[user.role].toLowerCase()}). Javi mu korisničko ime i lozinku.`);
    form.reset();
    role.value = "employee";
  } catch (error) {
    toast(userError(error), "error");
    return;
  }
  await loadUsers();
}

// --- dialogs ----------------------------------------------------------------------------
// A dialog takes the keyboard focus when it opens and gives it back to the button that opened
// it when it closes; while it is open, Tab stays inside (what aria-modal="true" promises).

const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])";

function openModal(modal) {
  if (!modal.hidden) return;
  modal.opener = document.activeElement;
  modal.hidden = false;
  $(`#${modal.id}-close`, modal)?.focus();
}

function closeModal(modal, { restoreFocus = true } = {}) {
  if (modal.hidden) return;
  modal.hidden = true;
  if (restoreFocus && modal.opener?.isConnected) modal.opener.focus();
  modal.opener = null;
}

function trapFocus(event) {
  if (event.key !== "Tab") return;
  const modal = [...document.querySelectorAll(".modal")].find((m) => !m.hidden);
  if (!modal) return;
  const items = [...modal.querySelectorAll(FOCUSABLE)].filter((node) => node.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const inside = modal.contains(document.activeElement);
  if (!inside || (event.shiftKey && document.activeElement === first)) {
    event.preventDefault();
    (inside ? last : first).focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
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
  document.addEventListener("keydown", trapFocus);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (closeExportMenu()) return;
      if (state.pending) cancelAsk();
      else if (!$("#insights").hidden) closeModal($("#insights"));
      else if (!$("#inbox").hidden) closeModal($("#inbox"));
      else if (!$("#users").hidden) closeModal($("#users"));
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
  $("#insights-close").addEventListener("click", () => closeModal($("#insights")));
  $("#inbox-card").addEventListener("click", openInbox);
  $("#inbox-close").addEventListener("click", () => closeModal($("#inbox")));
  $("#inbox").addEventListener("click", (event) => { if (event.target.id === "inbox") closeModal(event.currentTarget); });
  $("#queries-clear").addEventListener("click", clearQueries);
  $("#logout").addEventListener("click", async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch {}
    location.reload();  // the server now shows the sign-in page
  });
  $("#insights").addEventListener("click", (event) => { if (event.target.id === "insights") closeModal(event.currentTarget); });
  $("#users-open").addEventListener("click", openUsers);
  $("#users-close").addEventListener("click", () => closeModal($("#users")));
  $("#users").addEventListener("click", (event) => { if (event.target.id === "users") closeModal(event.currentTarget); });
  $("#new-role").replaceWith(roleSelect("employee", { id: "new-role", "aria-label": "Uloga novog korisnika" }));
  $("#user-add").addEventListener("submit", (event) => { event.preventDefault(); addUser(event.currentTarget); });
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
  } catch (error) {
    toast(`Server nije dostupan: ${error.message}`, "error");
    return;
  }
  try {
    await loadDocuments();
  } catch (error) {
    if (!rebuildWatch) toast(`Dokumenti: ${error.message}`, "error"); // else: progress is shown
  }
  try {
    await loadConversations();
  } catch (error) {
    toast(`Razgovori: ${error.message}`, "error");
  }
  loadInbox();
  watchLibrary();
}

main();
