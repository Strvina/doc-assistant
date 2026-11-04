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

## Answers with Claude

*Pending — needs the API key.* Planned runs, each with `python -m docassist.eval` and `--agent`:

| Run | Correct overall | Page cited | Fact recall | "Not found" correct | Wrong "not found" | Made-up citations | Latency avg / p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5, RAG | | | | | | | | |
| claude-sonnet-5-5, agent | | | | | | | | |
| claude-haiku-5-5, RAG | | | | | | | | |
