"""WF009 Employee Task Assignment."""
import re
from datetime import date

from agent.models import Escalation
from agent.registry import step, workflow_meta
from ._common import table

ROLES = ["developer", "designer", "analyst", "manager", "tester"]

workflow_meta("WF009", keywords=["assign", "task", "employee", "developer", "best available", "urgent", "workload", "delegate"],
              param_keys=["task", "role", "priority", "deadline", "estimated_hours"])


@step("WF009")
def understand_task_requirements(ctx):
    base = ctx.call_tool("json_reader", path="data/samples/task.json")
    staff = ctx.call_tool("json_reader", path="data/samples/employees.json")
    ctx.state["staff"] = staff["employees"]

    vocab = set(staff["skill_catalog"]) | {
        s for e in staff["employees"] for s in e["skills"]
    }

    explicit = ctx.params.get("task")

    if explicit:
        desc = explicit
        text = f"{ctx.request} {desc}".lower()
    else:
        desc = ctx.request
        text = ctx.request.lower()

        if not ctx.request.strip():
            desc = base["description"]
            text = desc.lower()
            ctx.decide(
                "Request contained no task details -> using sample task from data/samples/task.json"
            )

    skills = sorted(
        s for s in vocab
        if re.search(rf"\b{re.escape(s.lower())}\b", text)
    )

    # Normalize common skill aliases
    skill_aliases = {
        "machine learning": ["ml"],
        "ml": ["machine learning"],
    }

    for canonical, aliases in skill_aliases.items():
        if canonical in vocab and any(
            re.search(rf"\b{re.escape(alias)}\b", text)
            for alias in aliases
        ):
            if canonical not in skills:
                skills.append(canonical)

    skills = sorted(set(skills))

    role = ctx.params.get("role") or next((r for r in ROLES if r in text), None)

    if role:
        role = role.lower().strip()

        if role in ("python developer", "python engineer", "software developer"):
            role = "developer"

    urgent = bool(re.search(r"\b(urgent|asap|critical|immediately)\b", text))

    hours_value = ctx.params.get("estimated_hours")

    if hours_value in (None, ""):
        hours_match = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b",
            text,
            re.IGNORECASE
        )
        if hours_match:
            hours_value = hours_match.group(1)

    hours = float(hours_value or base["estimated_hours"])

    ctx.state["task"] = {
        "description": desc,
        "skills": skills,
        "role": role,
        "priority": ctx.params.get("priority") or ("High" if urgent else base["priority"].title()),
        "deadline": ctx.params.get("deadline") or base["deadline"],
        "hours": hours
    }

    t = ctx.state["task"]

    return f"role={t['role']}, skills={t['skills'] or 'none detected'}, priority={t['priority']}, est={t['hours']}h"


@step("WF009")
def compare_employee_skills(ctx):
    t = ctx.state["task"]
    for e in ctx.state["staff"]:
        e["role_ok"] = (not t["role"]) or e["role"] == t["role"]
        e["skill_cov"] = (sum(s in e["skills"] for s in t["skills"]) / len(t["skills"])) if t["skills"] else 1.0
    ctx.decide("Skill coverage = share of required skills the employee has (100% if the task names no specific skill); wrong role is excluded")
    return ", ".join(f"{e['name']}: {int(e['skill_cov'] * 100)}%" + ("" if e["role_ok"] else " (role mismatch)") for e in ctx.state["staff"])


@step("WF009")
def check_current_workload(ctx):
    for e in ctx.state["staff"]:
        e["spare"] = e["capacity_hours"] - e["workload_hours"]
        e["util"] = round(e["workload_hours"] / e["capacity_hours"] * 100)
    return ", ".join(f"{e['name']}: {e['spare']}h free" for e in ctx.state["staff"])


@step("WF009")
def rank_candidates(ctx):
    t = ctx.state["task"]
    cands = []
    for e in ctx.state["staff"]:
        why = []
        if not e["role_ok"]: why.append("role mismatch")
        if e["skill_cov"] < 1.0: why.append("missing skills")        # prefer people with the required skills
        if e["spare"] < t["hours"]: why.append(f"only {e['spare']}h free (<{t['hours']:.0f}h needed)")
        score = round(0.6 * e["skill_cov"] + 0.4 * max(e["spare"], 0) / e["capacity_hours"], 3)
        cands.append({**e, "score": score, "excluded": why})
    ctx.state["ranked"] = sorted(cands, key=lambda c: -c["score"])
    ctx.decide("Score = 0.6 x skill coverage + 0.4 x (spare hours / capacity). Candidates need the role, all required skills and enough spare hours.")
    return "; ".join(f"{c['name']}={c['score']}" + (f" [excluded: {', '.join(c['excluded'])}]" if c["excluded"] else "") for c in ctx.state["ranked"])


@step("WF009")
def select_employee(ctx):
    ok = [c for c in ctx.state["ranked"] if not c["excluded"]]
    if not ok:
        t = ctx.state["task"]
        ctx.decide("No suitable employee -> escalate to manager (workflow rule)")
        raise Escalation(f"No suitable employee for this task (role={t['role']}, skills={t['skills']}, needs {t['hours']:.0f}h). "
                         f"Escalating to the manager for a decision (hire/contract, re-prioritise, or training).")
    ctx.state["chosen"] = ok[0]
    return f"selected {ok[0]['name']}"


@step("WF009")
def generate_assignment_summary(ctx):
    c, t = ctx.state["chosen"], ctx.state["task"]
    runner = [x for x in ctx.state["ranked"] if not x["excluded"]][1:2]
    reason = (f"has {', '.join(t['skills']) if t['skills'] else 'the required role'}, {c['spare']}h free of {c['capacity_hours']}h capacity "
              f"({c['util']}% utilised)" + (f"; runner-up: {runner[0]['name']}" if runner else ""))
    ctx.output = {"assignee": c["name"], "reasoning": reason, "priority": t["priority"], "deadline": t["deadline"], "task": t["description"]}
    ctx.report = (f"Recommended assignee: {c['name']} ({c['role']})\n  Reasoning: {reason}\n  Priority: {t['priority']}\n"
                  f"  Deadline: {t['deadline']}\n  Task: {t['description']}\n\n  Candidate ranking:\n"
                  + table([{"name": x["name"], "score": x["score"], "free_h": x["spare"], "note": ", ".join(x["excluded"]) or "eligible"} for x in ctx.state["ranked"]],
                          ["name", "score", "free_h", "note"]))
    return f"assigned to {c['name']}"
