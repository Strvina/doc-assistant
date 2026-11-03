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

- **Faza:** 0 — Setup
- **Urađeno:** skelet repozitorijuma, planski fajlovi, CI (ruff + pytest), PR template.
- **U toku:** —
- **Blokirano:** —

## Sledeći korak

1. Završiti fazu 0: kreirati GitHub repo, push `main`, uključiti zaštitu grane (vidi `docs/WORKFLOW.md`).
2. Otvoriti granu `feat/ingest` i krenuti sa zadatkom **1.1** iz `docs/PLAN.md`.

## Otvorena pitanja

- Koji hosting za demo (Streamlit Community Cloud / Render / Hugging Face Spaces)? Odlučiti u fazi 4.
- Embeddings: ostati na OpenAI ili dodati lokalni višejezični model? Odlučiti posle evaluacije (faza 6).

## Dnevnik sesija

| Datum | Šta je urađeno | PR |
|---|---|---|
| 2026-11-03 | Ideja, plan, skelet repozitorijuma i CI | — |
