# Evaluation results

Question set: [`eval/questions.yaml`](../eval/questions.yaml) — 30 questions (Serbian, German,
English): 27 with a known source page and key facts, 3 the documents cannot answer.
Runner: `uv run python -m docassist.eval` (see the README).

## Retrieval (no LLM)

The right page among the top-5 retrieved passages, 27 answerable questions (2026-11-04):

| Embeddings | Chunk size | Top 5 | Top 1 | Query time |
|---|---|---|---|---|
| keyword hashing (baseline) | 800 | 56 % | — | — |
| potion-multilingual-128M (0.5 GB) | 800 | 78 % | 52 % | 4 ms |
| paraphrase-multilingual-MiniLM-L12-v2 (0.22 GB) | 800 | 89 % | 74 % | 18 ms |
| paraphrase-multilingual-mpnet-base-v2 (1.0 GB) | 800 | 93 % | 74 % | 48 ms |
| **paraphrase-multilingual-MiniLM-L12-v2** | **500** | **100 %** | **85 %** | 16 ms |
| paraphrase-multilingual-mpnet-base-v2 | 500 | 100 % | 78 % | 48 ms |

Caveats: a small set written by the author with knowledge of the documents, and the chunk size was
chosen on the same set — a sanity check, not a benchmark.

## Answers with free-tier models (ADR-021, ADR-022)

`uv run python -m docassist.eval --provider groq`, 2026-11-04, local MiniLM embeddings, top-5.
The index also held one extra, unrelated document uploaded while testing (a coffee price list),
which competes in retrieval.

| Run | Correct overall | Page cited | Fact recall | "Not found" correct | Wrong "not found" | Made-up citations | Latency avg / p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| groq openai/gpt-oss-120b, RAG | 93 % | 93 % | 93 % | 100 % | 7 % (2) | 0 | 1.9 s / 9.0 s | free |

The two misses are retrieval/chunking, not the model inventing things:

- `faq-canteen` ("Kada radi kantina?"): the canteen passage is not in the top 5 (another passage
  from the same FAQ page is, so the page-level "retrieved" metric still counts it). The model
  correctly says "not found". Keyword (hybrid) search would catch the exact word.
- `mount-torque-m8`: the torque table is split into two chunks; the busbar heading sits with the
  M6 row, the M8 row is in the next chunk. gpt-oss reads this strictly as "no M8 value for
  busbars". Keeping tables together with their heading when chunking would fix it.

Gemini (`gemini-3.5-flash`) answered the M8 question correctly in a manual test, but its free
tier allows 20 requests per day, too few for a full run; the fallback (Flash-Lite) took 15–20 s
per call that day.

## Answers with Claude

*Pending — needs the API key.* Planned runs, each with `python -m docassist.eval` and `--agent`:

| Run | Correct overall | Page cited | Fact recall | "Not found" correct | Wrong "not found" | Made-up citations | Latency avg / p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5, RAG | | | | | | | | |
| claude-sonnet-5-5, agent | | | | | | | | |
| claude-haiku-5-5, RAG | | | | | | | | |
