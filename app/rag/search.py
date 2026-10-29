"""Find the minutes of a recorded meeting that answer a question. The user never has to say a time.

Two searches over the 1-minute parts, merged:
- meaning: the question's embedding against each part's embedding (cosine), which finds
  "the launch date" when someone said "we ship on Friday", in any language;
- keywords: BM25 over the part texts, which catches names, numbers and product words that
  embeddings can blur.
Each search ranks the parts; the ranks are merged with reciprocal rank fusion, so neither
search's score scale has to be tuned. With no embeddings (none set, or the call failed) the
keyword search is used alone.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter

import numpy as np

from app.rag.store import Part

TOP_PARTS = 3
RRF_K = 60
# The question's embedding takes 0.3 s to over 3 s on Nebius. When the question's words are in
# the meeting, keyword search can answer alone after EMBED_TIMEOUT. When none are (a Tamil
# question about an English meeting, other words for the same thing), keywords find nothing,
# so the meaning search is worth waiting for, up to EMBED_TIMEOUT_NO_KEYWORDS.
EMBED_TIMEOUT = 3.0
EMBED_TIMEOUT_NO_KEYWORDS = 10.0
# Qwen3-Embedding expects an instruction in front of a query (not in front of the documents).
QUERY_INSTRUCTION = ("Instruct: Given a question about a meeting, find the part of the meeting "
                     "that answers it\nQuery: ")


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def keyword_scores(texts: list[str], query: str, k1: float = 1.5, b: float = 0.75) -> list[float]:
    """BM25 score of each text for the query (0 when no query word is in it)."""
    docs = [Counter(tokenize(t)) for t in texts]
    if not docs:
        return []
    avg = sum(sum(d.values()) for d in docs) / len(docs) or 1.0
    scores = [0.0] * len(docs)
    for word in set(tokenize(query)):
        n = sum(1 for d in docs if word in d)
        if not n:
            continue
        idf = math.log(1 + (len(docs) - n + 0.5) / (n + 0.5))
        for i, d in enumerate(docs):
            if tf := d.get(word):
                length = sum(d.values())
                scores[i] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
    return scores


def cosine_scores(parts: list[Part], query_vec: list[float]) -> list[float | None]:
    q = np.asarray(query_vec, dtype=np.float32)
    q /= np.linalg.norm(q) or 1.0
    out: list[float | None] = []
    for p in parts:
        if not p.embedding:
            out.append(None)
            continue
        v = np.asarray(p.embedding, dtype=np.float32)
        out.append(float(v @ q / (np.linalg.norm(v) or 1.0)))
    return out


def rank_parts(parts: list[Part], question: str, query_vec: list[float] | None = None,
               top: int = TOP_PARTS) -> list[int]:
    """Indexes of the best parts for the question, best first."""
    candidates = [i for i, p in enumerate(parts) if p.text]
    fused: dict[int, float] = {}

    def add(scores: dict[int, float]) -> None:
        for rank, i in enumerate(sorted(scores, key=scores.get, reverse=True)):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)

    words = keyword_scores([parts[i].text for i in candidates], question)
    add({i: s for i, s in zip(candidates, words) if s > 0})
    if query_vec is not None:
        cos = cosine_scores([parts[i] for i in candidates], query_vec)
        add({i: s for i, s in zip(candidates, cos) if s is not None})
    return sorted(fused, key=fused.get, reverse=True)[:top]


async def find_parts(client, parts: list[Part], question: str, top: int = TOP_PARTS) -> tuple[list[int], str]:
    """Best parts for the question, and which search found them ("meaning+keywords" or "keywords")."""
    query_vec = None
    model = getattr(client, "embed_model", "")
    if model and any(p.embedding for p in parts):
        prefix = QUERY_INSTRUCTION if "qwen3-embedding" in model.lower() else ""
        words_found = any(s > 0 for s in keyword_scores([p.text for p in parts], question))
        timeout = EMBED_TIMEOUT if words_found else EMBED_TIMEOUT_NO_KEYWORDS
        try:
            query_vec = (await asyncio.wait_for(client.embed([prefix + question.strip()]), timeout))[0]
        except Exception:  # noqa: BLE001 - slow or failed: keyword search alone still answers
            query_vec = None
    found = rank_parts(parts, question, query_vec, top)
    return found, "meaning+keywords" if query_vec is not None else "keywords"
