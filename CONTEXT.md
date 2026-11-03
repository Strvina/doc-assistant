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

- **Faza:** 2 — Indeks i pretraga — **gotova** (grana `feat/retrieval`, PR #2, nadovezan na PR #1)
- **Urađeno:**
  - Faza 0: skelet, planski fajlovi, CI, GitHub repo `Strvina/doc-assistant`, `main` push-ovan.
  - Faza 1 (PR #1): demo dokumenti, loader-i PDF/DOCX/TXT, chunking, `ingest(path)`.
  - Faza 2 (PR #2): `Embedder` (`OpenAIEmbedder`, `FakeEmbedder`), `VectorStore` nad Chroma,
    `retrieve()`, `factory.py`, CLI (`index`, `search`, `docs`, `delete`).
- **U toku:** —
- **Blokirano / ostalo iz faze 0:**
  - 0.5 zaštita grane `main` nije uključena (sekcija uklonjena iz `WORKFLOW.md` — odlučiti da li ostaje u planu).
  - 0.6 lokalni `.env` sa API ključevima — **bez njega ništa nije probano sa pravim OpenAI embeddings**.

## Sledeći korak

1. Pregledati i spojiti PR #1, pa PR #2 (redom; PR #2 je napravljen na grani `feat/ingest`).
2. Kad ključevi budu u `.env`: `uv run python -m docassist.cli index data/sample_docs --rebuild`
   pa `search "Koliki je moment pritezanja za M8?"` → očekivano `montageanleitung_pv400.pdf`, str. 2.
3. Faza 3 — `feat/llm`: `LLMProvider` (Claude, OpenAI, Fake), prompt, `answer()`.

## Napomene za sledeću sesiju

- `Page.page_no` je pozicija u originalu; prazne strane se preskaču bez pomeranja numeracije.
- DOCX "strane" su delovi između eksplicitnih preloma strane (ADR-010); demo DOCX-ovi ih imaju.
- PDF se čita po blokovima: jedan pasus / stavka liste / red tabele = jedan red (ADR-012).
- Chunk-ovi ne prelaze granicu strane; `chunk_id` = `izvor::pN::i`, `doc_id` = hash sadržaja fajla.
- Indeks pamti embedder; promena embedder-a traži `index --rebuild` (ADR-013).
- `EMBEDDING_PROVIDER=fake` = rad bez ključa (samo poklapanje reči, bez prevoda SR↔DE).
- `pymupdf` je namerno na 1.x (ADR-009). Lokalni venv je trenutno na Python 3.14, CI na 3.12.

## Otvorena pitanja

- Koji hosting za demo (Streamlit Community Cloud / Render / Hugging Face Spaces)? Odlučiti u fazi 4.
- Embeddings: ostati na OpenAI ili dodati lokalni višejezični model? Odlučiti posle evaluacije (faza 6).

## Dnevnik sesija

| Datum | Šta je urađeno | PR |
|---|---|---|
| 2026-11-03 | Ideja, plan, skelet repozitorijuma i CI | — |
| 2026-11-03 | Faza 1: demo dokumenti (SR/DE/EN), loader-i PDF/DOCX/TXT, chunking, testovi; ADR-009–012 | #1 |
| 2026-11-03 | Faza 2: embedder (OpenAI/Fake), Chroma store, retrieve, CLI; ADR-013 | #2 |
