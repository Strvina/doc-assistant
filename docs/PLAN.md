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
- [x] 1.3 Chunking: ~500 karaktera, preklapanje ~100 (ADR-019), čuva `source`, `page`, `chunk_id`
- [x] 1.4 Testovi za loader-e i chunking (`tests/test_loaders.py`, `tests/test_chunker.py`;
      fixture fajlovi se prave u `tmp_path`, bez binarnih fajlova u `tests/fixtures/`)

**Gotovo kad:** `ingest("fajl.pdf")` vraća chunk-ove sa ispravnim metapodacima, testovi prolaze.

## Faza 2 — Indeks i pretraga (7–8. 11.)  · grana: `feat/retrieval`

- [x] 2.1 `Embedder` interfejs + `LocalEmbedder` (podrazumevan, ADR-019) + `OpenAIEmbedder` + `FakeEmbedder` za testove
- [x] 2.2 `VectorStore` nad ChromaDB (persist u `data/index/`): add, search, delete po dokumentu
- [x] 2.3 `retrieve(question, k=5)` → chunk-ovi sa skorom
- [x] 2.4 CLI skripta: `uv run python -m docassist.cli index data/sample_docs` i `search "pitanje"`
      (+ `docs`, `delete`; `ask` sa LLM odgovorom dolazi u fazi 3)
- [x] 2.5 Testovi (sa FakeEmbedder, bez mreže)

**Gotovo kad:** iz terminala vidim relevantne chunk-ove za pitanje na srpskom o nemačkom dokumentu.

## Faza 3 — LLM sloj i odgovori sa citatima (9–10. 11.)  · grana: `feat/llm`

- [x] 3.1 `LLMProvider` interfejs + `ClaudeProvider` + `OpenAIProvider` + `FakeProvider`
- [x] 3.2 Sistemski prompt: odgovaraj samo iz konteksta, citiraj `[izvor, str. X]`,
      odgovori na jeziku pitanja, reci "Nema toga u dokumentima" kad nema
- [x] 3.3 `answer(question)` → `Answer(text, citations, provider, latency, tokens)`
- [x] 3.4 Prekidač provajdera preko `.env` (`LLM_PROVIDER=claude|openai|fake`) i `ask --provider`
- [x] 3.5 Testovi: format citata, "ne znam" ponašanje (sa FakeProvider)

## Faza 4 — Web UI + deploy = **MVP** (11–14. 11.)  · grana: `feat/ui` → `feat/web-ui` (ADR-020)

- [x] 4.1 Upload dokumenata, lista ubačenih dokumenata, brisanje dokumenta
- [x] 4.2 Chat sa istorijom; citati prikazani ispod odgovora (klik → tekst chunk-a)
- [x] 4.3 Izbor modela u sidebar-u (Claude / ChatGPT)
- [ ] 4.4 Deploy online demo + link u README — Hugging Face Space + GitHub Action spremni (`docs/DEPLOY.md`)
- [x] 4.5 README: opis, screenshot, kako pokrenuti
- [x] 4.7 Sopstveni web UI: pregled originala sa označenim citatom, preuzimanje, panel za praćenje (ADR-020)
- [ ] **4.6 PRIJAVA NA OGLAS** (CV na srpskom + link na demo i GitHub)

## Faza 5 — Agent i automatizacija (15–20. 11.)  · grana: `feat/agent`

- [x] 5.1 Tool use (native API, bez framework-a): agent bira između alata
  - `search_documents` — postojeća pretraga (+ `list_documents`)
  - `summarize_document` — rezime celog dokumenta
  - `escalate_to_human` — kad nema odgovora: šalje pitanje nadležnom (webhook)
  - `create_leave_request` — iz razgovora popuni zahtev za godišnji odmor (JSON)
- [x] 5.2 FastAPI: `POST /ask`, `POST /documents`, `GET /health` (+ `GET/DELETE /documents`, `X-API-Key`)
- [x] 5.3 n8n workflow: webhook → email/Slack obaveštenje (export JSON u `automations/`) — provereno uživo: n8n 2.46 + Mailpit, `scripts/setup_n8n.py`
- [x] 5.4 Testovi agent petlje sa FakeProvider-om koji "bira" alat

## Faza 6 — Evaluacija i monitoring (21–25. 11.)  · grana: `feat/eval`

- [x] 6.1 `eval/questions.yaml`: 25–30 pitanja + očekivani izvor + ključne činjenice
- [x] 6.2 `uv run python -m docassist.eval` → tačnost izvora, "ne znam" tačnost, latencija, trošak
- [ ] 6.3 Uporedni izveštaj modela u `docs/EVAL_RESULTS.md` — Groq gpt-oss-120b RAG 100 % i agent urađeni (ADR-023); Claude čeka kredit
- [x] 6.4 Logovanje upita u SQLite (pitanje, odgovor, izvori, latencija, tokeni, ocena)
- [x] 6.5 👍/👎 u UI + jednostavna statistika (+ `POST /feedback`, `GET /stats` u API-ju)

## Dodatno — OCR

- [x] OCR za skenirane PDF-ove i tekst na slikama, upload slika (ADR-024)
- [ ] Opcioni AI model za fotografije i tabele u slikama

## Dodatno — razgovori

- [x] Sačuvani razgovori: lista, otvaranje, brisanje (ADR-025)
- [x] Zaustavljanje pitanja dok se odgovara

## Faza 7 — Završna obrada (26–30. 11.)  · grana: `chore/polish`

- [x] 7.1 Dockerfile + `docker compose up` pokreće sve — `compose config` proveren, build nije pokrenut (Docker Desktop nije radio)
- [ ] 7.2 README na engleskom: arhitektura (dijagram), odluke, rezultati evaluacije — urađeno osim rezultata sa Claude-om (6.3)
- [ ] 7.3 Demo video 2–3 min
- [x] 7.4 Sekcija "Data protection": gde su podaci, brisanje, bez treniranja na podacima

---

## Van opsega (za kasnije, ne raditi sad)

Login/korisnici i prava pristupa · više firmi (multi-tenant) · OCR skeniranih PDF-ova ·
Teams/Slack bot · tender modul
