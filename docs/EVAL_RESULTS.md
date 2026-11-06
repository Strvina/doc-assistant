# Evaluation results

Question set: [`eval/questions.yaml`](../eval/questions.yaml) — 36 questions (Serbian, German,
English): 33 with a known source page and key facts, 3 the documents cannot answer. Runs before
2026-11-05 used the first 30 (no Excel/PowerPoint documents yet).
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

### Stricter check: every key fact in the retrieved passages (ADR-023)

"Right page retrieved" can pass while the passage with the answer is missing (another passage
from the same page was found). This column checks that all key facts are in the passages the
model receives. Clean index (demo documents only), 27 answerable questions, 2026-11-04:

| Search | Chunking | Top 5 | Top 3 |
|---|---|---|---|
| embeddings | fixed 500/100 | 96 % (misses `faq-canteen`) | 93 % |
| embeddings + keywords merged (RRF) | fixed 500/100 | 93 % | 89 % |
| embeddings + keywords merged (RRF, keyword weight 0.3) | fixed 500/100 | 93 % | 89 % |
| embeddings | by section | 96 % | 93 % |
| **embeddings + 1 keyword hit added** | **by section** | **100 %** | 96 % |

Merging the keyword ranking (RRF) pushed correct German passages for Serbian questions below
the cut, because keywords cannot match across languages; adding the single best keyword hit
keeps the embedding ranking intact and still catches exact words such as "kantina".

## Scanned documents (OCR, ADR-024)

Both demo PDFs replaced by **image-only copies** (every page rendered at 150 dpi, grey, no text
layer — like a scan), same file names; the DOCX/TXT documents unchanged. 2026-11-04.

| | Words of the original found by OCR | All key facts retrieved | Groq answers correct | Made-up citations |
|---|---|---|---|---|
| `pravilnik_o_radu.pdf` (scan) | 100 % | | | |
| `montageanleitung_pv400.pdf` (scan) | 98 % | | | |
| **Whole eval set with the scans** | | **100 %** | **100 %** | **0** |

PyMuPDF's built-in OCR missed the torque table on the scan entirely; calling Tesseract directly
reads it (`M8 20 Nm 13 mm`), which is why the app does that. On product photos (a coffee/drinks
price list with bottle pictures) Tesseract still produces partial and noisy text.

## Answers with free-tier models (ADR-021, ADR-022)

**After ADR-023** (hybrid search + section chunking), clean index, 2026-11-04:

| Run | Correct overall | Page cited | Fact recall | "Not found" correct | Wrong "not found" | Made-up citations | Latency avg / p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| groq openai/gpt-oss-120b, RAG | **100 %** | 100 % | 100 % | 100 % | 0 % | 0 | 6.2 s / 10.7 s ¹ | free |
| groq openai/gpt-oss-120b, agent | **100 %** | 100 % | 100 % | 100 % | 0 % | 0 | 12.8 s / 22.2 s ¹ | free |

¹ The runs send 30 questions back to back and wait out Groq's per-minute token limit; a single
question takes 1–3 s (agent: 3–7 s). The agent used ~3× the tokens of plain RAG (101k vs 35k in).

**After the table rule** (prompt: short table cells, descriptions under the table), clean index,
2026-11-05:

| Run | Correct overall | Page cited | Fact recall | "Not found" correct | Wrong "not found" | Made-up citations | Latency avg / p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| groq openai/gpt-oss-120b, RAG | **100 %** | 100 % | 100 % | 100 % | 0 % | 0 | 5.1 s / 11.0 s | free |
| groq openai/gpt-oss-120b, agent | 90 % | 89 % | 93 % | 100 % | 4 % | 0 | 11.2 s / 23.4 s | free |

None of the agent misses come from the new rule. Two questions (`qc-retention`, `qc-aql`) hit
free-tier limits on both Groq (per minute) and the Gemini fallback (per day) and got no answer.
`qc-insulation` was answered correctly, but with two sources in one bracket
(`[quality_control_procedure.docx, str. 2; montageanleitung_pv400.pdf, str. 3]`), which the
citation parser does not read yet.

**With Excel and PowerPoint** (ADR-028: 34 questions, 4 new on `rezervni_delovi.xlsx` and
`pv400_sales_training.pptx`, one of them answered only in the speaker notes), clean index, 2026-11-05:

| Run | Correct overall | Page cited | Fact recall | "Not found" correct | Wrong "not found" | Made-up citations | Latency avg / p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| groq openai/gpt-oss-120b, RAG | **100 %** | 100 % | 100 % | 100 % | 0 % | 0 | 6.8 s / 11.7 s | free |

Retrieval: expected page and all key facts in the top 5 for every question. Before every slide
started with the presentation's title, neither PowerPoint question found its slide ("Warranty and
service" does not say which product) and RAG scored 94 %. The agent run that day answered 15 of 34
questions, all 15 correctly (the new Excel/PowerPoint ones among them); the other 19 hit the
free-tier limits of Groq and the Gemini fallback after several runs that day, so it is not a
measurement. Several sources in one bracket are now read as citations.

**Whole lists** (ADR-030: chunks around each hit, one passage per page, lead-in lines as headings,
list rule in the prompt; 36 questions, 2 new ones that need every item of a list), clean index,
2026-11-06:

| Run | Correct overall | Page cited | Fact recall | "Not found" correct | Wrong "not found" | Made-up citations | Latency avg / p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| groq openai/gpt-oss-120b, RAG | **100 %** | 100 % | 100 % | 100 % | 0 % | 0 | 6.6 s / 16.4 s | free |

Input tokens: ~1,880 per question (before: ~1,170) — the price of sending the neighbouring chunks.
Outside the eval set, on a 96-page scanned textbook uploaded by the user: "Kategorije zaraznih
bolesti" (8 numbered categories on one page, cut into 4 chunks) got a wrong list before (category 1
plus "diseases with absolute disposition" from another chapter); now RAG and the agent list all 8
with their diseases. Re-reading Latin pages without Cyrillic removed all 1,171 Cyrillic look-alike
letters from that scan.

**Before** (fixed chunks, embeddings only; the index also held one unrelated uploaded document):

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
