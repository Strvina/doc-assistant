# DEPLOY — javni demo (Hugging Face Spaces, Docker)

Besplatno (CPU Basic: 2 vCPU, 16 GB RAM), pokreće isti Docker image kao lokalno.
Radi se kad Claude ključ radi lokalno.

## 1. Provera lokalno

```bash
cp .env.example .env                     # upiši ANTHROPIC_API_KEY (jedini potreban ključ)
uv run uvicorn docassist.api:app --reload
# → http://localhost:8000  — učitaj demo dokumente, postavi pitanje, otvori izvor
```

Sa Docker-om: `docker compose up --build` → <http://localhost:8000>.

## 2. Jednokratno podešavanje

1. Nalog na <https://huggingface.co> → **New Space** → ime `doc-assistant`, SDK **Docker** (*Blank*),
   hardware **CPU basic (free)**, vidljivost *Public*.
2. Space → **Settings → Variables and secrets**:

   | Tip | Ime | Vrednost |
   |---|---|---|
   | Secret | `ANTHROPIC_API_KEY` | `sk-ant-…` |
   | Secret | `API_TOKEN` | nasumičan string (štiti API od skripti; UI radi preko sesije) |
   | Variable | `PORT` | `7860` (port koji Space očekuje) |
   | Variable | `ALLOW_UPLOADS` | `false` — fiksni demo dokumenti, bez upload-a/brisanja |
   | Variable | `MAX_QUESTIONS_PER_SESSION` | `10` |
   | Variable | `MAX_QUESTIONS_PER_IP_HOUR` | `30` |
   | Variable | `ANTHROPIC_EFFORT` | `low` (opciono — brže i jeftinije) |

3. <https://huggingface.co/settings/tokens> → novi token sa **write** pravom.
4. GitHub repo → **Settings → Secrets and variables → Actions**:
   - *Secret* `HF_TOKEN` = taj token
   - *Variable* `HF_SPACE` = `<tvoj-hf-username>/doc-assistant`
5. GitHub → **Actions → Deploy demo → Run workflow** (ili samo spoji sledeći PR u `main`).

Workflow (`.github/workflows/deploy-space.yml`) objavljuje `main` u Space: README zamenjuje sa
`deploy/space_readme.md` (Space podešavanja: `sdk: docker`, `app_port: 7860`). Space sam
build-uje image (~5–8 min prvi put). Pri prvom pokretanju aplikacija indeksira demo dokumente.

## 3. Posle deploy-a

- Link (`https://<user>-doc-assistant.hf.space`) upiši u `README.md` (zadatak 4.4).
- Na console.anthropic.com postavi **mesečni limit potrošnje** za ključ koji koristi demo.
- Disk Space-a nije trajan: posle restarta indeks i log kreću ispočetka (za demo je to u redu).
