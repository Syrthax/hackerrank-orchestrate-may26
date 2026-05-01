from collections import Counter
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_ALIAS: dict[str, str] = {
    "privacy-and-legal": "privacy",
    "privacy_and_legal": "privacy",
    "account-management": "account_management",
    "conversation-management": "conversation_management",
    "getting-started": "general_support",
    "getting_started": "general_support",
    "general-help": "general_support",
    "general_help": "general_support",
    "travel-support": "travel_support",
    "travel_support": "travel_support",
    "uncategorized": "general_support",
    "general": "general_support",
    "index": "general_support",
}


@dataclass
class Index:
    vectorizer: TfidfVectorizer
    matrix: object  # scipy sparse matrix (CSR)


def build_index(chunks: list[dict]) -> Index:
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=1,
        sublinear_tf=True,
        strip_accents="unicode",
    )
    matrix = vectorizer.fit_transform([c["text"] for c in chunks])
    return Index(vectorizer=vectorizer, matrix=matrix)


def retrieve(
    query: str,
    index: Index,
    chunks: list[dict],
    domain: str = None,
    top_k: int = 6,
) -> list[dict]:
    if domain is not None:
        pairs = [(i, c) for i, c in enumerate(chunks) if c["domain"] == domain]
    else:
        pairs = list(enumerate(chunks))

    if not pairs:
        return []

    idx_list, sub_chunks = zip(*pairs)
    sub_matrix = index.matrix[list(idx_list)]
    query_vec = index.vectorizer.transform([query])
    scores = cosine_similarity(query_vec, sub_matrix).flatten()
    top = np.argsort(scores)[::-1][:top_k]

    return [{**sub_chunks[i], "score": float(scores[i])} for i in top]


def _normalize(subdir: str) -> str:
    s = subdir.lower()
    if s in _ALIAS:
        return _ALIAS[s]
    s2 = s.replace("-", "_")
    if s2 in _ALIAS:
        return _ALIAS[s2]
    if s2.endswith("s"):
        s2 = s2[:-1]
    return s2


def derive_product_area(chunks: list[dict]) -> str:
    if not chunks:
        return "general_support"
    counts = Counter(_normalize(c["subdir"]) for c in chunks if c.get("subdir"))
    return counts.most_common(1)[0][0] if counts else "general_support"
