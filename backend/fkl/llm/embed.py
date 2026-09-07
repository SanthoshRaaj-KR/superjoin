"""Embeddings, cached in the same SQLite file as everything else.

Embeddings do exactly one job in this project: deciding whether two *names* mean
the same thing. They resolve meaning — "Revenue from contracts with customers"
against "Revenue from services" — and they are never used for retrieval.

That distinction is deliberate and worth stating, because the reflex in this
problem space is to embed page chunks and search them. Once a claim is typed,
``WHERE entity AND metric AND period`` is exact, complete and free, where vector
search over the same data would be approximate and lossy. So there is no
similarity search over document text anywhere here — only over the short strings
that name things.

Vectors are cached by ``(model, text)``. The same predicates recur across
hundreds of pages, and a registry that re-embedded them on every lookup would
spend most of its budget rediscovering that "Revenue from services" is still
spelled the same way.
"""

from __future__ import annotations

import array
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import SETTINGS
from ..models import Embedding
from .client import raw_client

log = logging.getLogger(__name__)

_FLOAT = "f"  # float32


def _pack(vector: list[float]) -> bytes:
    return array.array(_FLOAT, vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    out = array.array(_FLOAT)
    out.frombytes(blob)
    return list(out)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Inputs are already unit-normalised by the API, but the
    denominator is kept so a cached vector from another source cannot silently
    produce a similarity above 1."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def embed_texts(
    session: Session, texts: list[str], model: str | None = None
) -> dict[str, list[float]]:
    """Embed a batch of strings, returning a mapping from text to vector.

    Cached rows are read first and only the misses are sent, so a repeated
    lookup costs one SELECT. The batch shape matters: the registry resolves a
    page's worth of predicates at once, and one request for twenty strings is
    both cheaper and faster than twenty requests.
    """
    model = model or SETTINGS.model_embed
    wanted = [t for t in dict.fromkeys(t.strip() for t in texts) if t]
    if not wanted:
        return {}

    cached = {
        row.text_key: _unpack(row.vector)
        for row in session.scalars(
            select(Embedding).where(
                Embedding.model == model, Embedding.text_key.in_(wanted)
            )
        )
    }
    missing = [t for t in wanted if t not in cached]
    if not missing:
        return cached

    response = raw_client().embeddings.create(model=model, input=missing)
    for text, item in zip(missing, response.data):
        vector = list(item.embedding)
        cached[text] = vector
        session.add(
            Embedding(
                model=model, text_key=text, dim=len(vector), vector=_pack(vector)
            )
        )
    session.flush()
    log.info("embedded %s new string(s), %s from cache", len(missing),
             len(wanted) - len(missing))
    return cached
