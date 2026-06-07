"""Semantic similarity for the idea-connection (Obsidian-style) graph.

We turn each video into a chunk of text (title + ideas + summary + tags) and
embed it, then connect videos whose vectors are close. Three backends are tried
in order of quality, so the pipeline always works — even with no extra packages
installed (which is handy for CI bootstrapping and local demos):

1. ``sentence-transformers`` — true multilingual semantic embeddings
   (model: ``paraphrase-multilingual-MiniLM-L12-v2``). This is the intended
   production backend.
2. ``scikit-learn`` TF-IDF — lexical vectors, decent and dependency-light.
3. A pure-Python TF-IDF — zero third-party dependencies, always available.

Set ``EMBED_BACKEND=tfidf`` to skip the heavy model, or
``EMBED_BACKEND=st`` to force sentence-transformers.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Sequence

_TOKEN_RE = re.compile(r"[\wа-яёА-ЯЁ]+", re.UNICODE)

# Common Russian + English stop words; trimmed but enough to reduce noise.
_STOP = set(
    "и в во не что он на я с со как а то все она так его но да ты к у же вы за "
    "бы по только ее мне было вот от меня еще нет о из ему теперь когда даже ну "
    "the a an of to in is it for on with as this that and or be are was were by "
    "при для это эта этот эти как же чтобы который которая которые video видео".split()
)


def build_text(rec: dict) -> str:
    """Compose the text used to represent a video for embedding."""
    parts: list[str] = [rec.get("title", ""), rec.get("topic", "")]
    parts.extend(rec.get("ideas", []))
    parts.append(rec.get("summary", ""))
    parts.extend(rec.get("tags", []))
    return " . ".join(p for p in parts if p)


def _tokenize(text: str) -> list[str]:
    return [t for t in (_TOKEN_RE.findall(text.lower())) if t not in _STOP and len(t) > 2]


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
def _embed_sentence_transformers(texts: Sequence[str]):
    from sentence_transformers import SentenceTransformer  # type: ignore

    model_name = os.environ.get(
        "EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    model = SentenceTransformer(model_name)
    vecs = model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return [list(map(float, v)) for v in vecs]


def _embed_sklearn(texts: Sequence[str]):
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.preprocessing import normalize  # type: ignore

    vec = TfidfVectorizer(tokenizer=_tokenize, lowercase=False, min_df=1)
    mat = normalize(vec.fit_transform(texts))
    return mat  # sparse matrix; cosine handled specially below


def _embed_pure_tfidf(texts: Sequence[str]) -> list[dict[str, float]]:
    docs = [_tokenize(t) for t in texts]
    n = len(docs)
    df: Counter[str] = Counter()
    for toks in docs:
        for term in set(toks):
            df[term] += 1
    vectors: list[dict[str, float]] = []
    for toks in docs:
        tf = Counter(toks)
        vec: dict[str, float] = {}
        for term, count in tf.items():
            idf = math.log((1 + n) / (1 + df[term])) + 1.0
            vec[term] = count * idf
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        vectors.append({t: w / norm for t, w in vec.items()})
    return vectors


# --------------------------------------------------------------------------- #
# Similarity
# --------------------------------------------------------------------------- #
def _cosine_dense(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are L2-normalized


def _cosine_sparse(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


# Cosine similarity lives on different scales per backend: dense semantic
# embeddings cluster around 0.3-0.8 for related texts, while sparse TF-IDF on
# short multilingual texts is much lower. So thresholds are backend-specific.
_DEFAULT_THRESHOLD = {
    "sentence-transformers": 0.42,
    "sklearn-tfidf": 0.05,
    "pure-tfidf": 0.045,
}


def compute_edges(
    records: Sequence[dict],
    *,
    top_k: int = 5,
    threshold: float | None = None,
) -> tuple[list[dict], str]:
    """Return ``(edges, backend_name)`` connecting semantically similar videos.

    Each video links to up to ``top_k`` of its nearest neighbours whose cosine
    similarity clears a backend-specific threshold. Edges are undirected and
    de-duplicated. Pass ``threshold`` to override the per-backend default.
    """
    ids = [r["video_id"] for r in records]
    n = len(records)
    if n < 2:
        return [], "none"

    sims, backend = _similarity_matrix([build_text(r) for r in records])
    thr = threshold if threshold is not None else _DEFAULT_THRESHOLD.get(backend, 0.2)
    return _knn_from_matrix(sims, ids, top_k, thr), backend


def _similarity_matrix(texts: list[str]) -> tuple[list[list[float]], str]:
    """Build an NxN cosine-similarity matrix using the best available backend."""
    n = len(texts)
    backend = os.environ.get("EMBED_BACKEND", "auto").lower()

    if backend in ("auto", "st"):
        try:
            vecs = _embed_sentence_transformers(texts)
            sims = [[_cosine_dense(vecs[i], vecs[j]) for j in range(n)] for i in range(n)]
            return sims, "sentence-transformers"
        except Exception:
            if backend == "st":
                raise

    if backend in ("auto", "tfidf", "sklearn"):
        try:
            mat = _embed_sklearn(texts)
            return (mat @ mat.T).toarray().tolist(), "sklearn-tfidf"  # type: ignore[attr-defined]
        except Exception:
            pass

    sparse = _embed_pure_tfidf(texts)
    sims = [[_cosine_sparse(sparse[i], sparse[j]) for j in range(n)] for i in range(n)]
    return sims, "pure-tfidf"


def _knn_from_matrix(sims, ids, top_k: int, threshold: float) -> list[dict]:
    n = len(ids)
    seen: set[tuple[str, str]] = set()
    edges: list[dict] = []
    for i in range(n):
        neighbours = sorted(
            ((float(sims[i][j]), j) for j in range(n) if j != i),
            reverse=True,
        )[:top_k]
        for score, j in neighbours:
            if score < threshold:
                continue
            key = tuple(sorted((ids[i], ids[j])))
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source": key[0],
                "target": key[1],
                "weight": round(score, 4),
                "type": "semantic",
            })
    return edges
