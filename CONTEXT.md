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
- **Ceo spisak do modela (ADR-030):** uz svaki od 5 pogodaka idu i susedni delovi sa iste strane (+ dalje
  dok traje spisak), delovi iste strane idu kao jedan odlomak; kratka linija sa ":" je naslov odeljka;
  OCR latinične strane bez lažne ćirilice (ponovno čitanje bez `srp`) i bez reči presečenih crticom;
  pravilo `LIST_RULE` u oba prompta. "Kategorije zaraznih bolesti" (sken 96 str.) sada daje svih 8
  kategorija (pre: 1 + pogrešna). Eval 36 pitanja (2 nova sa spiskom): RAG **100 %**, ~1.900 tokena/pitanje.
- **Pitanja sa nastavkom (ADR-031):** kad razgovor ima istoriju, model prvo prepiše pitanje u samostalan
  upit ("a u petu?" → "Koje bolesti spadaju u petu kategoriju zaraznih bolesti?"); UI pokazuje
  "tražio sam: …". Provereno uživo sa Groq-om (nastavak 2/2, nova tema 3/3).
- **Panel „Automatski uvoz iz foldera“ (ADR-037):** kartica u biblioteci → izbor foldera (Windows prozor, ispred browsera — ADR-038),
  uključi/isključi, Proveri sada, neuspeli sa razlogom (Pokušaj ponovo/Ukloni), istorija. Uvezeni fajlovi
  idu u `Uvezeno`, neuspeli u `Nije uspelo` (ništa se ne briše). Menja se samo sa računara servera.
- **Uvoz iz foldera (ADR-036):** fajl ubačen u `data/inbox` se sam doda u biblioteku (provera na 10 s, fajl
  miruje 5 s), pa se ukloni iz foldera; nečitljiv → `inbox/failed/` + razlog. UI javlja novi dokument.
  Provereno uživo (probni server + browser).
- **Nedeljni izveštaj o prazninama (ADR-035):** `GET /reports/gaps` (JSON za n8n, `format=html` pregled) →
  n8n `gap_report_workflow.json` (ponedeljak 8:00 / *Run now*) → mejl po timu. Provereno uživo: mejl
  stigao u Mailpit. `setup_n8n.py --send-gap-report` šalje odmah.
- **Pretraga u izabranim dokumentima (ADR-034):** kvačice u biblioteci → traka "Pretraga samo u: …" iznad
  polja; `doc_ids` u `/ask`; odgovor ima `searched_documents`. Provereno u browseru.
- **Kvalitet koda (ADR-033):** `api.py` podeljen na paket `docassist/api/` (ruteri po oblastima, `deps`,
  `security`, `schemas`); strogi `mypy` obavezan (CI + pre commita); šira ruff pravila. Pokrivenost 95 %.
- **Samoosvežavanje indeksa (ADR-032):** zastareo ili nedovršen indeks web aplikacija pravi sama iz
  `DOCUMENTS_DIR` u pozadini; UI traka "Osvežavam indeks… dokument 3 / 7"; bez originala za neki
  dokument ne briše ništa. Pregled proverava stare citate po početku teksta (`q`). Provereno sa pravim
  serverom i u browseru (Playwright).
- **OCR (ADR-024, grana `feat/ocr`):** skenovi, slike u PDF-u i upload slika (PNG/JPG/TIFF/BMP) se čitaju
  Tesseract-om lokalno (srp latinica+ćirilica, deu, eng); pregled označava citat i na skenu. Eval sa
  skeniranim demo PDF-ovima: 100 %. Fotografije proizvoda (Lavazza) daju delimičan tekst.
- **Razgovori (ADR-025):** kartica *Razgovori* levo — svako pitanje se čuva, stari razgovor se otvara ili
  briše; dugme za slanje je *Stop* dok se čeka (i `Esc`), zaustavljeno pitanje se ne čuva i vraća se u polje.
  Pretraga razgovora (naslov/pitanja/odgovori, bez obzira na kvačice), preimenovanje olovkom i
  **izvoz u PDF / Word** (dugme za preuzimanje → meni; ADR-027, `export.py`).
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

**Odluka (2026-11-06): nema demo videa ni online dema, i za sada nema Claude-a — samo razvoj aplikacije
(besplatni Groq/Gemini).** (Ranije, 2026-11-04: online demo se ne objavljuje.)
Sve za Hugging Face je spremno (`docs/DEPLOY.md`: Groq/Gemini, `EMBED_ORIGINS`, LFS u workflow-u);
deploy workflow se sam preskače dok GitHub varijabla `HF_SPACE` nije postavljena.

1. **Korisnik je tražio 5 stvari ("sve mi uradi"), radi se redom, svaka = jedan PR koji Claude spaja
   posle zelenog CI-ja:**
   - [x] Odgovor se ispisuje dok nastaje (#21).
   - [x] Pretraga i preimenovanje razgovora (#22), provereno u browseru (Playwright).
   - [x] Izvoz razgovora u PDF i Word (#23), provereno u browseru.
   - [x] **Ispravke posle korisnikovog testa (#24):**
     - "export.json" pri izvozu: uzrok je server na :8000 pokrenut pre #23 bez `--reload` (ruta ne
       postoji → 404 JSON). Server je restartovan (sa `--reload`). UI sada preuzima kroz `fetch` i
       pokazuje poruku (404 → "restartuj server") umesto da snimi grešku kao fajl.
     - Tabele u odgovoru: pravilo za model `TABLE_RULE` (`llm/prompts.py`, i u agentu) — kratke
       ćelije, opisi ispod tabele; UI: skraćen naziv izvora u čipu (24 znaka, u tabeli 16, ceo u
       tooltip-u), kolone sa brojevima desno i u jednom redu (`td.num`), tekst kolone min 10em.
       Izvoz (PDF/Word) takođe skraćuje izvore u tabelama (`export.short_citations`).
     - Provereno u browseru (pravi server, fake model): tabela; izvoz PDF preuzima fajl sa imenom
       razgovora; 404 → poruka, bez preuzimanja. Eval sa Groq-om na čistom indeksu (2026-11-05):
       RAG **100 %**; agent 90 % — 2 pitanja palo na limitu (Groq minutni + Gemini dnevni), 1 tačan
       odgovor sa dva izvora u jednoj zagradi `[a.docx, str. 2; b.pdf, str. 3]` koji parser ne čita.
     - **OCR (Lavazza cenovnik, str. 4/10):** Tesseract je na granici — 27 kombinacija (dpi 200/300/400,
       jezici, psm 3/11/12) bez primetnog poboljšanja. Cene sa malim decimalama ("1099⁰⁰") ispadaju
       kao "10990,", ćirilica (`srp`) pretvara latinicu u ćirilicu ("Модка"), naziv i cena iz iste
       kartice se razdvoje. Pravo rešenje = stavka "AI model za slike" ispod → predložiti da ide pre
       Excel/PowerPoint.
   - [x] Excel i PowerPoint (#25, ADR-028): list = strana (red pod zaglavljem = `Kolona: vrednost`),
     slajd = strana (naslov prezentacije na vrhu svakog slajda + beleške `Notes:`); pregled kao HTML
     sa označenim redom/pasusom; citati za bilo koju ekstenziju i više izvora u jednoj zagradi
     (`docassist/citations.py` + isti parser u `app.js`); nova demo dokumenta `rezervni_delovi.xlsx`
     i `pv400_sales_training.pptx`, eval 34 pitanja: RAG 100 %.
   - [x] AI model za slike (#26, ADR-029): dugme ✨ kod PDF-a/slike → potvrda → Gemini čita strane sa
     slikama, tekst u `<fajl>.vision.json` pored originala, dokument se ponovo indeksira. Provereno uživo
     na Lavazza str. 4 (Flash-Lite: svih 6 cena tačno, "1099,00") i u browseru sa lažnim modelom.
     **Korisnik treba sam da klikne ✨ na Lavazza cenovniku** (12 strana = 12 zahteva; glavni Gemini
     model ~20/dan, Flash-Lite preuzima kad se potroši).
   - Sve 5 (+ ispravke) urađeno. Sledeći predlozi: Claude eval kad bude kredita; označavanje citata na
     stranama koje je pročitao model (traži položaj teksta).
2. Promena `CHUNKING`/embedder-a više ne traži ručni `--rebuild`: aplikacija sama pravi indeks iz
   originala i pokazuje napredak (ADR-032). CLI i eval i dalje traže `--rebuild`.
3. Eval uvek na čistom indeksu (samo demo dokumenti): `INDEX_DIR`/`DOCUMENTS_DIR`/`LOG_DB` u privremeni folder.
4. Treniranje modela: korisnik je odložio (opcija A = dotreniranje embeddings modela, kad se vrati na to).
5. Claude eval (6.3) kad bude moguće platiti kredit.

## Napomene za sledeću sesiju

- Strane koje je pročitao AI model (ADR-029) dolaze iz `<original>.vision.json` (proverava sha256
  fajla); loader ih koristi umesto OCR-a, pa `--rebuild` ne troši Gemini limit. Brisanje dokumenta
  briše i taj fajl.
- XLSX: strana = list; PPTX: strana = slajd, naslov prezentacije na vrhu svakog slajda (ADR-028).
  Citati: `docassist/citations.py` i `CITE_PART` u `app.js` — menjati zajedno.
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
  Isti podskup parsira `export.parse_markdown` za PDF/Word — menjati oba zajedno.
- n8n 2.x: posle `update:workflow --active=true` treba restart; webhook se registruje par sekundi posle `/healthz`.
- Sesije su u SQLite (`sessions`), kolačić 1 godina → razgovori preživljavaju restart; vlasnik razgovora
  je `session:<id>` ili `api`/`open`.
- OCR strane idu paralelno (`ocr.run_jobs`, CPU-1 Tesseract procesa, `OMP_THREAD_LIMIT=1`): sken od 96 strana
  ~2 min (ranije ~6,5). UI prati `GET /documents/progress` i pokazuje "Čitam skenirane strane: 23 / 96".
- Tesseract se zove direktno (`ingest/ocr.py`, TSV); PyMuPDF-ov OCR je preskakao tabele. Windows: traži se
  u `C:/Program Files/Tesseract-OCR`. OCR strane u pregledu se radi ponovo (~1,5 s).
- Indeks pamti embedder i verziju podele (`CHUNKING` u `ingest/chunker.py`); promena → `--rebuild`.
- Susedni delovi: `VectorStore.with_neighbors` (`NEIGHBORS`, `LIST_FOLLOW` u `retrieval/store.py`); prompt dobija
  `rag.context_passages` (jedan odlomak po strani); `Answer.retrieved` sadrži i susede (`neighbor=True`).
- Prepisivanje pitanja: `rag.search_query` (+ `REWRITE_PROMPT`); `FakeProvider` na taj prompt vraća pitanje
  nepromenjeno. Agent ga ne koristi.
- Osvežavanje: `reindex.py` (nit, `Rebuild` status, oznaka `rebuilding` u `INDEX_DIR`); `api._open_library` pod
  `rebuild_lock`. Test server za proveru: `INDEX_DIR`/`DOCUMENTS_DIR` u scratch + `--port 8765`.
- Nova ruta → odgovarajući modul u `api/routes/`; zavisnosti iz `api/deps.py`, pristup `ACCESS`/`UPLOADS` iz
  `api/security.py`. `ConversationStore.list` se sada zove `summaries`.
- `n8n execute` pored pokrenutog n8n-a traži drugi port za task broker (`N8N_RUNNERS_BROKER_PORT=5690`) i
  ručni okidač u toku (*Run now*). Log ima nove kolone `escalation_team`, `search_query` (migracija sama).
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
| 2026-11-04 | Odgovor se ispisuje dok nastaje (`/ask/stream`, NDJSON); Stop prekida i generisanje; ADR-026 | #21 |
| 2026-11-04 | Pretraga (bez kvačica) i preimenovanje razgovora; provereno u browseru (ispravljeno: korpa je prekrivala olovku) | #22 |
| 2026-11-04 | Izvoz razgovora u PDF i Word (pitanja, odgovori sa tabelama, izvori); ADR-027 | #23 |
| 2026-11-04 | Ispravke: izvoz kroz `fetch` sa porukom o grešci, kraće tabele (pravilo za model + prikaz), analiza OCR-a Lavazza cenovnika | #24 |
| 2026-11-05 | Provera izvoza u browseru, eval sa novim pravilom za tabele (RAG 100 %, agent 90 % — limiti i citat sa `;`), restart servera | #24 |
| 2026-11-05 | Excel i PowerPoint dokumenti, citati sa više izvora u zagradi; eval 34 pitanja, RAG 100 %; ADR-028 | #25 |
| 2026-11-05 | AI model (Gemini) čita slike u dokumentu na zahtev; tekst se čuva pored originala; ADR-029 | #26 |
| 2026-11-06 | Ceo spisak do modela: susedni delovi, odlomak po strani, uvodna linija kao naslov, OCR bez lažne ćirilice i presečenih reči, pravilo za spiskove; eval 36 pitanja, RAG 100 %; ADR-030 | #27 |
| 2026-11-06 | Pitanja sa nastavkom: model prepiše pitanje pre pretrage (izmereno: 5/5 naspram 3/5 jeftine verzije); ADR-031 | #28 |
| 2026-11-06 | Aplikacija sama osvežava zastareo indeks (napredak u UI-ju), stari citati se proveravaju po tekstu; ADR-032 | #29 |
| 2026-11-06 | Kvalitet koda: API po ruterima, strogi mypy u CI-ju, šira ruff pravila; ADR-033 | #30 |
| 2026-11-06 | Pretraga samo u izabranim dokumentima (kvačice, `doc_ids`, i za Agenta); ADR-034 | #31 |
| 2026-11-06 | Nedeljni izveštaj o prazninama mejlom po timu (n8n), AI raspodela po timovima; ADR-035 | #32 |
| 2026-11-06 | Automatski uvoz dokumenata iz foldera `data/inbox` (skener, n8n); ADR-036 | #33 |
| 2026-11-06 | Panel „Automatski uvoz iz foldera“ (izbor foldera, istorija, neuspeli; premeštanje umesto brisanja); ADR-037 | #34 |
| 2026-11-06 | Panel prikazuje punu putanju foldera (ne relativnu `data/inbox`) | #35 |
| 2026-11-06 | Windows prozor za izbor foldera dolazi ispred browsera (potvrdio korisnik), jedan prozor odjednom; ispravka „false“ u panelu; ADR-038 | #37 |
| 2026-11-06 | `data/inbox.json` (lokalni izbor foldera) izbačen iz repoa i dodat u `.gitignore` | #38 |
