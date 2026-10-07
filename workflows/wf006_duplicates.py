"""WF006 Duplicate Product Detection."""
from itertools import combinations

from agent import config
from agent.models import StepError
from agent.registry import step, workflow_meta
from agent.tools.similarity import normalize_sku, normalize_text
from ._common import to_float

workflow_meta("WF006", keywords=["duplicate", "duplicates", "likely duplicate", "catalog", "same product", "similar"],
              param_keys=["catalog_csv"])


@step("WF006")
def load_products(ctx):
    d = ctx.call_tool("csv_reader", path=ctx.param("catalog_csv", "data/samples/catalog.csv"))
    if len(d["rows"]) < 2:
        raise StepError("Need at least 2 products to look for duplicates")
    ctx.state["rows"] = d["rows"]
    return f"{len(d['rows'])} products loaded"


@step("WF006")
def normalize_names_and_skus(ctx):
    for i, r in enumerate(ctx.state["rows"]):
        r["_i"] = i
        r["_sku"] = normalize_sku(r.get("sku", ""))
        brand = (r.get("brand") or "").lower()
        r["_name"] = normalize_text(r.get("name", ""), drop={brand} if brand else None)
    return "SKUs upper-cased/stripped of punctuation; names lower-cased, units/synonyms unified, brand removed"


@step("WF006")
def compare_identifiers(ctx):
    pairs = {}
    for a, b in combinations(ctx.state["rows"], 2):
        if a["_sku"] and a["_sku"] == b["_sku"]:
            pairs[(a["_i"], b["_i"])] = {"level": "definite", "score": 1.0, "matched": ["sku"]}
    ctx.state["pairs"] = pairs
    ctx.decide("Exact (normalised) SKU match => definite duplicate")
    return f"{len(pairs)} exact SKU match(es)"


@step("WF006")
def compare_product_attributes(ctx):
    pairs, thr = ctx.state["pairs"], config.DUPLICATE_POSSIBLE_THRESHOLD
    variants = 0
    with ctx.batch("text_similarity") as sim:
      for a, b in combinations(ctx.state["rows"], 2):
          key = (a["_i"], b["_i"])
          if key in pairs:
              continue
          name_sim = sim(a=a["_name"], b=b["_name"]) if a["_name"] and b["_name"] else 0
          attrs = ["brand", "category", "color", "size"]
          same = [f for f in attrs if (a.get(f) or "").strip().lower() == (b.get(f) or "").strip().lower() and a.get(f)]
          conflicting = [f for f in ("color", "size") if a.get(f) and b.get(f) and f not in same]
          if conflicting and name_sim >= 0.6:
              variants += 1                        # same style, different colour/size = variant, not duplicate
              continue
          pa, pb = to_float(a.get("price")), to_float(b.get("price"))
          price_close = pa and pb and abs(pa - pb) / max(pa, pb) <= 0.1
          score = round(0.6 * name_sim + 0.4 * (len(same) + (1 if price_close else 0)) / (len(attrs) + 1), 3)
          if score >= thr:
              pairs[key] = {"level": "possible", "score": score,
                            "matched": [f"name~{name_sim}"] + same + (["price~"] if price_close else [])}
    ctx.state["variants"] = variants
    ctx.decide(f"High attribute similarity (score >= {thr}) => possible duplicate; "
               "same name but different colour/size => treated as variant, not duplicate")
    return f"{len(pairs)} candidate pair(s); {variants} variant pair(s) ignored"


@step("WF006")
def group_likely_duplicates(ctx):
    parent = list(range(len(ctx.state["rows"])))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for (i, j) in ctx.state["pairs"]:
        parent[find(i)] = find(j)
    groups = {}
    for r in ctx.state["rows"]:
        groups.setdefault(find(r["_i"]), []).append(r)
    ctx.state["groups"] = [g for g in groups.values() if len(g) > 1]
    return f"{len(ctx.state['groups'])} duplicate group(s)"


@step("WF006")
def assign_confidence(ctx):
    out, lines = [], []
    for n, g in enumerate(ctx.state["groups"], start=1):
        idx = {r["_i"] for r in g}
        ps = [p for k, p in ctx.state["pairs"].items() if k[0] in idx and k[1] in idx]
        definite = any(p["level"] == "definite" for p in ps)
        conf = 1.0 if definite else round(min(p["score"] for p in ps), 2)
        label = "DEFINITE" if definite else ("HIGH" if conf >= 0.9 else "MEDIUM")
        matching = sorted({m for p in ps for m in p["matched"]})
        out.append({"group": n, "confidence": label, "score": conf, "matching_fields": matching,
                    "products": [{"sku": r["sku"], "name": r["name"]} for r in g]})
        lines.append(f"  Group {n} - {label} ({conf})  matched on: {', '.join(matching)}\n" +
                     "\n".join(f"      - {r['sku']}: {r['name']} [{r.get('color','')}, {r.get('size','')}] {r.get('price','')}" for r in g)
                     + "\n" + "\n".join(f"      pair {g_[0]}~{g_[1]}: {p['level']} ({p['score']})" for g_, p in [((ctx.state['rows'][k[0]]['sku'], ctx.state['rows'][k[1]]['sku']), p) for k, p in ctx.state['pairs'].items() if k[0] in idx and k[1] in idx]))
    ctx.output = {"groups": out, "variant_pairs_ignored": ctx.state["variants"]}
    ctx.report = (f"{len(out)} duplicate group(s) found in {len(ctx.state['rows'])} products:\n\n" + "\n\n".join(lines)
                  + f"\n\n  ({ctx.state['variants']} colour/size variant pair(s) were deliberately not flagged)")
    return f"{len(out)} group(s) scored"
