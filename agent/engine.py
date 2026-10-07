"""Generic workflow engine: routes a request, runs the workflow's steps in order,
applies conditions/errors uniformly, and records a full trace."""
from __future__ import annotations

import csv
import time
from contextlib import contextmanager
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, llm
from .models import (Escalation, NeedsInput, RouteDecision, RunResult, StepError,
                     StepRecord, ToolError)
from .registry import Registry
from .router import Router
from .tools import TOOLS
from .tools.files import write_csv


class RunContext:
    """Everything a step needs. Steps read/write ctx.state and finish by filling ctx.output / ctx.report."""

    def __init__(self, request: str, params: dict, spec, run_id: str):
        self.request, self.params, self.spec, self.run_id = request, dict(params), spec, run_id
        self.state: dict[str, Any] = {}
        self.output: dict[str, Any] = {}
        self.report: str = ""
        self.decisions: list[str] = []
        self.warnings: list[str] = []
        self.tool_calls: list[dict] = []
        self.artifacts: list[str] = []
        self._current_step = ""

    # parameters -------------------------------------------------------------------
    def param(self, name: str, default: Any = None) -> Any:
        v = self.params.get(name)
        if v in (None, ""):
            if default is not None:
                self.decide(f"Parameter '{name}' not supplied -> using default {default!r}")
            return default
        return v

    def require(self, name: str, message: str) -> Any:
        v = self.params.get(name)
        if v in (None, ""):
            raise NeedsInput(message, [name])
        return v

    # trace helpers ----------------------------------------------------------------
    def decide(self, text: str) -> None:
        self.decisions.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)

    # tools --------------------------------------------------------------------------
    def call_tool(self, name: str, **kw):
        if name not in TOOLS:
            raise StepError(f"Tool '{name}' is not registered")
        last: Exception | None = None
        for attempt in range(1, config.TOOL_MAX_ATTEMPTS + 1):
            t0 = time.perf_counter()
            try:
                result = TOOLS[name](**kw)
                self._log_tool(name, kw, "ok", t0, attempt)
                return result
            except ToolError as e:
                last = e
                self._log_tool(name, kw, f"error: {e}", t0, attempt)
                if not e.transient:
                    break
        raise last  # type: ignore[misc]

    def _log_tool(self, name, kw, status, t0, attempt):
        args = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in kw.items())
        self.tool_calls.append({"step": self._current_step, "tool": name, "args": args, "status": status,
                                "attempt": attempt, "ms": int((time.perf_counter() - t0) * 1000)})

    @contextmanager
    def batch(self, name: str):
        """For tight loops: calls still go through the registered tool but the trace shows ONE aggregated line."""
        n, t0 = [0], time.perf_counter()

        def fn(**kw):
            n[0] += 1
            return TOOLS[name](**kw)
        try:
            yield fn
        finally:
            self.tool_calls.append({"step": self._current_step, "tool": name, "args": f"{n[0]} calls", "status": "ok",
                                    "attempt": 1, "ms": int((time.perf_counter() - t0) * 1000)})

    def llm_json(self, system: str, user: str, offline, validate=None) -> tuple[dict, str]:
        t0 = time.perf_counter()
        data, src = llm.complete_json(system, user, offline, validate)
        self.tool_calls.append({"step": self._current_step, "tool": "llm", "args": user[:50].replace("\n", " ") + "...",
                                "status": f"ok ({src})", "attempt": 1, "ms": int((time.perf_counter() - t0) * 1000)})
        return data, src

    def save_csv(self, filename: str, rows: list[dict], headers: list[str] | None = None) -> str:
        path = config.OUTPUT_DIR / "artifacts" / f"{self.spec.id}_{filename}"
        write_csv(path, rows, headers)
        rel = str(path.relative_to(config.ROOT)) if path.is_relative_to(config.ROOT) else str(path)
        self.artifacts.append(rel)
        return rel


class Engine:
    def __init__(self, registry: Registry | None = None):
        self.registry = registry or Registry.load()
        self.router = Router(self.registry)

    def run(self, request: str, params: dict | None = None, workflow_id: str | None = None) -> RunResult:
        t_start = time.perf_counter()
        run_id = uuid.uuid4().hex[:8]
        route = self.router.route(request, workflow_id)

        if route.workflow_id is None:
            cands = ", ".join(f"{w} ({self.registry.specs[w].name})" for w, _ in route.candidates)
            return RunResult(run_id, request, None, "-", "no_match", route, [], [], [], [], {}, "",
                             message=f"I could not tell which workflow you mean. Closest: {cands}. "
                                     f"Could you rephrase or name the task?")
        spec = self.registry.specs[route.workflow_id]
        if not self.registry.implemented(spec.id):
            return RunResult(run_id, request, spec.id, spec.name, "not_implemented", route, [], [], [], [], {}, "",
                             message=f"{spec.name} is defined in Excel but has no step implementation yet.")

        merged = {**route.params, **(params or {})}          # explicit params win over extracted ones
        ctx = RunContext(request, merged, spec, run_id)
        records: list[StepRecord] = []
        status, message, missing = "success", "", []
        handlers = self.registry.handlers[spec.id]

        for i, (label, h) in enumerate(zip(spec.steps, handlers), start=1):
            ctx._current_step = label
            t0 = time.perf_counter()
            rec = StepRecord(i, label, "ok")
            try:
                detail = h.fn(ctx)
                rec.detail = detail or ""
            except NeedsInput as e:
                rec.status, rec.error = "failed", e.message
                status, message, missing = "needs_input", e.message, e.missing
            except Escalation as e:
                rec.status, rec.error = "failed", str(e)
                status, message = "escalated", str(e)
            except (StepError, ToolError) as e:
                rec.status, rec.error = ("warning" if h.optional else "failed"), str(e)
                if h.optional:
                    ctx.warn(f"Optional step '{label}' failed and was skipped: {e}")
                else:
                    status, message = "failed", f"Step '{label}' failed: {e}"
            except Exception as e:                       # unexpected bug: never crash the agent
                rec.status, rec.error = "failed", f"{type(e).__name__}: {e}"
                status, message = "failed", f"Unexpected error in step '{label}': {type(e).__name__}: {e}"
            rec.duration_ms = int((time.perf_counter() - t0) * 1000)
            rec.attempts = max([c["attempt"] for c in ctx.tool_calls if c["step"] == label] or [1])
            records.append(rec)
            if rec.status == "failed":
                break                                    # stop; remaining steps are shown as skipped

        for j in range(len(records), len(spec.steps)):
            records.append(StepRecord(j + 1, spec.steps[j], "skipped", "not executed (earlier step stopped the run)"))

        result = RunResult(run_id, request, spec.id, spec.name, status, route, records, ctx.decisions,
                           ctx.warnings, ctx.tool_calls, ctx.output, ctx.report, message, missing,
                           ctx.artifacts, int((time.perf_counter() - t_start) * 1000))
        self._append_run_log(result)
        return result

    # every real run is appended to a CSV with the same schema WF010 analyses
    def _append_run_log(self, r: RunResult) -> None:
        config.RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        new = not config.RUN_LOG.exists()
        with open(config.RUN_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "run_id", "workflow_id", "step_name", "status", "duration_ms", "error"])
            ts = datetime.now().isoformat(timespec="seconds")
            for s in r.steps:
                if s.status == "skipped":
                    continue
                st = ("success" if s.status != "failed" else
                      {"needs_input": "needs_input", "escalated": "escalated"}.get(r.status, "failed"))
                w.writerow([ts, r.run_id, r.workflow_id, s.name, st, s.duration_ms, s.error])
