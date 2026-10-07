"""WF002 Product Price Validation."""
import re

from agent import config
from agent.models import StepError
from agent.registry import step, workflow_meta
from agent.tools.similarity import normalize_sku
from ._common import table, to_float


def _extract(req):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", req)
    return {"tolerance_pct": float(m.group(1))} if m else {}


workflow_meta("WF002", keywords=["price", "validate", "vendor price", "differs", "difference", "percentage", "sku"],
              param_keys=["products_csv", "vendor_csv", "tolerance_pct"], extractor=_extract)


@step("WF002")
def load_product_prices(ctx):
    d = ctx.call_tool("csv_reader", path=ctx.param("products_csv", "data/samples/products.csv"))
    ctx.state["internal"] = d["rows"]
    d2 = ctx.call_tool("csv_reader", path=ctx.param("vendor_csv", "data/samples/vendor_prices.csv"))
    ctx.state["vendor"] = d2["rows"]
    if not d["rows"] or not d2["rows"]:
        raise StepError("Product file or vendor price list is empty")
    return f"{len(d['rows'])} internal products, {len(d2['rows'])} vendor prices"


@step("WF002")
def match_by_sku(ctx):
    vendor = {normalize_sku(r.get("sku", "")): r for r in ctx.state["vendor"]}
    matched, unmatched_internal = [], []
    seen = set()
    for r in ctx.state["internal"]:
        k = normalize_sku(r.get("sku", ""))
        if k in vendor:
            matched.append((r, vendor[k])); seen.add(k)
        else:
            unmatched_internal.append(r)
    ctx.state["matched"] = matched
    ctx.state["only_internal"] = unmatched_internal
    ctx.state["only_vendor"] = [v for k, v in vendor.items() if k not in seen]
    ctx.decide("SKUs normalised (trim, upper-case, punctuation removed) before matching")
    return (f"{len(matched)} matched; {len(unmatched_internal)} only internal; "
            f"{len(ctx.state['only_vendor'])} only in vendor list")


@step("WF002")
def compare_prices(ctx):
    rows = []
    for i, v in ctx.state["matched"]:
        ip, vp = to_float(i.get("internal_price")), to_float(v.get("vendor_price"))
        row = {"sku": i["sku"], "product": i.get("product_name", ""), "internal": ip, "vendor": vp, "diff_pct": None}
        if ip is None or vp is None:
            row["note"] = "non-numeric price"
        rows.append(row)
    ctx.state["compared"] = rows
    return f"compared {len(rows)} price pairs"


@step("WF002")
def calculate_percentage_difference(ctx):
    for r in ctx.state["compared"]:
        if r["internal"] is None or r["vendor"] is None:
            continue
        try:
            r["diff_pct"] = ctx.call_tool("calculator", op="pct_diff", base=r["internal"], other=r["vendor"])
        except Exception as e:
            r["note"] = str(e)
    ctx.decide("diff % = (vendor - internal) / internal x 100, rounded to 2 dp")
    return "percentage differences calculated"


@step("WF002")
def flag_exceptions(ctx):
    tol = float(ctx.param("tolerance_pct", config.PRICE_TOLERANCE_PCT))
    ctx.decide(f"Flag when |difference| > {tol}% (exactly {tol}% is NOT flagged); unpriceable rows are flagged for review")
    flagged = []
    for r in ctx.state["compared"]:
        if r["diff_pct"] is None:
            r["flag"] = "REVIEW"; flagged.append(r)
        elif abs(r["diff_pct"]) > tol:
            r["flag"] = "EXCEPTION"; flagged.append(r)
        else:
            r["flag"] = "ok"
        r["diff_pct_str"] = "n/a" if r["diff_pct"] is None else f"{r['diff_pct']:+.2f}%"
    ctx.output = {"tolerance_pct": tol, "matched": len(ctx.state["compared"]), "exceptions": flagged,
                  "unmatched_internal": [r["sku"] for r in ctx.state["only_internal"]],
                  "unmatched_vendor": [r["sku"] for r in ctx.state["only_vendor"]]}
    path = ctx.save_csv("price_validation.csv", ctx.state["compared"],
                        ["sku", "product", "internal", "vendor", "diff_pct", "flag"])
    ctx.report = (f"Price validation report (tolerance {tol}%)\n"
                  f"  Matched products: {len(ctx.state['compared'])} | Exceptions: {len(flagged)}\n\n"
                  + table(ctx.state["compared"], ["sku", "product", "internal", "vendor", "diff_pct_str", "flag"])
                  + f"\n\n  Only in our catalogue (no vendor price): {', '.join(ctx.output['unmatched_internal']) or 'none'}"
                  + f"\n  Only in vendor list: {', '.join(ctx.output['unmatched_vendor']) or 'none'}"
                  + f"\n  Saved: {path}")
    return f"{len(flagged)} exception(s)"
