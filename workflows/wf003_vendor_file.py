"""WF003 Vendor File Processing."""
from agent.models import StepError
from agent.registry import step, workflow_meta
from ._common import snake, table, to_float

ALIASES = {
    "sku": {"sku", "item_code", "product_code", "vendor_sku", "part_number", "item_number", "code"},
    "product_name": {"product_name", "name", "product_title", "title", "item_name", "product", "description"},
    "price": {"price", "unit_price", "cost", "unit_cost", "vendor_price"},
    "quantity": {"qty", "quantity", "qty_on_hand", "stock", "on_hand"},
    "color": {"color", "colour"},
}
REQUIRED = ["sku", "product_name"]

workflow_meta("WF003", keywords=["vendor", "file", "spreadsheet", "invalid rows", "process", "clean", "xlsx", "csv"],
              param_keys=["vendor_file"],
              extractor=lambda req: ({"vendor_file": m.group(0)} if (m := __import__("re").search(r"[\w/\\.\-]+\.(?:csv|xlsx)", req, 2)) else {}))


@step("WF003")
def read_file(ctx):
    path = ctx.param("vendor_file", "data/samples/vendor_file.csv")
    d = ctx.call_tool("excel_csv_parser", path=path)
    if not d["rows"]:
        raise StepError("Vendor file has no data rows")
    ctx.state.update(raw=d["rows"], headers=d["headers"], path=path)
    return f"read {len(d['rows'])} rows, {len(d['headers'])} columns from {path}"


@step("WF003")
def detect_columns(ctx):
    mapping = {}
    for h in ctx.state["headers"]:
        key = snake(h)
        for canon, names in ALIASES.items():
            if key in names and canon not in mapping.values():
                mapping[h] = canon; break
    ctx.state["mapping"] = mapping
    missing = [c for c in REQUIRED if c not in mapping.values()]
    if missing:
        raise StepError(f"Could not detect required column(s): {', '.join(missing)}. Headers found: {ctx.state['headers']}")
    return "; ".join(f"'{h}' -> {c}" for h, c in mapping.items())


@step("WF003")
def normalize_column_names(ctx):
    rows = []
    for r in ctx.state["raw"]:
        n = {canon: (r.get(h) or "").strip() for h, canon in ctx.state["mapping"].items()}
        extras = {snake(h): (r.get(h) or "").strip() for h in ctx.state["headers"] if h not in ctx.state["mapping"]}
        rows.append({**n, **extras})
    ctx.state["rows"] = rows
    return "headers renamed to canonical names; values trimmed"


@step("WF003")
def validate_required_fields(ctx):
    ctx.decide("Rule: rows missing SKU or product name are invalid; fully blank rows are dropped silently")
    valid, invalid, seen = [], [], set()
    for i, r in enumerate(ctx.state["rows"], start=2):          # +2: header is row 1
        if not any(v for v in r.values()):
            continue
        reasons = [f"missing {c}" for c in REQUIRED if not r.get(c)]
        if reasons:
            invalid.append({"row": i, **r, "reason": "; ".join(reasons)}); continue
        if "price" in r and r["price"] and to_float(r["price"]) is None:
            ctx.warn(f"Row {i} ({r['sku']}): price {r['price']!r} is not numeric (row kept)")
        if r["sku"].upper() in seen:
            ctx.warn(f"Row {i}: duplicate SKU {r['sku']} (row kept; run WF006 to review duplicates)")
        seen.add(r["sku"].upper())
        valid.append(r)
    ctx.state.update(valid=valid, invalid=invalid)
    return f"{len(valid)} valid, {len(invalid)} invalid"


@step("WF003")
def identify_invalid_rows(ctx):
    return "; ".join(f"row {r['row']}: {r['reason']}" for r in ctx.state["invalid"]) or "no invalid rows"


@step("WF003")
def produce_cleaned_dataset(ctx):
    v, bad = ctx.state["valid"], ctx.state["invalid"]
    clean_path = ctx.save_csv("cleaned.csv", v)
    bad_path = ctx.save_csv("invalid_rows.csv", bad)
    ctx.output = {"total_rows": len(v) + len(bad), "valid": len(v), "invalid": len(bad), "invalid_rows": bad}
    ctx.report = (f"Vendor file processed: {ctx.state['path']}\n"
                  f"  Total data rows: {len(v) + len(bad)} | Valid: {len(v)} | Invalid: {len(bad)}\n\n"
                  f"Invalid rows:\n" + table(bad, ["row", "sku", "product_name", "reason"])
                  + (f"\n\nWarnings:\n  " + "\n  ".join(ctx.warnings) if ctx.warnings else "")
                  + f"\n\n  Cleaned file: {clean_path}\n  Invalid-row report: {bad_path}")
    return "cleaned file and invalid-row report written"
