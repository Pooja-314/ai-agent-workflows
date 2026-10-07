"""WF008 SEO Keyword Classification."""
import json
import re

from agent.registry import step, workflow_meta
from agent.models import StepError
from agent.tools.similarity import normalize_text
from ._common import table, to_float

INTENTS = ["informational", "commercial", "transactional", "navigational"]

workflow_meta("WF008", keywords=["seo", "keyword", "keywords", "classify", "search intent", "map pages", "intent"],
              param_keys=["keywords_csv"],
              extractor=lambda req: ({"keywords_csv": m.group(0)} if (m := re.search(r"[\w/\\.\-]+\.csv", req, re.I)) else {}))


@step("WF008")
def read_keywords(ctx):
    d = ctx.call_tool("csv_reader", path=ctx.param("keywords_csv", "data/samples/keywords.csv"))
    col = "keyword" if "keyword" in d["headers"] else (d["headers"][0] if d["headers"] else None)
    if not col or not d["rows"]:
        raise StepError("Keyword file is empty")
    ctx.state["rows"] = [{"keyword": (r.get(col) or "").strip(), "volume": to_float(r.get("monthly_volume")) or 0} for r in d["rows"] if (r.get(col) or "").strip()]
    ctx.state["site"] = ctx.call_tool("json_reader", path="data/samples/site_pages.json")
    return f"{len(ctx.state['rows'])} keywords read"


@step("WF008")
def remove_duplicates(ctx):
    seen, out = {}, []
    for r in ctx.state["rows"]:
        k = " ".join(r["keyword"].lower().split())
        if k in seen:
            seen[k]["volume"] = max(seen[k]["volume"], r["volume"]); continue
        r = {**r, "keyword": k}; seen[k] = r; out.append(r)
    removed = len(ctx.state["rows"]) - len(out)
    ctx.state["rows"] = out
    return f"removed {removed} duplicate(s); {len(out)} unique"


def _rule_intent(kw: str, brands: list[str]) -> str:
    k = kw.lower()
    if any(b in k for b in brands) or re.search(r"\b(login|official|website|store locator)\b", k):
        return "navigational"
    if re.search(r"\b(buy|price|prices|order|discount|coupon|deal|cheap|near me|shop|purchase|sale)\b", k):
        return "transactional"
    if re.search(r"\b(best|top|review|reviews|vs|versus|compare|comparison|alternative|alternatives)\b", k):
        return "commercial"
    return "informational"


@step("WF008")
def classify_search_intent(ctx):
    brands = ctx.state["site"]["brands"]
    kws = [r["keyword"] for r in ctx.state["rows"]]
    system = ("Classify each SEO keyword's search intent as exactly one of: informational, commercial, transactional, navigational. "
              'Return JSON {"intents": {"<keyword>": "<intent>"}} covering every keyword.')

    def _validate(d):
        if not all(d["intents"].get(k) in INTENTS for k in kws):
            raise ValueError("LLM returned missing/invalid intents")

    data, src = ctx.llm_json(system, json.dumps(kws), offline=lambda: {"intents": {k: _rule_intent(k, brands) for k in kws}}, validate=_validate)
    for r in ctx.state["rows"]:
        r["intent"] = data["intents"][r["keyword"]]
    ctx.state["source"] = src
    ctx.decide("Intent is one of informational / commercial / transactional / navigational (LLM output validated against this set)")
    counts = {i: sum(r["intent"] == i for r in ctx.state["rows"]) for i in INTENTS}
    return f"{counts} via {src}"


@step("WF008")
def map_keywords_to_categories(ctx):
    cats = ctx.state["site"]["categories"]
    unmapped = 0
    for r in ctx.state["rows"]:
        toks = set(normalize_text(r["keyword"]).split()) | set(normalize_text(r["keyword"]).replace(" ", "").split())
        best, score = None, 0
        for name, c in cats.items():
            s = len(toks & set(c["terms"])) + sum(1 for t in c["terms"] if t in r["keyword"].replace("-", ""))
            if s > score: best, score = name, s
        r["category"] = best or "Uncategorized"
        if not best:
            unmapped += 1
        r["page"] = (cats[best]["page"] if best else "-")
    return f"{unmapped} keyword(s) could not be mapped"


@step("WF008")
def identify_high_priority_keywords(ctx):
    for r in ctx.state["rows"]:
        pts = {"transactional": 3, "commercial": 2, "informational": 1, "navigational": 0}[r["intent"]]
        pts += 2 if r["volume"] >= 1000 else 1 if r["volume"] >= 500 else 0
        if r["category"] == "Uncategorized": pts -= 1
        r["priority"] = "High" if pts >= 4 else "Medium" if pts >= 2 else "Low"
        if r["intent"] == "informational": r["page"] = "/blog/" + re.sub(r"[^a-z0-9]+", "-", r["keyword"]).strip("-")
        if r["intent"] == "navigational": r["page"] = "/ (homepage / brand page)"
    ctx.decide("Priority points: intent (transactional 3, commercial 2, informational 1, navigational 0) + volume (>=1000: 2, >=500: 1); >=4 High, >=2 Medium. Informational keywords target blog pages.")
    return f"{sum(r['priority'] == 'High' for r in ctx.state['rows'])} high-priority"


@step("WF008")
def export_results(ctx):
    rows = sorted(ctx.state["rows"], key=lambda r: ({"High": 0, "Medium": 1, "Low": 2}[r["priority"]], -r["volume"]))
    out = [{"keyword": r["keyword"], "volume": int(r["volume"]), "intent": r["intent"], "category": r["category"],
            "priority": r["priority"], "target_page": r["page"]} for r in rows]
    path = ctx.save_csv("keyword_report.csv", out)
    ctx.output = {"keywords": out, "classified_by": ctx.state["source"]}
    ctx.report = (f"Keyword report ({len(out)} unique keywords, classified by {ctx.state['source']}):\n"
                  + table(out, ["keyword", "volume", "intent", "category", "priority", "target_page"]) + f"\n  Saved: {path}")
    return f"exported to {path}"
