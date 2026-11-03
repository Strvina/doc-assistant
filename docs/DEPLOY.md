# DEPLOY — javni demo (Streamlit Community Cloud)

Besplatno, direktno iz GitHub repoa. Radi se **posle** spajanja PR-ova u `main` i kad API ključevi rade lokalno.

## 1. Provera lokalno

```bash
cp .env.example .env          # upiši ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENAI_MODEL
uv run python -m docassist.cli index data/sample_docs --rebuild
uv run python -m docassist.cli ask "Koliki je moment pritezanja za M8?"
uv run streamlit run app/streamlit_app.py
```

## 2. Deploy

1. <https://share.streamlit.io> → prijava preko GitHub-a → **Create app** → *Deploy a public app from GitHub*.
2. Repository `Strvina/doc-assistant`, branch `main`, main file `app/streamlit_app.py`.
3. **Advanced settings** → Python **3.12** → **Secrets** (TOML; svaka vrednost postaje env promenljiva):

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   OPENAI_API_KEY = "sk-..."
   OPENAI_MODEL = "..."              # aktuelni chat model iz OpenAI dokumentacije
   LLM_PROVIDER = "claude"
   ALLOW_UPLOADS = "false"           # javni demo: fiksni demo dokumenti, bez upload-a/brisanja
   MAX_QUESTIONS_PER_SESSION = "10"  # štiti API kredit
   ANTHROPIC_EFFORT = "low"          # brži i jeftiniji odgovori (opciono)
   ```

4. **Deploy**. Pri prvom pokretanju aplikacija sama indeksira `data/sample_docs/` (~35 chunk-ova,
   trošak embeddings-a je zanemarljiv). Disk na Community Cloud-u nije trajan, pa se to ponavlja
   posle svakog restarta.

> Ako platforma ne prepozna `pyproject.toml`/`uv.lock`, napravi `requirements.txt`:
> `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt`, dodaj red `.` na kraj
> (instalira i sam paket `docassist`), commit kroz PR.

## 3. Posle deploy-a

- Link upiši u `README.md` (zadatak 4.4) i napravi screenshot za README (4.5).
- Na console.anthropic.com i platform.openai.com postavi **mesečni limit potrošnje** za ključeve
  koje koristi demo.
