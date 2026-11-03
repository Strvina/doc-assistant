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

### ADR-004 · OpenAI embeddings (iza interfejsa)
**Odluka:** embeddings preko OpenAI API-ja; model se podešava u `.env`.
**Zašto:** dobar kvalitet za više jezika (SR/DE/EN), jeftino, bez teških lokalnih zavisnosti (torch).
**Alternativa:** lokalni višejezični model (npr. multilingual-e5) — razmotriti posle evaluacije.
**Napomena:** Anthropic nema embedding API, pa je OpenAI ključ potreban i kad je LLM Claude.

### ADR-005 · Claude i OpenAI iza zajedničkog `LLMProvider` interfejsa
**Odluka:** jedan interfejs, provajder se bira preko `LLM_PROVIDER`.
**Zašto:** oglas izričito traži oba; omogućava uporednu evaluaciju (faza 6).

### ADR-006 · Streamlit prvo, FastAPI u fazi 5
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
