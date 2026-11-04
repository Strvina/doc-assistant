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

Python 3.12 · uv · Claude API (OpenAI opciono, ADR-018) · besplatni Groq/Gemini za demo (ADR-021/022) · lokalni višejezični embeddings
(fastembed, ADR-019) · ChromaDB · FastAPI + sopstveni web UI (ADR-020) · n8n · ruff + pytest ·
GitHub Actions · Docker / Hugging Face Spaces

## Trenutno stanje

- **Sve što ne traži API ključ je urađeno i spojeno u `main`** (faze 0–7, osim stavki koje traže ključ/nalog).
- **Aplikacija:** `uv run uvicorn docassist.api:app --reload` → <http://localhost:8000>
  - tri kolone: biblioteka (upload, jezik dokumenta, otvori/preuzmi/obriši) · razgovor (Odgovor/Agent) ·
    pregled dokumenta (PDF na tačnoj strani sa **highlight-om** citiranog teksta; DOCX/TXT kao HTML strane);
  - panel *Praćenje*: pitanja, % odgovoreno, vreme, trošak, 👍/👎, **praznine u dokumentaciji**;
  - tamna tema, mobilni prikaz; zaštite na serveru (sesija/API ključ, limiti po sesiji i IP, demo režim).
- **Pretraga (ADR-023, grana `feat/hybrid-search`):** lokalni `paraphrase-multilingual-MiniLM-L12-v2` +
  1 dodatni BM25 pogodak po ključnim rečima; podela po odeljcima (naslov ostaje uz tabelu). Sve ključne
  činjenice stižu do modela za 27/27 pitanja; odgovori sa Groq-om **100 %** (pre: 93 %).
- **OCR (ADR-024, grana `feat/ocr`):** skenovi, slike u PDF-u i upload slika (PNG/JPG/TIFF/BMP) se čitaju
  Tesseract-om lokalno (srp latinica+ćirilica, deu, eng); pregled označava citat i na skenu. Eval sa
  skeniranim demo PDF-ovima: 100 %. Fotografije proizvoda (Lavazza) daju delimičan tekst.
- **Razgovori (ADR-025):** kartica *Razgovori* levo — svako pitanje se čuva, stari razgovor se otvara ili
  briše; dugme za slanje je *Stop* dok se čeka (i `Esc`), zaustavljeno pitanje se ne čuva i vraća se u polje.
- **Praćenje:** pitanja iz loga se brišu pojedinačno (×) ili sva (`DELETE /queries`); zaključano kad je
  `ALLOW_UPLOADS=false` (javni demo). Razgovori ostaju.
- **Testovi:** svi bez mreže (fake modeli, lažni SDK klijenti); `tests/conftest.py` sve putanje preusmerava u tmp.
- **n8n eskalacija provereno uživo:** `uv run python scripts/setup_n8n.py` → webhook → n8n → mejl u Mailpit
  (<http://localhost:8025>); radi i iz koda aplikacije (`WebhookEscalator`).
- **Pravi AI odgovori bez plaćanja (grana `feat/gemini`):** Claude kredit nije moguće uplatiti (banka),
  pa demo radi na besplatnim nivoima: **Groq** `openai/gpt-oss-120b` (glavni, ~1 s) → `gpt-oss-20b` →
  **Gemini** `gemini-3.5-flash` → `gemini-flash-lite-latest` (lanac, ADR-022). Spojeno u `main` (#12). Gemini free: samo 20 zahteva/dan za 3.5-flash.
- **Prosleđivanje pitanja timu:** ispod odgovora "nema u dokumentima" dugme po timu (`ESCALATION_TEAMS`,
  podrazumevano HR / Održavanje / Kontrola kvaliteta) → `POST /escalate` → n8n → mejl na adresu tima;
  agent bira tim sam. Jedan mejl po pitanju.
- **Nije provereno uživo:** Claude (nema kredita), Docker image build aplikacije.
- **Blokirano:**
  - 0.5 zaštita grane `main` — nije uključena (sekcija uklonjena iz `WORKFLOW.md`).

## Sledeći korak

**Odluka (2026-11-04): online demo se za sada NE objavljuje — samo razvoj aplikacije.**
Sve za Hugging Face je spremno (`docs/DEPLOY.md`: Groq/Gemini, `EMBED_ORIGINS`, LFS u workflow-u);
deploy workflow se sam preskače dok GitHub varijabla `HF_SPACE` nije postavljena.

1. Korisnik je tražio sve: ✅ streaming; zatim pretraga i preimenovanje razgovora, izvoz razgovora
   (PDF/Word), Excel i PowerPoint dokumenti (+ ispravka: citati iz slika `.png/.jpg` se ne prepoznaju u
   `rag._CITATION` i `app.js CITATION`), AI model za fotografije/tabele u slikama (opt-in, Gemini).
2. Eval uvek na čistom indeksu (samo demo dokumenti): `INDEX_DIR`/`DOCUMENTS_DIR`/`LOG_DB` u privremeni folder.
3. Treniranje modela: korisnik je odložio (opcija A = dotreniranje embeddings modela, kad se vrati na to).
4. Claude eval (6.3) kad bude moguće platiti kredit.

## Napomene za sledeću sesiju

- `Page.page_no` je pozicija u originalu; prazne strane se preskaču bez pomeranja numeracije.
- DOCX "strane" su delovi između eksplicitnih preloma strane (ADR-010); demo DOCX-ovi ih imaju.
- PDF se čita po blokovima (`pdf_page_blocks`) — iste blokove koristi viewer za highlight (ADR-012).
- PDF se otvara iz memorije: na Windows-u neuspelo otvaranje sa diska zaključava fajl.
- Chunk-ovi ne prelaze granicu strane; `chunk_id` = `izvor::pN::i`, `doc_id` = hash sadržaja fajla.
- Indeks pamti embedder; promena embedder-a/modela traži `index --rebuild` (ADR-013).
- Bez ključa: `LLM_PROVIDER=fake` ("odgovor" = prva rečenica najboljeg pasusa); pretraga je i tada prava.
- Originali dokumenata se čuvaju u `DOCUMENTS_DIR` (`Library`); dokumenti indeksirani pre toga nemaju
  original → `index --rebuild` ili ponovni upload.
- Claude: zvanični SDK, refusal fallback, `ANTHROPIC_EFFORT` (npr. `low`) je poluga za cenu/brzinu (ADR-014).
- Frontend (`src/docassist/web/`) nema build korak; menja se direktno, server servira statiku.
- `renderAnswerText` (app.js) prikazuje Markdown iz odgovora: tabele, `#` naslove, liste, **bold**, `kod`.
- n8n 2.x: posle `update:workflow --active=true` treba restart; webhook se registruje par sekundi posle `/healthz`.
- Sesije su u SQLite (`sessions`), kolačić 1 godina → razgovori preživljavaju restart; vlasnik razgovora
  je `session:<id>` ili `api`/`open`.
- OCR strane idu paralelno (`ocr.run_jobs`, CPU-1 Tesseract procesa, `OMP_THREAD_LIMIT=1`): sken od 96 strana
  ~2 min (ranije ~6,5). UI prati `GET /documents/progress` i pokazuje "Čitam skenirane strane: 23 / 96".
- Tesseract se zove direktno (`ingest/ocr.py`, TSV); PyMuPDF-ov OCR je preskakao tabele. Windows: traži se
  u `C:/Program Files/Tesseract-OCR`. OCR strane u pregledu se radi ponovo (~1,5 s).
- Indeks pamti embedder i verziju podele (`CHUNKING` u `ingest/chunker.py`); promena → `--rebuild`.
- Groq: 8.000 tokena/min po modelu → 413 za velike zahteve (agent čita ceo dokument); lanac prelazi dalje.
- gpt-oss ponekad citira sa `【...】`; `chat_completions._clean` to pretvara u `[...]`.
- Statika (`app.css`/`app.js`) se u `index.html` servira sa `?v=<hash>` — nema zastarelog CSS-a posle izmene.
- `pymupdf` je namerno na 1.x (ADR-009). Lokalni venv je na Python 3.14, CI i Docker na 3.12.

## Otvorena pitanja

- Da li uključiti zaštitu grane `main` (0.5)?
- Posle evaluacije sa Claude-om: Sonnet ili Haiku za javni demo (cena vs kvalitet)?

## Dnevnik sesija

| Datum | Šta je urađeno | PR |
|---|---|---|
| 2026-11-03 | Ideja, plan, skelet repozitorijuma i CI | — |
| 2026-11-03 | Faza 1: demo dokumenti (SR/DE/EN), loader-i PDF/DOCX/TXT, chunking, testovi; ADR-009–012 | #1 |
| 2026-11-03 | Faza 2: embedder (OpenAI/Fake), Chroma store, retrieve, CLI; ADR-013 | #2 |
| 2026-11-03 | Faza 3: LLM provajderi (Claude/OpenAI/Fake), prompt, answer() sa citatima, CLI ask; ADR-014 | #3 |
| 2026-11-03 | Faza 4: Streamlit UI, zaštite za javni demo, DEPLOY.md; ADR-015 | #4 |
| 2026-11-04 | Faza 5: agent sa alatima, FastAPI, n8n eskalacija; ADR-016 | #5 |
| 2026-11-04 | Faza 6: eval skup + alat, SQLite log, 👍/👎; odluka samo Claude; ADR-017, ADR-018 | #6 |
| 2026-11-04 | Lokalni višejezični embeddings (izmereno: 100 % top-5), chunk 500/100; ADR-019 | #7 |
| 2026-11-04 | Faza 7 (delimično): Docker, README dijagram + Data protection | #8 |
| 2026-11-04 | Svi PR-ovi spojeni u main; redizajn UI-ja + screenshot | #9 |
| 2026-11-04 | Sopstveni web UI (3 kolone, pregled dokumenata sa highlight-om, praćenje + praznine), Library, limiti na serveru, HF Spaces deploy; ADR-020 | #10 |
| 2026-11-04 | n8n + Mailpit jednom komandom (`scripts/setup_n8n.py`), preneseno na novi `main`, provereno uživo (n8n 2.46) | #11 |
| 2026-11-04 | Besplatni modeli: Gemini, pa Groq + lanac rezervi (ADR-021, ADR-022), eval 93 %; dugmad za prosleđivanje pitanja timovima (n8n → mejl timu) | #12 |
| 2026-11-04 | Hibridna pretraga (+1 BM25 pogodak) i podela po odeljcima; eval: činjenice 96→100 %, odgovori 93→100 %, agent 100 %; ADR-023 | #13 |
| 2026-11-04 | OCR (Tesseract) za skenove i slike, upload slika; eval sa skenovima 100 %; ADR-024 | #15 (#14 greškom spojen u `feat/hybrid-search`) |
| 2026-11-04 | OCR paralelno (~3× brže) + prikaz napretka pri uploadu skena | #16 |
| 2026-11-04 | Sačuvani razgovori (otvori/obriši) i zaustavljanje pitanja; ADR-025 | #17 |
| 2026-11-04 | Brisanje pitanja iz praćenja (jedno ili sva; zaključano u demo režimu) | #18 |
| 2026-11-04 | Odgovori: Markdown tabele, naslovi i `kod` se prikazuju formatirano | #19 |
| 2026-11-04 | Priprema za HF Space (iframe/kolačić, LFS, DEPLOY za Groq); demo odložen | #20 |
| 2026-11-04 | Odgovor se ispisuje dok nastaje (`/ask/stream`, NDJSON); Stop prekida i generisanje; ADR-026 | — |
