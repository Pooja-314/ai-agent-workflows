"""WF007 Marketing Campaign Brief."""
import json
import re
from datetime import date, datetime, timedelta

from agent.models import NeedsInput
from agent.registry import step, workflow_meta

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(?:to|until|-|through)\s*(\d{4}-\d{2}-\d{2})", re.I)


def _extract(req):
    out = {}
    if m := DATE_RE.search(req): out["dates"] = f"{m.group(1)} to {m.group(2)}"
    if m := re.search(r"goal\s*[:=]\s*([^;\n]+)", req, re.I): out["goal"] = m.group(1).strip()
    if m := re.search(r"audience\s*[:=]\s*([^;\n]+)", req, re.I): out["audience"] = m.group(1).strip()
    if m := re.search(r"promotion\s*[:=]\s*([^;\n]+)", req, re.I): out["promotion"] = m.group(1).strip()
    return out


workflow_meta("WF007", keywords=["campaign", "brief", "marketing", "promotion", "launch", "collection", "channels"],
              param_keys=["goal", "dates", "audience", "promotion", "products_csv"], extractor=_extract)


@step("WF007")
def validate_inputs(ctx):
    missing = [k for k in ("goal", "dates") if not ctx.params.get(k)]
    if missing:
        ctx.decide("Rule: goal and dates are mandatory -> ask before generating anything")
        raise NeedsInput("Before I can write the brief I need: "
                         + " and ".join({"goal": "the campaign goal", "dates": "the campaign dates (e.g. 2026-11-01 to 2026-11-30)"}[m] for m in missing) + ".",
                         missing)
    m = DATE_RE.search(str(ctx.params["dates"]))
    if not m:
        raise NeedsInput("I couldn't read the dates. Please use 'YYYY-MM-DD to YYYY-MM-DD'.", ["dates"])
    start, end = (datetime.strptime(x, "%Y-%m-%d").date() for x in m.groups())
    if end < start:
        raise NeedsInput("The end date is before the start date. Please correct the dates.", ["dates"])
    ctx.state.update(start=start, end=end)
    for k in ("audience", "promotion"):
        if not ctx.params.get(k):
            ctx.warn(f"{k} not provided - marked 'TBD' in the brief")
    return f"goal and dates OK ({start} -> {end})"


@step("WF007")
def identify_campaign_objective(ctx):
    goal = str(ctx.params["goal"]).lower()
    rules = [("sales", ["sale", "revenue", "convert", "purchase", "launch"]), ("awareness", ["awareness", "reach", "brand", "visibility"]),
             ("retention", ["retention", "loyal", "repeat", "re-engage"]), ("lead generation", ["lead", "signup", "sign-up", "subscribe"])]
    obj = next((name for name, kws in rules if any(k in goal for k in kws)), "general promotion")
    ctx.state["objective"] = obj
    return f"objective: {obj}"


@step("WF007")
def summarize_products(ctx):

    path = ctx.param("products_csv", "data/samples/new_collection.csv")

    # Normalize parameter value to a single path string
    if isinstance(path, (list, tuple)):
        path = path[0] if path else "data/samples/new_collection.csv"

    rows = ctx.call_tool("csv_reader", path=path)["rows"]

    ctx.state["products"] = rows

    return f"{len(rows)} products: " + ", ".join(r["name"] for r in rows)


def _offline(ctx):
    p, obj = ctx.params, ctx.state["objective"]
    names = [r["name"] for r in ctx.state["products"]]
    promo = p.get("promotion") or "TBD"
    return {"messaging": {"headline": f"Meet the new collection: {', '.join(names[:2])} and more",
                          "key_messages": [f"Fresh arrivals: {', '.join(names)}", f"Offer: {promo}", f"Built around the goal: {p['goal']}"],
                          "call_to_action": "Shop the collection now"},
            "channels": [{"channel": "Email", "why": "owned audience, best for launch announcements and offers"},
                         {"channel": "Instagram / Reels", "why": "visual product discovery"},
                         {"channel": "Paid search & shopping ads", "why": "capture high-intent buyers" if obj == "sales" else "extend reach"},
                         {"channel": "Website banner + landing page", "why": "single destination for all traffic"}]}


@step("WF007")
def create_messaging(ctx):
    system = ("You are a marketing strategist. Use only the facts supplied; write 'TBD' for anything unknown. "
              "Return JSON: {messaging:{headline,key_messages[],call_to_action}, channels:[{channel,why}]}")
    user = json.dumps({"goal": ctx.params["goal"], "objective": ctx.state["objective"], "audience": ctx.params.get("audience") or "TBD",
                       "promotion": ctx.params.get("promotion") or "TBD", "products": ctx.state["products"]})
    data, src = ctx.llm_json(system, user, offline=lambda: _offline(ctx))
    ctx.state.update(messaging=data["messaging"], channels=data["channels"], source=src)
    return f"messaging drafted ({src})"


@step("WF007")
def create_channel_recommendations(ctx):
    return ", ".join(c["channel"] for c in ctx.state["channels"])


@step("WF007")
def create_campaign_checklist(ctx):
    s, e = ctx.state["start"], ctx.state["end"]
    timeline = [("Pre-launch: assets, copy, tracking set-up", s - timedelta(days=14), s - timedelta(days=1)),
                ("Launch & active promotion", s, e), ("Wrap-up: reporting & learnings", e + timedelta(days=1), e + timedelta(days=7))]
    checklist = ["Confirm product availability / stock for the campaign period", "Finalise creative and copy (approved by brand owner)",
                 "Set up UTM tracking and conversion goals", "Schedule email + social posts", "Configure promotion code / pricing rules",
                 "QA landing page on mobile and desktop", "Daily performance check during campaign", "Post-campaign report vs. goal"]
    p = ctx.params
    brief = {"objective": ctx.state["objective"], "goal": p["goal"], "audience": p.get("audience") or "TBD",
             "promotion": p.get("promotion") or "TBD", "dates": f"{s} to {e}", "products": ctx.state["products"],
             "messaging": ctx.state["messaging"], "channels": ctx.state["channels"],
             "timeline": [{"phase": a, "from": str(b), "to": str(c)} for a, b, c in timeline], "checklist": checklist,
             "generated_by": ctx.state["source"]}
    ctx.output = brief
    m = brief["messaging"]
    ctx.report = (f"CAMPAIGN BRIEF\n  Objective: {brief['objective']}  (goal: {brief['goal']})\n  Audience: {brief['audience']}\n"
                  f"  Promotion: {brief['promotion']}\n  Dates: {brief['dates']}\n\n  Products:\n"
                  + "\n".join(f"    - {r['name']} ({r.get('category','')}, {r.get('price','')})" for r in brief["products"])
                  + f"\n\n  Messaging:\n    Headline: {m['headline']}\n" + "\n".join(f"    * {k}" for k in m["key_messages"])
                  + f"\n    CTA: {m['call_to_action']}\n\n  Channels:\n" + "\n".join(f"    - {c['channel']}: {c['why']}" for c in brief["channels"])
                  + "\n\n  Timeline:\n" + "\n".join(f"    - {t['phase']}: {t['from']} -> {t['to']}" for t in brief["timeline"])
                  + "\n\n  Checklist:\n" + "\n".join(f"    [ ] {c}" for c in checklist) + f"\n\n  Generated by: {brief['generated_by']}")
    return "brief assembled"
