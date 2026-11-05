# DECISIONS — dnevnik odluka (ADR)

Format: kratko šta je odlučeno, zašto, i šta je alternativa. Nova odluka = novi unos na dnu.
Ako se odluka promeni, ne briši staru: označi je kao *Zamenjena sa ADR-XXX*.

---

### ADR-001 · Python 3.12 + uv
**Odluka:** Python 3.12, uv za zavisnosti i virtuelno okruženje, `uv.lock` u gitu.
**Zašto:** brz, jedan alat za venv/zavisnosti/pokretanje; lock fajl daje iste verzije lokalno i u CI-ju.
**Alternativa:** pip + requirements.txt, Poetry.

### ADR-002 · RAG pišemo sami, bez LangChain/LlamaIndex
**Odluka:** loader-i, chunking, pretraga i prompt su naš kod.
**Zašto:** cilj je da na razgovoru za posao mogu da objasnim svaki korak; manje magije, lakše debagovanje.
**Alternativa:** LlamaIndex — brže na početku, ali skriva detalje.

### ADR-003 · ChromaDB kao vektorska baza
**Odluka:** Chroma u lokalnom (persist) režimu, folder `data/index/`.
**Zašto:** bez servera i Docker-a za start, dovoljno za demo.
**Alternativa:** Postgres + pgvector (produkcijski izbor; moguća migracija kasnije).

### ADR-004 · OpenAI embeddings (iza interfejsa) — *zamenjena sa ADR-019*
**Odluka:** embeddings preko OpenAI API-ja; model se podešava u `.env`.
**Zašto:** dobar kvalitet za više jezika (SR/DE/EN), jeftino, bez teških lokalnih zavisnosti (torch).
**Alternativa:** lokalni višejezični model (npr. multilingual-e5) — razmotriti posle evaluacije.
**Napomena:** Anthropic nema embedding API, pa je OpenAI ključ potreban i kad je LLM Claude.

### ADR-005 · Claude i OpenAI iza zajedničkog `LLMProvider` interfejsa — *dopunjena sa ADR-018*
**Odluka:** jedan interfejs, provajder se bira preko `LLM_PROVIDER`.
**Zašto:** oglas izričito traži oba; omogućava uporednu evaluaciju (faza 6).

### ADR-006 · Streamlit prvo, FastAPI u fazi 5 — *zamenjena sa ADR-020*
**Odluka:** UI u Streamlit-u poziva core paket direktno; API dolazi kad zatreba za n8n.
**Zašto:** najbrži put do demo-a koji poslodavac može da proba.

### ADR-007 · CI: ruff + pytest, testovi bez mreže
**Odluka:** GitHub Actions na svaki PR: `ruff check`, `ruff format --check`, `pytest`.
Sve spoljne usluge imaju Fake implementacije.
**Zašto:** CI ne treba tajne ključeve, brz je i deterministički.

### ADR-008 · Agent bez framework-a (faza 5)
**Odluka:** tool use preko nativnih API-ja (Claude tool use / OpenAI function calling), sopstvena petlja.
**Zašto:** isti razlog kao ADR-002; pokazuje razumevanje kako agenti rade.

### ADR-009 · PyMuPDF ostaje na 1.x liniji
**Odluka:** `pymupdf>=1.28.2,<2`.
**Zašto:** `pymupdf` 2.x (izašao 2. 11. 2026.) uvlači `onnxruntime`, `numpy`, `networkx` i
`pymupdf4llm` (~65 MB wheel + zavisnosti) za analizu rasporeda koja nam ne treba. 1.x nema
zavisnosti, što je bitno za brz CI i mali demo deploy.
**Alternativa:** preći na 2.x ako zatreba prepoznavanje tabela/rasporeda (npr. za skenirane ili složene PDF-ove).

### ADR-010 · "Strana" u DOCX = tekst između eksplicitnih preloma strane
**Odluka:** DOCX loader deli dokument na strane samo na eksplicitnim prelomima
(`Ctrl+Enter` / `w:br type="page"` i "Page break before" na pasusu). Tabele se čitaju redom kojim
se pojavljuju, ćelije spojene sa ` | `. TXT/MD je jedna strana, osim ako sadrži form feed (`\f`).
Prazne strane se preskaču, ali se numeracija ne pomera.
**Zašto:** DOCX ne čuva raspored strana — on zavisi od fonta, štampača i verzije Word-a.
Word-ovi `lastRenderedPageBreak` markeri postoje samo ako je fajl sačuvan u Word-u i zastare posle
izmena, pa bi citat "str. X" mogao da bude pogrešan bez upozorenja.
**Alternativa:** citirati DOCX po naslovu poglavlja umesto po strani — razmotriti u fazi 3
ako se pokaže da su strane u DOCX-u nedovoljno precizne.

### ADR-011 · Demo dokumenti se generišu iz tekstualnih izvora
**Odluka:** sadržaj demo dokumenata je u `scripts/sample_docs_src/*.md` (mali podskup Markdown-a),
a `scripts/build_sample_docs.py` od njih pravi PDF/DOCX/TXT u `data/sample_docs/`.
I izvori i generisani fajlovi su u gitu.
**Zašto:** sadržaj se lako menja i pregleda u diff-u; generisani fajlovi su odmah spremni za demo i
testove. Firma (Panelvolt d.o.o.), ljudi, brojevi telefona i domeni (`*.example`) su izmišljeni.

### ADR-012 · Chunk-ovi ne prelaze granicu strane; PDF se čita po blokovima
**Odluka:** chunker (800 znakova, preklapanje 150) seče svaku stranu posebno, redom po pasusima,
redovima, rečenicama i rečima. PDF loader spaja redove jednog PyMuPDF bloka u jedan red
(pasus / stavka liste / red tabele), a blokove razdvaja praznim redom.
**Zašto:** svaki chunk mora da ima tačno jednu stranu za citat. PDF čuva vizuelne redove, pa bi
rečenice bile presečene prelomom reda; po blokovima chunker dobija prirodne granice, a red tabele
(`M8 20 Nm 13 mm`) ostaje ceo.
**Cena:** rečenica koja prelazi na sledeću stranu deli se na dva chunk-a.

### ADR-013 · Indeks pamti embedder; `EMBEDDING_PROVIDER=fake` za rad bez ključa
**Odluka:** Chroma kolekcija u metapodacima čuva ime embedder-a (npr. `openai:text-embedding-3-small`).
Ako se indeks otvori drugim embedder-om → jasna greška i uputstvo `index --rebuild`.
`FakeEmbedder` (heširane reči + trigrami) se može uključiti i van testova sa `EMBEDDING_PROVIDER=fake`.
Dokument se identifikuje po imenu fajla: isti naziv + nov sadržaj zamenjuje staru verziju,
isti sadržaj se ne embeduje ponovo (ušteda na API pozivima).
**Zašto:** vektori različitih modela nisu uporedivi — mešanje bi tiho vraćalo besmislene rezultate.
Fake režim omogućava da se ceo tok (index → search) proba bez API ključa.
**Ograničenje:** fake embeddings ne razumeju značenje ni prevod (SR pitanje neće naći DE odgovor).

### ADR-014 · LLM sloj: kontekst u user poruci, citati se proveravaju, refusal fallback
**Odluka:**
- Sistemski prompt je fiksan (može da se kešira); dokumenti idu u user poruku kao
  `<documents><document source=".." page="..">`, uz pravilo da su dokumenti podaci, ne instrukcije.
- Citati `[fajl, str. N]` se parsiraju iz odgovora i proveravaju protiv pronađenih chunk-ova;
  citat na stranu koja nije bila u kontekstu ide u `invalid_citations` (izmišljen izvor).
  `Answer.found` = postoji bar jedan validan citat.
- Prazan indeks / nema rezultata → odgovor "Nema toga..." bez poziva LLM-a (nema troška).
- Claude: zvanični `anthropic` SDK, `max_tokens=16000`, adaptivno razmišljanje (podrazumevano
  za model), `ANTHROPIC_EFFORT` opciono; server-side refusal fallback (`fallbacks="default"`,
  beta `server-side-fallback-2026-07-01`) za sve osim Haiku modela.
- OpenAI: Responses API, `store=False`; `OPENAI_MODEL` mora biti postavljen (bez pogađanja imena modela).
- `LLM_PROVIDER=fake` — offline odgovor (prva rečenica najboljeg chunk-a + citat) za probu UI-ja bez ključa.
**Zašto:** proverljivi citati su suština projekta; fiksni sistemski prompt smanjuje cenu;
fake provajder omogućava razvoj i demo toka bez API kredita.

### ADR-015 · Streamlit UI i zaštite za javni demo
**Odluka:** `app/streamlit_app.py` koristi isti core (`build_store`, `build_llm`, `answer`).
Za javni demo: `ALLOW_UPLOADS=false` (fiksni demo dokumenti, auto-indeksiranje pri startu,
bez upload-a i brisanja) i `MAX_QUESTIONS_PER_SESSION` + limit od 1000 znakova po pitanju.
Upload se čuva u `data/uploads/` (nije u gitu) i briše zajedno sa dokumentom iz indeksa.
Prazne vrednosti u `.env` (`TOP_K=`) znače "koristi podrazumevano".
**Zašto:** javni link troši moj API kredit i deli isti indeks među svim posetiocima —
bez ovih ograničenja svako bi mogao da ubaci proizvoljan sadržaj ili potroši kredit.
**Alternativa:** login/korisnici (van opsega, vidi PLAN).

### ADR-016 · Agent: nativna petlja po provajderu, zajednički alati
**Odluka:** svaki provajder ima svoju `run_agent` petlju nad nativnim API-jem
(Claude: `tool_use`/`tool_result`, istorija samo raste — `response.content` se vraća nepromenjen;
OpenAI: Responses API `function_call`/`function_call_output`, `store=False` +
`include=["reasoning.encrypted_content"]`). Alati (`agent/tools.py`) su zajednički, sa striktnim
JSON šemama (`strict: true`, sva polja obavezna). `tool_choice` ostaje `auto` (Claude Sonnet/Opus 5.5
ne dozvoljavaju forsiran poziv) — kada koristiti alat piše u sistemskom promptu.
Greške alata se vraćaju modelu kao `is_error` rezultat (model se oporavlja), nepoznato ime alata
se toleriše po velikim/malim slovima. Max 8 koraka po pitanju.
Eskalacija: `ESCALATION_WEBHOOK_URL` (n8n) ili lokalni `data/outbox/escalations.jsonl`;
zahtevi za odmor su nacrti u `data/outbox/leave_requests.jsonl`.
Citati agenta se proveravaju protiv svih pasusa koje su alati vratili.
REST API (FastAPI) sa opcionim `X-API-Key` (`API_TOKEN`); `/health` je uvek otvoren.
**Zašto:** pokazuje razumevanje oba API-ja (ADR-008); zajednički alati = isto ponašanje za oba
modela i fer poređenje u fazi 6.

### ADR-017 · Evaluacija i logovanje
**Odluka:** `eval/questions.yaml` (30 pitanja: SR/DE/EN, pitanja preko jezika, 3 bez odgovora);
metrike: pogodak strane u pretrazi, citirana tačna strana/dokument, pokrivenost ključnih činjenica,
"nema toga" na pitanjima bez odgovora, pogrešno "nema toga", izmišljeni citati, latencija, tokeni, trošak.
`--retrieval-only` meri samo pretragu (bez LLM troška). Svaki upit iz UI-ja i API-ja se loguje u
SQLite (`LOG_DB`) sa ocenom 👍/👎; statistika u UI sidebar-u i na `GET /stats`.
**Zašto:** brojke umesto utiska — za izbor modela/podešavanja i za razgovor za posao.
Test proverava da je svaka očekivana činjenica stvarno na navedenoj strani (nema grešaka u skupu).

### ADR-018 · Samo Claude za odgovore; OpenAI ostaje opciono
**Odluka (2026-11-04):** projekat koristi isključivo Claude za odgovore i agenta. `OpenAIProvider`
ostaje u kodu kao neobavezna opcija (vidljiv samo uz `OPENAI_API_KEY` + `OPENAI_MODEL`).
Embeddings prelaze sa OpenAI na lokalni višejezični model (zaseban PR) — *menja ADR-004*.
**Zašto:** jedan provajder = jedan račun i manji trošak; arhitektura i dalje pokazuje da podržava oba.

### ADR-019 · Lokalni višejezični embeddings (fastembed) + chunk 500/100
**Odluka:** podrazumevani embedder je `LocalEmbedder` (fastembed/ONNX) sa modelom
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (~0,22 GB, preuzima se jednom u
`data/models/`). Chunk-ovi su 500 znakova sa preklapanjem 100 (bilo 800/150). OpenAI embeddings
ostaju kao opcija (`EMBEDDING_PROVIDER=openai`). Zamenjuje ADR-004; menja veličinu iz ADR-012.
**Merenje** (`eval/questions.yaml`, 27 pitanja sa odgovorom, tačna strana među top-5 / na 1. mestu):

| Embedder | Chunk | top-5 | top-1 | Upit |
|---|---|---|---|---|
| fake (heš reči) | 800 | 56 % | — | — |
| potion-multilingual-128M (0,5 GB) | 800 | 78 % | 52 % | 4 ms |
| MiniLM-L12 multilingual (0,22 GB) | 800 | 89 % | 74 % | 18 ms |
| mpnet-base multilingual (1,0 GB) | 800 | 93 % | 74 % | 48 ms |
| **MiniLM-L12 multilingual** | **500** | **100 %** | **85 %** | 16 ms |
| mpnet-base multilingual | 500 | 100 % | 78 % | 48 ms |

**Zašto:** samo Claude se plaća (ADR-018); dokumenti ne napuštaju server radi indeksiranja
(argument za zaštitu podataka); radi preko jezika (SR pitanje → DE pasus). MiniLM čita ~128 tokena,
zato manji chunk-ovi daju bolji rezultat. Mali model staje u memoriju besplatnog hostinga.
**Cena:** ~220 MB preuzimanja pri prvom pokretanju; indeksiranje troši CPU umesto API-ja.

### ADR-020 · Sopstveni web UI (FastAPI + HTML/CSS/JS) umesto Streamlit-a
**Odluka:** Streamlit aplikacija je uklonjena. FastAPI app servira i web interfejs
(`src/docassist/web/`: čist HTML/CSS/JS, bez build koraka i bez npm-a) i REST API.
UI ima tri kolone (biblioteka · razgovor · pregled dokumenta), citate kao klikabilne oznake,
pregled originala sa označenim pasusom (PDF highlight anotacije; DOCX/TXT kao HTML strane),
panel za praćenje sa "prazninama u dokumentaciji", tamnu temu i mobilni prikaz.
Originali se čuvaju uz indeks (`Library`, `DOCUMENTS_DIR`) da bi mogli da se prikažu i preuzmu.
Zaštite su na serveru: sesija (cookie) za browser, `X-API-Key` za integracije, limit pitanja po
sesiji i po IP adresi na sat, zaključan upload u demo režimu, max veličina fajla.
Hosting javnog demoa: Hugging Face Spaces (Docker) umesto Streamlit Community Cloud-a.
*Zamenjuje ADR-006 i UI deo ADR-015.*
**Zašto:** Streamlit ne dozvoljava specifičan dizajn ni pregled dokumenta pored razgovora;
povratna informacija je bila da izgleda previše osnovno. Jedan proces (UI + API) je jednostavniji
za deploy, a zaštite u Streamlit-u su bile samo u UI-ju (zaobilazile su se preko API-ja).
**Cena:** frontend se ne testira automatski u CI-ju (testiran je API koji koristi; UI je proveren
screenshot-ovima u Edge-u, desktop/mobilni/tamna tema).

### ADR-021 · Google Gemini (besplatan nivo) kao provajder za demo bez plaćenih kredita
**Odluka:** dodat `LLM_PROVIDER=gemini` (`GeminiProvider`, `src/docassist/llm/gemini.py`).
Poziva Gemini preko njegovog OpenAI-kompatibilnog Chat Completions endpointa, kroz postojeći
`openai` SDK (bez nove zavisnosti); radi i običan odgovor i agent sa alatima. Model je
`GEMINI_MODEL` (podrazumevano `gemini-3.5-flash`), a kad je preopterećen (HTTP 429/5xx)
automatski se prelazi na `GEMINI_FALLBACK_MODEL` (podrazumevano `gemini-flash-lite-latest`).
Alias `gemini-flash-latest` se ne koristi jer pokazuje na najnoviji model, koji je na besplatnom
nivou često vraćao 503. Claude ostaje podržan i dokumentovan kao
produkcijska opcija; Gemini se u UI-ju prikazuje samo ako je postavljen `GEMINI_API_KEY`.
*Dopunjuje ADR-018 (do sada: samo Claude).*
**Zašto:** uplata Claude API kredita trenutno nije moguća (banka odbija karticu), a javni demo
mora da daje prave odgovore. Gemini API ima besplatan nivo bez kartice i dobar je na srpskom i
nemačkom.
**Cena:** limiti besplatnog nivoa (broj zahteva u minuti/danu; vide se u AI Studio-u), pa demo mora
da ostane na niskim limitima pitanja. Google na besplatnom nivou sme da koristi sadržaj za
poboljšanje svojih modela — prihvatljivo za izmišljene demo dokumente, **ne** za prave firmine
dokumente (za njih: Claude ili plaćeni Gemini nivo).

### ADR-022 · Groq (besplatan nivo) kao glavni model za demo; lanac besplatnih modela
**Odluka:** dodat `LLM_PROVIDER=groq` (open-weight `openai/gpt-oss-120b`, rezerva
`openai/gpt-oss-20b`). Gemini i Groq dele jedan provajder (`ChatCompletionsProvider`,
`src/docassist/llm/chat_completions.py`; zamenjuje `GeminiProvider` iz ADR-021) koji prolazi kroz
**lanac**: izabrani servis (glavni + rezervni model), pa drugi besplatni servis ako ima ključ.
Kad model vrati 429 (limit), 5xx (preopterećen) ili 413 (zahtev prevelik za minutni limit),
odmah se prelazi na sledeći; model sa potrošenim limitom se preskače dok se limit ne obnovi
(`retry-after` / Google `retryDelay`). Poruke se šalju u obliku koji servis prihvata (Gemini
dobija nazad svoje "thought signatures", ostali samo standardna polja).
**Zašto:** izmereno 4. 11.: Gemini free tier — 20 zahteva dnevno za `gemini-3.5-flash`,
Flash-Lite 15–20 s po pozivu (agent 80–110 s). Groq free tier — 0,2–1,5 s po pozivu,
1.000 zahteva dnevno i 8.000 tokena u minuti po modelu; eval 93 % tačno, 0 izmišljenih citata
(`docs/EVAL_RESULTS.md`). Lanac pokriva i dnevni i minutni limit.
**Cena:** odgovor u jednom razgovoru može doći od različitih modela (UI prikazuje koji);
gpt-oss je strožiji od Gemini-ja kad je kontekst iscepkan (vidi EVAL_RESULTS); besplatni nivoi
nemaju garanciju dostupnosti. Za prave firmine dokumente i dalje: Claude ili plaćeni nivo.

### ADR-023 · Hibridna pretraga (+1 pogodak po ključnim rečima) i podela po odeljcima
**Odluka:**
1. **Pretraga:** uz `k` pasusa po značenju (embeddings) dodaje se najviše **jedan** pasus koji
   BM25 pretraga po ključnim rečima rangira prvi, ako ga embeddings nisu vratili
   (`retrieval/keywords.py`, `VectorStore.search`; `HYBRID_SEARCH=false` isključuje).
   Tokeni: mala slova, bez dijakritika, skraćeni na 6 znakova (grubi "stemmer" za srpski/nemački).
2. **Podela teksta:** strana se deli po odeljcima — numerisani naslovi ("6. Anzugsmomente …") i
   pitanja u FAQ-u; ceo odeljak ide u jedan pasus kad stane, a delovi dugog odeljka počinju
   njegovim naslovom (`split_sections`). Indeks pamti verziju podele (`CHUNKING`); stari indeks
   traži `index data/documents --rebuild`.
**Zašto:** dva promašaja iz evaluacije (ADR-022): "Kada radi kantina?" — mali embedding model nije
našao pasus sa tačnom rečju; M8 — red tabele odvojen od naslova "za sabirnice", pa je model
zaključio da vrednost ne postoji. Izmereno (`docs/EVAL_RESULTS.md`): sve činjenice u pronađenim
pasusima 96 % → **100 %**; odgovori (Groq gpt-oss-120b) 93 % → **100 %**.
Spajanje rang-lista (Reciprocal Rank Fusion), i sa manjom težinom ključnih reči, bilo je **gore**
(93 %): ključne reči ne rade preko jezika, pa su gurale tačne nemačke pasuse za srpska pitanja
ispod granice. Zato se pogodak po ključnim rečima *dodaje*, a ne meša u rang.
**Cena:** jedan pasus više u promptu (~150 tokena); BM25 indeks se pravi u memoriji (za
hiljade pasusa zanemarljivo; za veliki broj dokumenata: pravi full-text indeks, npr.
SQLite FTS5 ili Postgres). Promena podele traži ponovno indeksiranje.

### ADR-024 · OCR za skenove i tekst na slikama (Tesseract, lokalno)
**Odluka:** strana PDF-a na kojoj slike pokrivaju ≥ 10 % površine se renderuje (200 DPI, sivo)
i čita **Tesseract**-om (srpski latinica + ćirilica, nemački, engleski), lokalno. Tesseract vraća
svaku reč sa položajem, pa se OCR tekst grupiše u blokove kao PDF tekst: podela po odeljcima i
označavanje citata u pregledu rade i na skeniranoj strani. OCR redovi koji se poklapaju sa
postojećim tekstom strane se izbacuju (bez duplikata); šum sa fotografija se filtrira po redu
(red ostaje ako je većina tokena čitljiva i bar jedan je reč od 3+ slova ili broj od 2+ cifre).
Podržan je i upload slika (PNG, JPG, TIFF, BMP) — čitaju se kao PDF od jedne strane.
`OCR=false` isključuje; bez instaliranog Tesseract-a strane se čitaju bez OCR-a. Indeks pamti
verziju (`CHUNKING = "sections-v2-ocr1"`). Docker slika i CI instaliraju Tesseract.
**Zašto:** većina stvarnih PDF-ova su skenovi ili slike nalepljene u PDF; bez OCR-a su prazni.
Lokalni OCR je besplatan i dokumenti ne napuštaju server (isti argument kao ADR-019).
Tesseract se poziva direktno (komandna linija, TSV izlaz): ugrađeni OCR u PyMuPDF-u
(`get_textpage_ocr`, `pdfocr_tobytes`) je na test skenu **preskočio celu tabelu** koju Tesseract
sam pročita ispravno.
Izmereno na demo PDF-ovima pretvorenim u slike (bez ijednog slova teksta): 98–100 % reči
originala; eval sa tim skenovima: pretraga 100 %, odgovori (Groq) **100 %**.
**Cena:** ~1,5–2 s po strani sa slikama pri indeksiranju (i pri otvaranju takve strane u
pregledu); Docker slika veća za ~40 MB. Fotografije proizvoda, logotipi i složene tabele u
fotografijama daju nepouzdan tekst (Lavazza cenovnik: nazivi i cene delimično) — za to je
sledeći korak opcioni AI model koji čita slike (npr. Gemini), uz svestan izbor jer slike tada
napuštaju server.

### ADR-025 · Sačuvani razgovori po sesiji i zaustavljanje pitanja
**Odluka:** svako pitanje se čuva u razgovoru (`conversations.py`, isti SQLite fajl kao log
pitanja: tabele `sessions`, `conversations`, `turns`). Odgovor se čuva ceo, kako ga je API
vratio (citati, koraci agenta), pa se stari razgovor prikazuje isto kao kad je nastao; naslov je
prvo pitanje. API: `POST /ask` sa `conversation_id` nastavlja razgovor (bez njega počinje novi),
`GET /conversations`, `GET/DELETE /conversations/{id}`. Razgovor pripada vlasniku — browser
sesiji, ili "api" za integracije — i samo ga vlasnik vidi, otvara i briše.
Sesija se čuva u bazi i kolačić traje godinu dana, pa razgovori preživljavaju restart servera.
**Zaustavljanje:** UI šalje `request_id`; dugme za slanje postaje "Stop" (i `Esc`). Klik javlja
serveru `POST /ask/cancel` i prekida čekanje; ako odgovor ipak stigne, server ga ne čuva i ne
loguje (vraća 409). Pitanje se vraća u polje za unos da se ispravi.
**Zašto:** korisnik hoće da se vrati na stare razgovore i da ih briše; i da prekine pogrešno
postavljeno pitanje. Na serveru (ne u `localStorage`) — radi i za API, brisanje je stvarno
brisanje sa servera, a na javnom demou posetioci ne vide tuđe razgovore.
**Cena:** model ipak završi zaustavljeni odgovor u pozadini (troši limit, ne i vreme korisnika);
razgovori su vezani za browser — drugi browser ili obrisani kolačići = nova, prazna lista
(nema korisničkih naloga). Brisanje razgovora ne briše red iz loga pitanja (monitoring).

### ADR-026 · Odgovor se ispisuje dok nastaje (streaming, NDJSON)
**Odluka:** `POST /ask/stream` šalje odgovor kao niz JSON redova (`application/x-ndjson`):
`{"type":"delta","text":…}` dok model piše, pa `{"type":"done","response":<isto kao /ask>}`
(ili `{"type":"error",…}`). Provajder sa metodom `stream` (Groq/Gemini preko Chat Completions,
offline) šalje tekst u delovima; ostali (Claude, ChatGPT — još nisu provereni uživo) daju ceo
odgovor kao jedan deo (`stream_complete`). Agent šalje samo "done" (između koraka su alati).
UI čita tok kroz `fetch` + `ReadableStream`; dok se piše, citati su neutralne oznake, a gotova
kartica sa izvorima zamenjuje tekst na kraju. Stop prekida tok: server zatvara stream ka modelu
(prestaje generisanje) i ništa ne čuva. `/ask` ostaje za integracije.
**Zašto:** prvi tekst posle ~1,5 s umesto celog odgovora posle 3–10 s; NDJSON (a ne SSE) jer
pitanje ide POST-om sa telom, a `fetch` čita tok direktno.
**Cena:** i dalje se čeka pretraga i prvi token modela; besplatni Gemini nekad šalje odgovor u
jednom komadu.

### ADR-027 · Izvoz razgovora u PDF i Word
**Odluka:** `GET /conversations/{id}/export?format=pdf|docx` (samo vlasnik razgovora) vraća fajl:
naslov, pa za svako pitanje odgovor i "Izvori" (dokument, strana, citirani pasus skraćen na 300
znakova). Odgovor se čita kao isti Markdown podskup koji prikazuje UI (naslovi, liste, tabele,
**bold**, `kod`) — `export.parse_markdown` pa dva renderera: Word preko python-docx, PDF preko
PyMuPDF-ovog HTML rasporeda (`Story`) sa brojevima strana. U UI-ju dugme za preuzimanje u listi
razgovora otvara meni PDF / Word; ime fajla je naslov razgovora (ASCII rezerva za stare klijente).
**Zašto:** obe biblioteke već čitaju dokumente — nema nove zavisnosti ni spoljnog servisa
(wkhtmltopdf, LibreOffice); PyMuPDF-ovi ugrađeni fontovi pokrivaju srpsku latinicu i ćirilicu i
nemačka slova. Izvoz na serveru (a ne `window.print`) daje isti fajl i za API integracije.
**Cena:** Markdown parser postoji dvaput (app.js i Python) — promena u jednom traži promenu u
drugom. Vreme u fajlu je lokalno vreme servera (na HF Space-u UTC). Koraci agenta (alati) se ne
izvoze, samo konačni odgovor.

### ADR-028 · Excel i PowerPoint dokumenti
**Odluka:** `.xlsx` (openpyxl) i `.pptx` (python-pptx). Excel: jedna "strana" = jedan vidljiv list
(sakriveni se preskaču), vrednosti koje je Excel poslednje izračunao (ne formule). Kad prvi red
imenuje kolone, svaki sledeći red se indeksira kao `Kolona: vrednost | Kolona: vrednost`.
PowerPoint: jedna strana = jedan slajd; naslov prezentacije (naslov prvog slajda), naslov slajda,
pa tekst i tabele odozgo nadole, pa beleške govornika (`Notes: …`). Pregled prikazuje list kao tabelu, a slajd kao stranu, i označava citirani red
ili pasus. Citati se prepoznaju za bilo koju ekstenziju i više izvora u jednoj zagradi
(`[a.xlsx, str. 2; b.pdf, str. 3]`) — `docassist/citations.py` i isti parser u `app.js`.
**Zašto:** u firmi su cenovnici, spiskovi delova i obuke često u Excelu/PowerPointu. Dugačak list se
deli na mnogo delova; red koji sam kaže šta mu je koja kolona ostaje razumljiv i daleko od zaglavlja.
Beleške često sadrže ono što se na slajdu samo pominje. Slajd retko ponavlja o čemu je
prezentacija ("Warranty and service" — za koji proizvod?): bez naslova prezentacije pretraga nije
našla slajd ni za jedno od 2 eval pitanja, sa njim 2/2. gpt-oss je u evalu spojio dva izvora u jednu
zagradu, pa je tačan odgovor izgledao kao da nema izvora.
**Cena:** slike u slajdovima i listovima se ne čitaju (to je posao modela za slike). `.xls`, `.ppt`
(stari binarni formati) nisu podržani. Veliki list daje mnogo delova (više tokena po redu zbog naziva
kolona). Strana u citatu za Excel je redni broj lista ("str. 2" = drugi list).

### ADR-029 · AI model za slike u dokumentima (na zahtev, po dokumentu)
**Odluka:** dugme ✨ "Pročitaj slike AI modelom" kod PDF-a i slike → potvrda → `POST
/documents/{id}/pictures`. Strane na kojima slike pokrivaju ≥ 10 % (isto pravilo kao za OCR) se
renderuju (najviše 2000 px po dužoj strani) i šalju Gemini-ju, jedna po zahtevu (lanac
`GEMINI_MODEL` → `GEMINI_FALLBACK_MODEL`; Groq-ovi gpt-oss ne čitaju slike). Prompt: prepiši sav
tekst redom, tabele kao Markdown redove, proizvod sa cenom u jednom redu, brojevi tačno, "1099⁰⁰" =
"1099,00", bez prevođenja i opisa slika. Tekst se čuva pored originala u `<fajl>.vision.json` (sha256
fajla, strana → tekst) i loader ga koristi umesto OCR-a i teksta strane; dokument se ponovo indeksira.
Pročitane strane se čuvaju jedna po jedna: kad se potroši besplatni limit, sledeći pokušaj nastavlja.
U javnom demu (`ALLOW_UPLOADS=false`) i bez `GEMINI_API_KEY` dugme ne postoji.
**Zašto:** Tesseract na Lavazza cenovniku daje "10990," umesto "1099,00", razdvaja naziv i cenu i
pretvara latinicu u ćirilicu; Gemini Flash-Lite je na strani 4 pročitao svih 6 proizvoda sa tačnim
cenama (~3,5 s, ~1.300 ulaznih tokena po strani). Na zahtev, a ne automatski: strane tada odlaze
Google-u, a besplatni nivo ima mali dnevni limit (glavni model ~20 zahteva). Čuvanje teksta pored
originala znači da `index --rebuild` ne troši limit ponovo, a izmenjen fajl istog imena ne dobija
stari tekst.
**Cena:** za strane koje je pročitao model pregled otvara pravu stranu, ali citirani pasus se obično
ne označava (označavanje traži položaj teksta na strani, a model ga ne daje). Google-ovi uslovi za
besplatni nivo mogu dozvoliti korišćenje sadržaja — za prave dokumente plaćeni nivo ili Claude
(kad bude kredita, isti `read_image` interfejs). Sken od 96 strana = 96 zahteva: za skenove je
Tesseract i dalje pravi izbor.
