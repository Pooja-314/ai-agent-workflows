"""Tool registry. A tool is a plain Python function registered under a name;
workflows call tools through ctx.call_tool(name, ...) so every call is logged,
retried (for transient errors) and shown in the final trace."""
from __future__ import annotations

from typing import Callable

TOOLS: dict[str, Callable] = {}


def tool(name: str):
    def deco(fn: Callable) -> Callable:
        TOOLS[name] = fn
        return fn
    return deco


def load_all() -> None:
    # importing the modules registers their tools
    from . import files, calculator, similarity, orders  # noqa: F401
