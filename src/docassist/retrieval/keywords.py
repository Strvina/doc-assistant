"""Keyword search (BM25) over chunk texts, to combine with embedding search (ADR-023).

Embeddings find passages with the same meaning in another language; keywords find the exact
word, number or code ("kantina", "M8", "QP-07") that a small embedding model can miss. The
index is tiny (one entry per chunk) and is built in memory from the vector store.

Tokens are lower-cased, stripped of diacritics (č → c, ü → u, ß → ss) and cut to their first
`STEM_LENGTH` characters: a crude stemmer that lets Serbian and German inflections match
("kantina", "kantine", "kantini" → "kantin").
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Sequence

from docassist.models import Chunk

STEM_LENGTH = 6
# BM25 parameters: k1 = how fast repeated words stop adding score, b = length normalisation.
K1 = 1.5
B = 0.75

_WORD = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = (
        "".join(ch for ch in plain if not unicodedata.combining(ch))
        .replace("đ", "dj")
        .replace("ß", "ss")
    )
    return [word[:STEM_LENGTH] for word in _WORD.findall(plain) if len(word) > 1 or word.isdigit()]


class KeywordIndex:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)
        self._terms = [Counter(tokenize(c.text)) for c in self._chunks]
        self._lengths = [sum(terms.values()) for terms in self._terms]
        self._avg_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        document_frequency: Counter[str] = Counter()
        for terms in self._terms:
            document_frequency.update(terms.keys())
        n = len(self._chunks)
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    def __len__(self) -> int:
        return len(self._chunks)

    def search(
        self, query: str, k: int, doc_ids: Collection[str] | None = None
    ) -> list[tuple[Chunk, float]]:
        """The `k` best-matching chunks with their BM25 score, best first (score > 0 only);
        only chunks of the documents in `doc_ids` when it is given."""
        query_terms = set(tokenize(query)) & self._idf.keys()
        if not query_terms or k <= 0:
            return []
        scored = []
        for chunk, terms, length in zip(self._chunks, self._terms, self._lengths, strict=True):
            if doc_ids is not None and chunk.doc_id not in doc_ids:
                continue
            score = 0.0
            for term in query_terms:
                tf = terms.get(term, 0)
                if tf:
                    norm = K1 * (1 - B + B * length / self._avg_length)
                    score += self._idf[term] * tf * (K1 + 1) / (tf + norm)
            if score > 0:
                scored.append((chunk, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]
