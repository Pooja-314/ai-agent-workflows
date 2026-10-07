"""WF005 Customer Order Status."""
import re

from agent.models import NeedsInput, StepError
from agent.registry import step, workflow_meta

ORDER_RE = re.compile(r"\bORD-\d+\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _extract(req):
    out = {}
    if m := ORDER_RE.search(req): out["order_id"] = m.group(0).upper()
    if m := EMAIL_RE.search(req): out["email"] = m.group(0).lower()
    return out


workflow_meta("WF005", keywords=["order", "status", "shipment", "tracking", "where is", "delivery", "customer"],
              param_keys=["order_id", "email"], extractor=_extract)


@step("WF005")
def validate_identifier(ctx):
    oid, email = ctx.params.get("order_id"), ctx.params.get("email")
    if oid and not ORDER_RE.fullmatch(str(oid)):
        raise NeedsInput(f"'{oid}' is not a valid order ID (expected ORD-1234). Please send another identifier.", ["order_id"])
    if email and not EMAIL_RE.fullmatch(str(email)):
        raise NeedsInput(f"'{email}' is not a valid email address.", ["email"])
    if not oid and not email:
        raise NeedsInput("Please provide an order ID (e.g. ORD-1001) or the customer email.", ["order_id", "email"])
    ctx.state["id"] = {"order_id": oid} if oid else {"email": email}
    return f"identifier OK: {ctx.state['id']}"


@step("WF005")
def search_order_data(ctx):
    orders = ctx.call_tool("order_api", **ctx.state["id"])
    if not orders:
        ident = next(iter(ctx.state["id"].values()))
        ctx.decide("No order found -> ask the user for another identifier (per workflow rule)")
        raise NeedsInput(f"No order found for '{ident}'. Please provide another identifier (order ID or customer email).",
                         ["order_id", "email"])
    ctx.state["orders"] = orders
    return f"{len(orders)} order(s) found"


@step("WF005")
def retrieve_order_status(ctx):
    return "; ".join(f"{o['order_id']}: {o['status']}" for o in ctx.state["orders"])


@step("WF005", optional=True)      # an order status is still useful when the carrier is down
def retrieve_shipment_information(ctx):
    lines = []
    for o in ctx.state["orders"]:
        o["shipment"] = None
        if not o.get("shipment_id"):
            continue
        try:
            o["shipment"] = ctx.call_tool("shipment_api", shipment_id=o["shipment_id"])
            lines.append(f"{o['order_id']}: {o['shipment']['status']}")
        except Exception as e:
            o["shipment_error"] = str(e)
            ctx.warn(f"{o['order_id']}: shipment lookup failed ({e})")
    if not lines and any(o.get("shipment_error") for o in ctx.state["orders"]):
        raise StepError("shipment lookup failed for all orders")
    return "; ".join(lines) or "no shipments yet"


@step("WF005")
def summarize_current_status(ctx):
    out = []
    for o in ctx.state["orders"]:
        items = ", ".join(f"{i['qty']} x {i['name']}" for i in o["items"])
        sh = o.get("shipment")
        if sh:
            ship = f"{sh['carrier']} - {sh['status']} (tracking {sh['tracking']}, ETA {sh['eta']}, last seen: {sh['last_location']})"
        elif o["status"] == "Cancelled":
            ship = "order cancelled - no shipment"
        elif o.get("shipment_id"):
            ship = "shipment details temporarily unavailable from the carrier; please try again later"
        else:
            ship = "not shipped yet - no tracking available"
        out.append(f"  {o['order_id']} (placed {o['placed']})\n    Status:   {o['status']}\n    Items:    {items}\n    Shipment: {ship}")
    ctx.output = {"orders": [{k: v for k, v in o.items()} for o in ctx.state["orders"]]}
    ctx.report = "\n\n".join(out)
    return f"summarised {len(out)} order(s)"
