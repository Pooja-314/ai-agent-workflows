"""Core data models and exception types shared across the framework."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class NeedsInput(Exception):
    """Raised by a step when the workflow cannot continue without more user input
    (e.g. missing campaign goal, order not found). The engine turns it into a
    clean 'needs_input' result instead of a crash."""

    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.missing = missing or []


class Escalation(Exception):
    """Raised when the workflow's own rules say a human must decide
    (e.g. no suitable employee for a task)."""


class StepError(Exception):
    """A step failed in a way the workflow cannot recover from."""


class ToolError(Exception):
    """A tool/API call failed. transient=True means a retry may succeed."""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass
class WorkflowSpec:
    """One row of the 'Workflows' sheet."""
    id: str
    name: str
    trigger: str
    inputs: str
    steps: list[str]
    decision_logic: str
    tools: str
    expected_output: str


@dataclass
class StepRecord:
    index: int
    name: str
    status: str            # ok | failed | skipped | warning
    detail: str = ""
    duration_ms: int = 0
    error: str = ""
    attempts: int = 1


@dataclass
class RouteDecision:
    workflow_id: str | None
    method: str            # llm | lexical | forced
    confidence: float
    reasoning: str
    params: dict[str, Any] = field(default_factory=dict)
    candidates: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    request: str
    workflow_id: str | None
    workflow_name: str
    status: str            # success | needs_input | escalated | failed | no_match | not_implemented
    route: RouteDecision | None
    steps: list[StepRecord]
    decisions: list[str]
    warnings: list[str]
    tool_calls: list[dict]
    output: dict[str, Any]
    report: str
    message: str = ""
    missing: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    total_ms: int = 0
