# Uputstvo za AI asistenta (Claude Code / Copilot / bilo koji agent)

Ovaj fajl se automatski učitava u Claude Code. Za druge asistente, nalepi ga na početku sesije.

## Pre bilo kakvog rada

1. Pročitaj `CONTEXT.md` (trenutno stanje i sledeći korak).
2. Pročitaj zadatak koji je na redu u `docs/PLAN.md`.
3. Za arhitekturu i odluke: `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`.

## Pravila

- Komunikacija sa mnom: **srpski**. Kod, imena, komentari, commit poruke i README: **engleski**.
- Radi se isključivo na feature granama, nikad direktno na `main` (vidi `docs/WORKFLOW.md`).
- Pre svakog commita moraju proći:
  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy
  uv run pytest
  ```
- Testovi **nikad ne zovu prave API-je** (Claude/OpenAI). Koristi fake/mock implementacije.
- Nove zavisnosti dodaj sa `uv add <paket>` (ili `uv add --dev <paket>`), nikad ručno u `pyproject.toml`.
- Tajne (API ključevi) samo u `.env`, nikad u kodu ili commitu.
- Male, fokusirane izmene: jedan zadatak iz plana = jedan PR.
- Nova arhitektonska odluka → novi unos u `docs/DECISIONS.md`.

## Na kraju sesije

Ažuriraj `CONTEXT.md` (Trenutno stanje, Sledeći korak, Dnevnik sesija) i štikliraj
urađene stavke u `docs/PLAN.md`. Te izmene idu u isti PR.
