# WORKFLOW — git, PR i provere

## Jednokratno podešavanje (faza 0)

```bash
# 1. U folderu projekta
git init -b main
git add .
git commit -m "chore: initial project skeleton"

# 2. Na GitHub-u napravi PRAZAN repo "doc-assistant" (bez README/.gitignore), pa:
git remote add origin https://github.com/<tvoj-username>/doc-assistant.git
git push -u origin main
```

## Svakodnevni ciklus (jedan zadatak iz PLAN.md = jedan PR)

```bash
git checkout main && git pull
git checkout -b feat/ingest            # nova grana za zadatak

# ... kod ...
uv run ruff format .                   # automatski formatiraj
uv run ruff check . --fix              # popravi lint gde može
uv run pytest                          # testovi

git add .
git commit -m "feat(ingest): add PDF and DOCX loaders"
git push -u origin feat/ingest
```

Zatim na GitHub-u: **Compare & pull request** → popuni template → sačekaj zeleni CI →
pregledaj "Files changed" → **Squash and merge** → **Delete branch**.

Lokalno posle merge-a:
```bash
git checkout main && git pull
git branch -d feat/ingest
```

Sa GitHub CLI (opciono): `gh pr create --fill` · `gh pr checks --watch` · `gh pr merge --squash --delete-branch`

## Imenovanje grana

`feat/...` nova funkcija · `fix/...` ispravka · `docs/...` dokumentacija · `chore/...` alati/podešavanja · `test/...` testovi

## Commit poruke (Conventional Commits, engleski)

```
feat(retrieval): add Chroma vector store
fix(ingest): keep page numbers for DOCX
docs: update CONTEXT after phase 1
test(llm): cover "not found" answer
chore(ci): cache uv dependencies
```

## Šta CI proverava (`.github/workflows/ci.yml`)

1. `uv sync --locked` — zavisnosti se poklapaju sa `uv.lock`
2. `ruff check .` — lint
3. `ruff format --check .` — formatiranje
4. `pytest` — testovi

Ako CI padne: otvori run u tabu *Actions*, popravi lokalno, commit + push na istu granu — PR se sam ažurira.

## Pravila

- Nikad direktno na `main`.
- `.env` nikad u git (već je u `.gitignore`).
- Svaki PR ažurira `CONTEXT.md` (+ štiklira stavke u `docs/PLAN.md`).
