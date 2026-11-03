# CONTEXT — memorija projekta

> **Pravilo:** Na početku svake nove sesije (sa AI asistentom ili sam) prvo pročitaj ovaj fajl,
> pa `docs/PLAN.md`. Na kraju sesije ažuriraj sekcije **Trenutno stanje**, **Sledeći korak**
> i dodaj red u **Dnevnik sesija**. Ovaj fajl se menja u svakom PR-u.

---

## O projektu (ukratko)

**Doc Assistant** — interni AI chatbot koji odgovara na pitanja na osnovu dokumenata
koje firma ubaci (PDF, DOCX, TXT). Svaki odgovor ima citat izvora (dokument + strana).
Ako odgovor nije u dokumentima, bot to kaže umesto da izmišlja.

- **Cilj:** portfolio projekat za prijavu na oglas *eTech Schaltanlagenbau — Stručnjak za AI agente
  i automatizaciju poslovnih procesa (ChatGPT, Claude)*. Rok oglasa: **3. decembar 2026.**
- **Plan prijave:** prijaviti se čim MVP radi i ima online demo (cilj ~15. novembar),
  a ostale faze završavati dok se čeka odgovor.
- **Demo scenario:** izmišljena proizvodna firma (elektro-ormani), dokumenti na srpskom,
  nemačkom i engleskom; korisnik pita na srpskom, dobija odgovor sa citatom iz nemačkog dokumenta.

## Stack (detalji u `docs/DECISIONS.md`)

Python 3.12 · uv · Claude API + OpenAI API (iza zajedničkog interfejsa) · OpenAI embeddings ·
ChromaDB · Streamlit (UI) · FastAPI (API, faza 5) · n8n (automatizacije) · ruff + pytest · GitHub Actions

## Trenutno stanje

- **Faza:** 1 — Učitavanje dokumenata — **gotova** (grana `feat/ingest`, PR #1)
- **Urađeno:**
  - Faza 0: skelet, planski fajlovi, CI, GitHub repo `Strvina/doc-assistant`, `main` push-ovan.
  - 1.1: 5 demo dokumenata izmišljene firme Panelvolt d.o.o. u `data/sample_docs/`
    (2× PDF, 2× DOCX, 1× TXT; SR/DE/EN), generišu se skriptom `scripts/build_sample_docs.py`.
  - 1.2: `docassist.ingest.load_document(path) -> list[Page]` za PDF, DOCX, TXT/MD.
  - 1.3: `chunk_pages()` + `ingest(path) -> list[Chunk]` (800 znakova, preklapanje 150).
  - 1.4: `tests/test_loaders.py`, `tests/test_chunker.py`.
- **U toku:** —
- **Blokirano / ostalo iz faze 0:**
  - 0.5 zaštita grane `main` nije uključena (sekcija uklonjena iz `WORKFLOW.md` — odlučiti da li ostaje u planu).
  - 0.6 lokalni `.env` sa API ključevima — potreban od faze 2 za pravo indeksiranje.

## Sledeći korak

1. Pregledati i spojiti PR #1 (`feat/ingest`).
2. Faza 2 — `feat/retrieval`: `Embedder` + `FakeEmbedder`, Chroma `VectorStore`, `retrieve()`, CLI.

## Napomene za sledeću sesiju

- `Page.page_no` je pozicija u originalu; prazne strane se preskaču bez pomeranja numeracije.
- DOCX "strane" su delovi između eksplicitnih preloma strane (ADR-010); demo DOCX-ovi ih imaju.
- PDF se čita po blokovima: jedan pasus / stavka liste / red tabele = jedan red (ADR-012).
- Chunk-ovi ne prelaze granicu strane; `chunk_id` = `izvor::pN::i`, `doc_id` = hash sadržaja fajla.
- `pymupdf` je namerno na 1.x (ADR-009).

## Otvorena pitanja

- Koji hosting za demo (Streamlit Community Cloud / Render / Hugging Face Spaces)? Odlučiti u fazi 4.
- Embeddings: ostati na OpenAI ili dodati lokalni višejezični model? Odlučiti posle evaluacije (faza 6).

## Dnevnik sesija

| Datum | Šta je urađeno | PR |
|---|---|---|
| 2026-11-03 | Ideja, plan, skelet repozitorijuma i CI | — |
| 2026-11-03 | Faza 1: demo dokumenti (SR/DE/EN), loader-i PDF/DOCX/TXT, chunking, testovi; ADR-009–012 | #1 |
