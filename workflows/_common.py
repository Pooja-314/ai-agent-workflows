"""Small helpers shared by workflow modules."""
from __future__ import annotations

import re


def to_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def table(rows: list[dict], cols: list[str], limit: int = 50) -> str:
    """Plain-text table for reports."""
    if not rows:
        return "  (none)"
    shown = rows[:limit]
    widths = [max(len(c), *(len(str(r.get(c, ""))) for r in shown)) for c in cols]
    line = "  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    sep = "  " + "  ".join("-" * w for w in widths)
    body = ["  " + "  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)) for r in shown]
    more = [f"  ... {len(rows) - limit} more"] if len(rows) > limit else []
    return "\n".join([line, sep, *body, *more])


def snake(s: str) -> str:
    s = re.sub(r"\(.*?\)|[^a-zA-Z0-9]+", " ", s or "").strip().lower()
    return re.sub(r"\s+", "_", s)
