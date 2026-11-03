"""Text → vector. One interface, a real (OpenAI) and an offline (fake) implementation."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    @property
    def name(self) -> str:
        """Identifies the model; an index can only be searched with the embedder that built it."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Embeddings from the OpenAI API (ADR-004)."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        client: Any = None,
        batch_size: int = 100,
    ) -> None:
        if client is None:
            from openai import OpenAI  # imported lazily: tests and fake mode don't need it

            client = OpenAI(api_key=api_key)
        self._client = client
        self._model = model
        self._batch_size = batch_size

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = self._client.embeddings.create(model=self._model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
        return vectors


class FakeEmbedder:
    """Deterministic, offline "embedding": hashed bag of words and word trigrams.

    Texts that share words get similar vectors, which is enough for tests and for trying the
    pipeline without an API key. It does not understand meaning or translate between
    languages, so a Serbian question will not find a German answer.
    """

    def __init__(self, dimensions: int = 512) -> None:
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return f"fake:hashing-{self._dimensions}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for feature in _features(text):
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            sign = 1.0 if value & 1 else -1.0
            vector[(value >> 1) % self._dimensions] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # Cosine distance is undefined for a zero vector; use a fixed unit vector instead.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]


def _features(text: str) -> list[str]:
    features = []
    for word in _WORD.findall(text.lower()):
        features.append(f"w:{word}")
        padded = f"#{word}#"
        # Trigrams make "pritezanja" and "pritezanje" similar, like a crude stemmer.
        features.extend(f"t:{padded[i : i + 3]}" for i in range(len(padded) - 2))
    return features
