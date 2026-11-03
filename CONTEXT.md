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

- **Faza:** 5 — Agent i automatizacija — **gotova** (grana `feat/agent`, PR #5, nadovezan na PR #4); faza 4 čeka deploy
- **Urađeno:**
  - Faza 0: skelet, planski fajlovi, CI, GitHub repo `Strvina/doc-assistant`, `main` push-ovan.
  - Faza 1 (PR #1): demo dokumenti, loader-i PDF/DOCX/TXT, chunking, `ingest(path)`.
  - Faza 2 (PR #2): `Embedder` (`OpenAIEmbedder`, `FakeEmbedder`), `VectorStore` nad Chroma,
    `retrieve()`, `factory.py`, CLI (`index`, `search`, `docs`, `delete`).
  - Faza 3 (PR #3): `LLMProvider` (`ClaudeProvider`, `OpenAIProvider`, `FakeProvider`),
    sistemski prompt, `rag.answer()` sa proverom citata, CLI `ask`.
  - Faza 4 (PR #4): `app/streamlit_app.py` — upload/lista/brisanje dokumenata, chat sa istorijom,
    citati u expander-ima, izbor modela, zaštite za javni demo; `docs/DEPLOY.md`.
  - Faza 5 (PR #5): agent sa 5 alata (nativne petlje za Claude i OpenAI), FastAPI (`docassist.api`),
    n8n workflow za eskalaciju (`automations/`), agent režim u CLI (`--agent`) i UI.
- **U toku:** —
- **Blokirano / ostalo iz faze 0:**
  - 0.5 zaštita grane `main` nije uključena (sekcija uklonjena iz `WORKFLOW.md` — odlučiti da li ostaje u planu).
  - 0.6 lokalni `.env` sa API ključevima — **bez njega ništa nije probano sa pravim OpenAI embeddings**.

## Sledeći korak

1. Pregledati i spojiti PR-ove redom: #1 → #2 → #3 → #4 → #5 (svaki je napravljen na grani prethodnog).
2. Kad ključevi budu u `.env` (+ `OPENAI_MODEL`): `uv run python -m docassist.cli index data/sample_docs --rebuild`,
   pa `ask "Koliki je moment pritezanja za M8?"` → očekivano 20 Nm, `montageanleitung_pv400.pdf`, str. 2;
   isto sa `--provider openai`.
3. `uv run streamlit run app/streamlit_app.py` → proba u browseru; zatim deploy po `docs/DEPLOY.md` (4.4),
   screenshot u README (4.5) i **prijava na oglas (4.6)**.

## Napomene za sledeću sesiju

- `Page.page_no` je pozicija u originalu; prazne strane se preskaču bez pomeranja numeracije.
- DOCX "strane" su delovi između eksplicitnih preloma strane (ADR-010); demo DOCX-ovi ih imaju.
- PDF se čita po blokovima: jedan pasus / stavka liste / red tabele = jedan red (ADR-012).
- Chunk-ovi ne prelaze granicu strane; `chunk_id` = `izvor::pN::i`, `doc_id` = hash sadržaja fajla.
- Indeks pamti embedder; promena embedder-a traži `index --rebuild` (ADR-013).
- `EMBEDDING_PROVIDER=fake` + `LLM_PROVIDER=fake` = ceo tok bez ključa (poklapanje reči, "odgovor" = citat prve rečenice).
- Agent petlje (Claude/OpenAI) su testirane samo sa lažnim klijentima — prvo pravo pokretanje
  `ask --agent` sa ključem je važna provera (posebno OpenAI `include=reasoning.encrypted_content`).
- n8n workflow nije importovan u živi n8n — proveriti po `automations/README.md`.
- Claude ide preko zvaničnog SDK-a sa refusal fallback-om; `ANTHROPIC_EFFORT` (npr. `low`) je poluga za cenu/brzinu (ADR-014).
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
| 2026-11-03 | Faza 3: LLM provajderi (Claude/OpenAI/Fake), prompt, answer() sa citatima, CLI ask; ADR-014 | #3 |
| 2026-11-03 | Faza 4: Streamlit UI, zaštite za javni demo, DEPLOY.md; ADR-015 | #4 |
| 2026-11-04 | Faza 5: agent sa alatima, FastAPI, n8n eskalacija; ADR-016 | #5 |
