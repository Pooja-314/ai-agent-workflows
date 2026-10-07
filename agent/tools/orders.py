"""Simulated order database + shipment carrier API.
The carrier API is deliberately flaky for one shipment (transient failure on the
first call) and missing for another (permanent failure) so the engine's retry and
optional-step handling can be demonstrated."""
from __future__ import annotations

import json

from ..config import SAMPLES
from ..models import ToolError
from . import tool

_calls: dict[str, int] = {}


def _db() -> dict:
    return json.loads((SAMPLES / "orders.json").read_text(encoding="utf-8"))


@tool("order_api")
def order_api(order_id: str | None = None, email: str | None = None) -> list[dict]:
    db = _db()["orders"]
    if order_id:
        return [o for o in db if o["order_id"].upper() == order_id.upper()]
    if email:
        return [o for o in db if o["customer_email"].lower() == email.lower()]
    raise ToolError("order_api needs order_id or email")


@tool("shipment_api")
def shipment_api(shipment_id: str) -> dict:
    n = _calls[shipment_id] = _calls.get(shipment_id, 0) + 1
    if shipment_id == "SH-4004" and n == 1:
        raise ToolError("carrier API timeout", transient=True)
    shipments = _db()["shipments"]
    if shipment_id not in shipments:
        raise ToolError(f"carrier has no record of shipment {shipment_id}")
    return shipments[shipment_id]
