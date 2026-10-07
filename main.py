"""CLI entry point.

  python main.py "Which products need restocking?"
  python main.py "Create a campaign brief for the new collection." --param goal="Boost sales" --param dates="2026-11-01 to 2026-11-30"
  python main.py --interactive
  python main.py --run-examples        # runs every Test_Questions row + edge cases, saves outputs/examples/
  python main.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent import config, llm
from agent.engine import Engine
from agent.formatter import to_json, to_text


def parse_params(items: list[str] | None) -> dict:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k.strip()] = v.strip()
    return out


def run_examples(engine: Engine) -> int:
    out_dir = config.OUTPUT_DIR / "examples"
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = [{"label": f"{t['Workflow_ID']} - {t['What_To_Check']}", "request": t["Test_Request"],
              "expect_workflow": t["Workflow_ID"], "expect_status": None, "params": {}} for t in engine.registry.test_requests]
    cases += json.loads((config.SAMPLES / "extra_requests.json").read_text(encoding="utf-8"))
    summary, bad = [], 0
    for n, c in enumerate(cases, 1):
        r = engine.run(c["request"], c.get("params"))
        ok = r.workflow_id == c["expect_workflow"] and (c["expect_status"] in (None, r.status))
        bad += not ok
        text = to_text(r)
        (out_dir / f"{n:02d}_{(r.workflow_id or 'none')}.txt").write_text(f"# {c['label']}\n\n{text}\n", encoding="utf-8")
        summary.append(f"{'PASS' if ok else 'FAIL'}  {n:02d}  {r.workflow_id or '-':5} {r.status:12} {c['label']}")
        print(f"\n{'=' * 100}\n# {c['label']}\n{'=' * 100}\n{text}")
    print("\n" + "\n".join(summary))
    (out_dir / "SUMMARY.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return 1 if bad else 0


def interactive(engine: Engine) -> None:
    print("AI Workflow Agent - type a request (or 'quit'). Workflows: " + ", ".join(f"{k}={v.name}" for k, v in engine.registry.specs.items()))
    while True:
        try:
            req = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if req.lower() in ("quit", "exit", ""):
            if req: break
            continue
        params: dict = {}
        while True:
            r = engine.run(req, params)
            print("\n" + to_text(r))
            if r.status == "needs_input" and r.missing:
                print("\nagent> I need more information.")
                try:
                    for m in r.missing[:2] if len(r.missing) > 1 and r.workflow_id == "WF005" else r.missing:
                        v = input(f"  {m}> ").strip()
                        if v: params[m] = v
                except (EOFError, KeyboardInterrupt):
                    break
                if not params: break
                continue
            break


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Excel-driven AI workflow agent")
    ap.add_argument("request", nargs="?", help="natural-language request")
    ap.add_argument("--param", action="append", help="explicit parameter key=value (repeatable)")
    ap.add_argument("--workflow", help="skip routing and force a workflow id, e.g. WF003")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--run-examples", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    engine = Engine()
    if a.list:
        print(f"LLM provider: {llm.provider()}")
        for wid, s in engine.registry.specs.items():
            print(f"{wid}  {'OK ' if engine.registry.implemented(wid) else 'TODO'}  {s.name:32} {len(s.steps)} steps | {s.trigger}")
        return 0
    if a.run_examples:
        return run_examples(engine)
    if a.interactive or not a.request:
        interactive(engine); return 0
    r = engine.run(a.request, parse_params(a.param), a.workflow)
    print(to_json(r) if a.json else to_text(r))
    return 0 if r.status in ("success", "needs_input", "escalated", "no_match") else 1


if __name__ == "__main__":
    sys.exit(main())
