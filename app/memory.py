"""Example memory: retrieval-augmented SQL writing with a FAISS vector index.

Every question the agent answers correctly on a sample dataset is saved with its SQL and an embedding of the
question. When a new question arrives, the most similar solved questions on the same dataset are retrieved
(cosine similarity in FAISS) and given to the model as worked examples, so the agent learns the dataset's
patterns from real usage. The embeddings live in the history database, so the index is rebuilt on startup.

The embedding model is pluggable: production uses a small local ONNX model through fastembed (no extra API
key or GPU); tests use a deterministic stand-in so they run offline.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Protocol

import faiss
import numpy as np

from app.store import Store

log = logging.getLogger("datachat.memory")


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return one float32 row per text."""


class FastEmbedEmbedder:
    """Sentence embeddings from a small local ONNX model (BAAI/bge-small-en-v1.5 by default, 384 dims)."""

    def __init__(self, model_name: str, cache_dir: str | None = None):
        from fastembed import TextEmbedding  # imported here so tests never load the model

        self.model = TextEmbedding(model_name, cache_dir=cache_dir or None)
        self.dim = int(self.embed(["warm up"]).shape[1])

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(list(self.model.embed(texts)), dtype="float32")


def normalise_question(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


@dataclass
class Example:
    question: str
    sql: str
    score: float


class _DatasetIndex:
    def __init__(self, dim: int):
        self.index = faiss.IndexFlatIP(dim)  # inner product on unit vectors = cosine similarity
        self.items: list[tuple[str, str]] = []
        self.seen: set[str] = set()


class ExampleMemory:
    """One FAISS index per dataset, backed by the `examples` table in the history database."""

    def __init__(self, store: Store, embedder: Embedder, top_k: int = 3, min_score: float = 0.6,
                 max_per_dataset: int = 2000):
        self.store, self.embedder = store, embedder
        self.top_k, self.min_score, self.max_per_dataset = top_k, min_score, max_per_dataset
        self.indexes: dict[str, _DatasetIndex] = {}
        self.lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------ internals
    def _vectors(self, texts: list[str]) -> np.ndarray:
        v = np.ascontiguousarray(self.embedder.embed(texts), dtype="float32")
        faiss.normalize_L2(v)
        return v

    def _index(self, dataset_id: str) -> _DatasetIndex:
        if dataset_id not in self.indexes:
            self.indexes[dataset_id] = _DatasetIndex(self.embedder.dim)
        return self.indexes[dataset_id]

    def _load(self) -> None:
        rows = self.store.all_examples()
        for r in rows:
            vec = np.frombuffer(r["embedding"], dtype="float32")
            if vec.shape[0] != self.embedder.dim:  # saved with another model: re-embed the question
                vec = self._vectors([r["question"]])[0]
            idx = self._index(r["dataset_id"])
            idx.index.add(vec.reshape(1, -1))
            idx.items.append((r["question"], r["sql"]))
            idx.seen.add(normalise_question(r["question"]))
        if rows:
            log.info("Loaded %d solved examples into the vector index", len(rows))

    # ------------------------------------------------------------------ public
    def size(self, dataset_id: str | None = None) -> int:
        if dataset_id is not None:
            return len(self.indexes[dataset_id].items) if dataset_id in self.indexes else 0
        return sum(len(i.items) for i in self.indexes.values())

    def search(self, dataset_id: str, question: str, k: int | None = None) -> list[Example]:
        """The most similar solved questions on this dataset, best first, above the similarity threshold."""
        k = k or self.top_k
        with self.lock:
            idx = self.indexes.get(dataset_id)
            if idx is None or not idx.items:
                return []
            scores, ids = idx.index.search(self._vectors([question]), min(k, len(idx.items)))
            target = normalise_question(question)
            out = []
            for score, i in zip(scores[0], ids[0], strict=True):
                if i < 0 or score < self.min_score:
                    continue
                q, sql = idx.items[i]
                if normalise_question(q) == target:  # the same question: the answer cache already handles it
                    continue
                out.append(Example(q, sql, round(float(score), 3)))
            return out

    def add(self, dataset_id: str, question: str, sql: str) -> bool:
        """Remember a correctly answered question. Returns False when it was already known or the index is full."""
        key = normalise_question(question)
        with self.lock:
            idx = self._index(dataset_id)
            if not key or key in idx.seen or len(idx.items) >= self.max_per_dataset:
                return False
            vec = self._vectors([question])
            self.store.add_example(dataset_id=dataset_id, question=question, sql=sql,
                                   embedding=vec[0].tobytes())
            idx.index.add(vec)
            idx.items.append((question, sql))
            idx.seen.add(key)
            return True
