"""Text normalisation + similarity (stdlib only: difflib + token Jaccard)."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from . import tool

_STOP = {"the", "a", "an", "of", "with", "and", "for", "by", "in"}
_SYNONYMS = {"tee": "tshirt", "tshirts": "tshirt", "shirts": "shirt"}


def normalize_sku(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def normalize_text(s: str, drop: set[str] | None = None) -> str:
    s = (s or "").lower().replace("-", " ")
    s = re.sub(r"\bt\s+shirt\b", "tshirt", s)
    s = re.sub(r"(\d+)\s*(ml|l|oz|g|kg|cm|mm)\b", r"\1\2", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [_SYNONYMS.get(t, t) for t in s.split() if t not in _STOP and t not in (drop or set())]
    return " ".join(toks)


@tool("text_similarity")
def text_similarity(a: str, b: str) -> float:
    """0..1 score = mean of token-sort ratio and token Jaccard."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    jac = len(set(ta) & set(tb)) / len(set(ta) | set(tb))
    return round((ratio + jac) / 2, 3)
