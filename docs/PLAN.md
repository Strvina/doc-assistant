# PLAN — roadmap projekta

Rok oglasa: **3. 12. 2026.** Cilj: MVP + online demo do **~15. 11.**, pa prijava.
Svaka faza = jedna ili više grana/PR-ova. Štikliraj `[x]` kad je stavka spojena u `main`.

---

## Faza 0 — Setup (3–4. 11.)  · grana: `chore/setup`

- [x] 0.1 Skelet repozitorijuma (`src/`, `tests/`, `pyproject.toml`, uv)
- [x] 0.2 Planski fajlovi (CONTEXT, PLAN, ARCHITECTURE, DECISIONS, WORKFLOW)
- [x] 0.3 CI: ruff lint + format check + pytest na svaki PR
- [x] 0.4 GitHub repo kreiran, `main` push-ovan
- [ ] 0.5 Zaštita grane `main` (PR obavezan, CI mora da prođe)
- [ ] 0.6 `.env` lokalno sa API ključevima (Anthropic, OpenAI)

## Faza 1 — Učitavanje dokumenata (5–6. 11.)  · grana: `feat/ingest`

- [x] 1.1 Demo dokumenti u `data/sample_docs/` (izmišljena firma; SR pravilnik o radu,
      DE uputstvo za montažu, EN procedura kontrole kvaliteta, SR bezbednost na radu, FAQ)
- [x] 1.2 Loader-i: PDF (`pymupdf`), DOCX (`python-docx`), TXT/MD → lista `Page(source, page_no, text)`
- [ ] 1.3 Chunking: ~800 karaktera, preklapanje ~150, čuva `source`, `page`, `chunk_id`
- [ ] 1.4 Testovi za loader-e i chunking — loader testovi urađeni uz 1.2 (`tests/test_loaders.py`,
      fixture fajlovi se prave u `tmp_path`); ostaju testovi za chunking

**Gotovo kad:** `ingest("fajl.pdf")` vraća chunk-ove sa ispravnim metapodacima, testovi prolaze.

## Faza 2 — Indeks i pretraga (7–8. 11.)  · grana: `feat/retrieval`

- [ ] 2.1 `Embedder` interfejs + `OpenAIEmbedder` + `FakeEmbedder` za testove
- [ ] 2.2 `VectorStore` nad ChromaDB (persist u `data/index/`): add, search, delete po dokumentu
- [ ] 2.3 `retrieve(question, k=5)` → chunk-ovi sa skorom
- [ ] 2.4 CLI skripta: `uv run python -m docassist.cli index data/sample_docs` i `ask "pitanje"`
- [ ] 2.5 Testovi (sa FakeEmbedder, bez mreže)

**Gotovo kad:** iz terminala vidim relevantne chunk-ove za pitanje na srpskom o nemačkom dokumentu.

## Faza 3 — LLM sloj i odgovori sa citatima (9–10. 11.)  · grana: `feat/llm`

- [ ] 3.1 `LLMProvider` interfejs + `ClaudeProvider` + `OpenAIProvider` + `FakeProvider`
- [ ] 3.2 Sistemski prompt: odgovaraj samo iz konteksta, citiraj `[izvor, str. X]`,
      odgovori na jeziku pitanja, reci "Nema toga u dokumentima" kad nema
- [ ] 3.3 `answer(question)` → `Answer(text, citations, provider, latency, tokens)`
- [ ] 3.4 Prekidač provajdera preko `.env` (`LLM_PROVIDER=claude|openai`)
- [ ] 3.5 Testovi: format citata, "ne znam" ponašanje (sa FakeProvider)

## Faza 4 — Streamlit UI + deploy = **MVP** (11–14. 11.)  · grana: `feat/ui`

- [ ] 4.1 Upload dokumenata, lista ubačenih dokumenata, brisanje dokumenta
- [ ] 4.2 Chat sa istorijom; citati prikazani ispod odgovora (klik → tekst chunk-a)
- [ ] 4.3 Izbor modela u sidebar-u (Claude / ChatGPT)
- [ ] 4.4 Deploy online demo + link u README
- [ ] 4.5 README: opis, screenshot, kako pokrenuti
- [ ] **4.6 PRIJAVA NA OGLAS** (CV na srpskom + link na demo i GitHub)

## Faza 5 — Agent i automatizacija (15–20. 11.)  · grana: `feat/agent`

- [ ] 5.1 Tool use (native API, bez framework-a): agent bira između alata
  - `search_documents` — postojeća pretraga
  - `summarize_document` — rezime celog dokumenta
  - `escalate_to_human` — kad nema odgovora: šalje pitanje nadležnom (webhook)
  - `create_leave_request` — iz razgovora popuni zahtev za godišnji odmor (JSON)
- [ ] 5.2 FastAPI: `POST /ask`, `POST /documents`, `GET /health`
- [ ] 5.3 n8n workflow: webhook → email/Slack obaveštenje (export JSON u `automations/`)
- [ ] 5.4 Testovi agent petlje sa FakeProvider-om koji "bira" alat

## Faza 6 — Evaluacija i monitoring (21–25. 11.)  · grana: `feat/eval`

- [ ] 6.1 `eval/questions.yaml`: 25–30 pitanja + očekivani izvor + ključne činjenice
- [ ] 6.2 `uv run python -m docassist.eval` → tačnost izvora, "ne znam" tačnost, latencija, trošak
- [ ] 6.3 Uporedni izveštaj Claude vs OpenAI (tabela u `docs/EVAL_RESULTS.md`)
- [ ] 6.4 Logovanje upita u SQLite (pitanje, odgovor, izvori, latencija, tokeni, ocena)
- [ ] 6.5 👍/👎 u UI + jednostavna statistika

## Faza 7 — Završna obrada (26–30. 11.)  · grana: `chore/polish`

- [ ] 7.1 Dockerfile + `docker compose up` pokreće sve
- [ ] 7.2 README na engleskom: arhitektura (dijagram), odluke, rezultati evaluacije
- [ ] 7.3 Demo video 2–3 min
- [ ] 7.4 Sekcija "Data protection": gde su podaci, brisanje, bez treniranja na podacima

---

## Van opsega (za kasnije, ne raditi sad)

Login/korisnici i prava pristupa · više firmi (multi-tenant) · OCR skeniranih PDF-ova ·
Teams/Slack bot · tender modul
