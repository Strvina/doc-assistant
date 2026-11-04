# DEPLOY — javni demo (Hugging Face Spaces, Docker)

Besplatno (CPU Basic: 2 vCPU, 16 GB RAM). Space build-uje isti `Dockerfile` kao lokalno (sa
Tesseract OCR-om i ugrađenim modelom za pretragu). Odgovore daju besplatni nivoi **Groq**
(glavni) i **Gemini** (rezerva) — ADR-021/022; Claude se uključuje samo dodavanjem ključa.

## 1. Provera lokalno

```bash
uv run uvicorn docassist.api:app --reload      # → http://localhost:8000
docker build -t doc-assistant . && docker run -p 8000:8000 --env-file .env doc-assistant
```

## 2. Jednokratno podešavanje

1. Nalog na <https://huggingface.co> → **New Space** → ime `doc-assistant`, SDK **Docker**
   (*Blank*), hardware **CPU basic (free)**, vidljivost *Public*.
2. Space → **Settings → Variables and secrets**:

   | Tip | Ime | Vrednost | Zašto |
   |---|---|---|---|
   | Secret | `GROQ_API_KEY` | `gsk_…` | glavni model (besplatno) |
   | Secret | `GEMINI_API_KEY` | `AIza…` | rezerva kad Groq potroši limit (opciono) |
   | Secret | `API_TOKEN` | nasumičan string | štiti API od skripti; browser radi preko sesije |
   | Variable | `LLM_PROVIDER` | `groq` | |
   | Variable | `PORT` | `7860` | port koji Space očekuje |
   | Variable | `ALLOW_UPLOADS` | `false` | fiksni izmišljeni demo dokumenti: bez upload-a i brisanja |
   | Variable | `MAX_QUESTIONS_PER_SESSION` | `15` | čuva besplatne limite modela |
   | Variable | `MAX_QUESTIONS_PER_IP_HOUR` | `40` | |
   | Variable | `EMBED_ORIGINS` | `https://huggingface.co` | Space prikazuje aplikaciju u iframe-u na huggingface.co |

   Bez `ESCALATION_WEBHOOK_URL` (n8n nije online) prosleđena pitanja se upisuju u lokalni fajl
   Space-a — dugme radi i daje referencu.
3. <https://huggingface.co/settings/tokens> → novi token, tip **Write**.
4. GitHub repo → **Settings → Secrets and variables → Actions**:
   - *Secret* `HF_TOKEN` = taj token
   - *Variable* `HF_SPACE` = `<tvoj-hf-username>/doc-assistant`
5. GitHub → **Actions → Deploy demo → Run workflow** (posle toga svaki merge u `main` sam
   objavljuje novu verziju).

Workflow (`.github/workflows/deploy-space.yml`) objavljuje `main` u Space: README zamenjuje sa
`deploy/space_readme.md` (Space podešavanja: `sdk: docker`, `app_port: 7860`), a PDF/DOCX/slike
šalje preko Git LFS-a (Hub drugačije odbija binarne fajlove). Space sam build-uje image
(~10 min prvi put). Pri prvom pitanju aplikacija indeksira demo dokumente.

## 3. Posle deploy-a

- Proveri: otvori Space, postavi pitanje iz primera, klikni izvor (pregled sa označenim pasusom),
  prebaci na *Agent*.
- Link (`https://<user>-doc-assistant.hf.space`) upiši u `README.md` (zadatak 4.4).
- Disk Space-a nije trajan: posle restarta indeks, log i razgovori kreću ispočetka (za demo u redu).
- Ključevi besplatnih nivoa troše samo limite (Groq: 1.000 zahteva dnevno po modelu), ne novac.
  **U demo nikad ne stavljaj prave firmine dokumente** — besplatni nivoi smeju da koriste sadržaj.
