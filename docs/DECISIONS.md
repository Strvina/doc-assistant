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
