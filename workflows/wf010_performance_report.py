"""WF010 Workflow Performance Report (reads sample logs + logs of real runs)."""
from collections import Counter, defaultdict

from agent import config
from agent.models import StepError
from agent.registry import step, workflow_meta
from ._common import table, to_float

workflow_meta("WF010", keywords=["performance", "failing", "failure rate", "report", "logs", "execution time", "slow", "errors"],
              param_keys=["logs_csv", "include_live_runs", "failure_threshold_pct", "time_threshold_ms"])


@step("WF010")
def load_execution_logs(ctx):
    rows = ctx.call_tool("csv_reader", path=ctx.param("logs_csv", "data/samples/execution_logs.csv"))["rows"]
    live = config.RUN_LOG
    if live.exists() and str(ctx.params.get("include_live_runs", "")).lower() in ("1", "true", "yes"):
        rows += ctx.call_tool("csv_reader", path=str(live))["rows"]
        ctx.decide("Also included logs from real agent runs (outputs/run_log.csv)")
    if not rows:
        raise StepError("No execution logs found")
    ctx.state["logs"] = rows
    return f"{len(rows)} step-execution log rows"


@step("WF010")
def calculate_success_failure_rate(ctx):
    runs = defaultdict(lambda: {"wf": None, "failed": False, "ms": 0})
    for r in ctx.state["logs"]:
        d = runs[r["run_id"]]
        d["wf"] = r["workflow_id"]
        d["failed"] |= (r["status"] == "failed")      # needs_input / escalated are NOT failures
        d["ms"] += to_float(r["duration_ms"]) or 0
    ctx.state["runs"] = runs
    per = defaultdict(lambda: {"runs": 0, "failed": 0, "ms": 0})
    for d in runs.values():
        p = per[d["wf"]]; p["runs"] += 1; p["failed"] += d["failed"]; p["ms"] += d["ms"]
    ctx.state["per"] = per
    with ctx.batch("calculator") as calc:
        for wid, p in per.items():
            p["failure_rate"] = calc(op="rate", part=p["failed"], total=p["runs"])
    return f"{len(runs)} runs across {len(per)} workflows"


@step("WF010")
def calculate_average_execution_time(ctx):
    with ctx.batch("calculator") as calc:
        for p in ctx.state["per"].values():
            p["avg_ms"] = calc(op="mean", values=[p["ms"] / p["runs"]])
    return ", ".join(f"{w}: {int(p['avg_ms'])}ms" for w, p in sorted(ctx.state["per"].items()))


@step("WF010")
def identify_frequent_errors(ctx):
    errs = defaultdict(Counter)
    for r in ctx.state["logs"]:
        if r["status"] == "failed" and r.get("error"):
            errs[r["workflow_id"]][r["error"]] += 1
    ctx.state["errors"] = errs
    return "; ".join(f"{w}: {c.most_common(1)[0][0]} x{c.most_common(1)[0][1]}" for w, c in sorted(errs.items())) or "no errors"


@step("WF010")
def identify_slow_steps(ctx):
    agg = defaultdict(list)
    for r in ctx.state["logs"]:
        agg[(r["workflow_id"], r["step_name"])].append(to_float(r["duration_ms"]) or 0)
    slow = {}
    for (wid, name), v in agg.items():
        avg = sum(v) / len(v)
        if wid not in slow or avg > slow[wid][1]:
            slow[wid] = (name, avg)
    ctx.state["slow"] = slow
    return "; ".join(f"{w}: '{n}' {int(a)}ms" for w, (n, a) in sorted(slow.items()))


@step("WF010")
def generate_recommendations(ctx):
    fthr = float(ctx.param("failure_threshold_pct", config.FAILURE_RATE_THRESHOLD_PCT))
    tthr = float(ctx.param("time_threshold_ms", config.AVG_EXEC_TIME_THRESHOLD_MS))
    ctx.decide(f"Flag workflow if failure rate > {fthr}% OR average run time > {tthr:.0f} ms")
    rows, recs = [], []
    for wid, p in sorted(ctx.state["per"].items(), key=lambda kv: -kv[1]["failure_rate"]):
        flags = []
        if p["failure_rate"] > fthr: flags.append("HIGH FAILURE")
        if p["avg_ms"] > tthr: flags.append("SLOW")
        rows.append({"workflow": wid, "runs": p["runs"], "failure_%": p["failure_rate"], "avg_ms": int(p["avg_ms"]), "flags": ", ".join(flags) or "ok"})
        err = ctx.state["errors"].get(wid)
        slow = ctx.state["slow"].get(wid)
        if "HIGH FAILURE" in flags and err:
            e, n = err.most_common(1)[0]
            recs.append(f"{wid}: failure rate {p['failure_rate']}% - most common error '{e}' ({n}x). Add retries/back-off and input validation around that step.")
        if "SLOW" in flags and slow:
            recs.append(f"{wid}: avg {int(p['avg_ms'])}ms - slowest step is '{slow[0]}' ({int(slow[1])}ms). Cache, batch or run it asynchronously.")
    ctx.output = {"summary": rows, "recommendations": recs, "thresholds": {"failure_pct": fthr, "avg_ms": tthr}}
    ctx.report = ("Workflow performance summary (sorted by failure rate):\n" + table(rows, ["workflow", "runs", "failure_%", "avg_ms", "flags"])
                  + "\n\n  Recommendations:\n" + ("\n".join(f"    {i}. {r}" for i, r in enumerate(recs, 1)) or "    none - all workflows within thresholds"))
    return f"{len(recs)} recommendation(s)"
