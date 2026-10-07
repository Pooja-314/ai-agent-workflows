"""WF001 Inventory Restock Check."""
import re

from agent.models import StepError
from agent.registry import step, workflow_meta
from ._common import table, to_float


def _extract(req):
    m = re.search(r"threshold\D{0,10}(\d+)", req, re.I)
    return {"threshold": int(m.group(1))} if m else {}


workflow_meta("WF001", keywords=["inventory", "stock", "restock", "reorder", "low stock", "replenish"],
              param_keys=["inventory_csv", "threshold"], extractor=_extract)


@step("WF001")
def load_inventory(ctx):
    path = ctx.param("inventory_csv", "data/samples/inventory.csv")
    data = ctx.call_tool("csv_reader", path=path)
    if not data["rows"]:
        raise StepError("Inventory file is empty")
    ctx.state["rows"] = data["rows"]
    return f"loaded {len(data['rows'])} products from {path}"


@step("WF001")
def compare_with_threshold(ctx):
    override = ctx.params.get("threshold")
    ctx.state["checked"], ctx.state["skipped"] = [], []
    for r in ctx.state["rows"]:
        cur = to_float(r.get("current_stock"))
        mn = to_float(override if override is not None else r.get("minimum_stock"))
        if cur is None or mn is None:
            ctx.state["skipped"].append(r)
            ctx.warn(f"{r.get('sku')}: unreadable stock/threshold (current={r.get('current_stock')!r}) - skipped")
            continue
        ctx.state["checked"].append({**r, "_cur": cur, "_min": mn, "_max": to_float(r.get("max_stock"))})
    src = f"global threshold {override}" if override is not None else "per-product minimum_stock"
    ctx.decide(f"Rule: current_stock < minimum_stock => restock (using {src})")
    return f"compared {len(ctx.state['checked'])} products, skipped {len(ctx.state['skipped'])} invalid"


@step("WF001")
def identify_low_stock(ctx):
    low = [r for r in ctx.state["checked"] if r["_cur"] < r["_min"]]
    ctx.state["low"] = low
    return f"{len(low)} product(s) below minimum"


@step("WF001")
def calculate_reorder_quantity(ctx):
    for r in ctx.state["low"]:
        r["reorder_qty"] = ctx.call_tool("calculator", op="reorder_qty", current=r["_cur"],
                                         minimum=r["_min"], maximum=r["_max"])
    ctx.decide("Reorder qty = (max_stock, or 2 x minimum if no max) - current_stock")
    return "reorder quantities calculated"


@step("WF001")
def generate_restock_list(ctx):
    rows = [{"sku": r["sku"], "product": r["product_name"], "current": int(r["_cur"]),
             "minimum": int(r["_min"]), "reorder_qty": r["reorder_qty"]} for r in ctx.state["low"]]
    rows.sort(key=lambda r: r["current"] - r["minimum"])
    ctx.output = {"restock_list": rows, "checked": len(ctx.state["checked"])}
    path = ctx.save_csv("restock_list.csv", rows)
    ctx.report = (f"Products requiring restocking ({len(rows)} of {len(ctx.state['checked'])} checked):\n"
                  + table(rows, ["sku", "product", "current", "minimum", "reorder_qty"])
                  + f"\n  Saved: {path}")
    return f"restock list with {len(rows)} product(s)"
