"""Renders a RunResult the way the assignment asks: workflow selected, steps executed, final result."""
from __future__ import annotations

import json
from dataclasses import asdict

from .models import RunResult

_MARK = {"ok": "[OK]  ", "failed": "[FAIL]", "skipped": "[SKIP]", "warning": "[WARN]"}


def to_text(r: RunResult) -> str:
    L = [f'User Request: "{r.request}"', ""]
    if r.workflow_id is None:
        L += ["Selected Workflow: (none)", f"Router: {r.route.method if r.route else '-'} - {r.route.reasoning if r.route else ''}", "",
              "Result:", f"  {r.message}"]
        return "\n".join(L)
    L.append(f"Selected Workflow: {r.workflow_name} ({r.workflow_id})")
    L.append(f"  Router: {r.route.method} | confidence {r.route.confidence:.2f} | {r.route.reasoning}")
    if r.route.params:
        L.append(f"  Parameters extracted from request: {r.route.params}")
    L += ["", "Steps Executed:"]
    for s in r.steps:
        extra = f" - {s.detail}" if s.detail else ""
        extra += f" - {s.error}" if s.error and s.status != "ok" else ""
        retry = f" (after {s.attempts} attempts)" if s.attempts > 1 else ""
        L.append(f"  {s.index}. {_MARK[s.status]} {s.name} [{s.duration_ms} ms]{retry}{extra}")
    if r.decisions:
        L += ["", "Conditions / Decisions:"] + [f"  * {d}" for d in r.decisions]
    if r.tool_calls:
        L += ["", "Tool / API calls:"]
        for c in r.tool_calls:
            L.append(f"  - {c['tool']}({c['args']}) -> {c['status']}" + (f" [attempt {c['attempt']}]" if c["attempt"] > 1 or "error" in c["status"] else ""))
    if r.warnings:
        L += ["", "Warnings:"] + [f"  ! {w}" for w in r.warnings]
    L += ["", f"Status: {r.status.upper()}  ({r.total_ms} ms)", "", "Result:"]
    L.append(r.report if r.report and r.status == "success" else f"  {r.message}")
    if r.status == "needs_input":
        L.append(f"\n  -> Provide: {', '.join(r.missing)}  (e.g. --param {r.missing[0]}=...)" if r.missing else "")
    return "\n".join(L)


def to_json(r: RunResult) -> str:
    return json.dumps(asdict(r), indent=2, default=str)
