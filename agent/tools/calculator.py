"""Deterministic maths. Kept out of the LLM on purpose: numbers must be exact."""
from __future__ import annotations

from ..models import ToolError
from . import tool


@tool("calculator")
def calculator(op: str, **kw):
    if op == "pct_diff":                     # (vendor - internal) / internal * 100
        base = kw["base"]
        if base == 0:
            raise ToolError("division by zero: base price is 0")
        return round((kw["other"] - base) / base * 100, 2)
    if op == "reorder_qty":                  # top up to max_stock, else to 2x minimum
        current, minimum, maximum = kw["current"], kw["minimum"], kw.get("maximum")
        target = maximum if maximum else minimum * 2
        return max(int(round(target - current)), 1)
    if op == "mean":
        vals = kw["values"]
        return round(sum(vals) / len(vals), 2) if vals else 0.0
    if op == "rate":
        return round(kw["part"] / kw["total"] * 100, 2) if kw["total"] else 0.0
    raise ToolError(f"unknown calculator op '{op}'")
