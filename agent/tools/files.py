"""CSV / Excel readers and writers."""
from __future__ import annotations

import csv
from pathlib import Path

from ..models import ToolError
from . import tool


def _resolve(path: str) -> Path:
    from ..config import ROOT
    p = Path(path)
    return p if p.is_absolute() else (ROOT / p)


@tool("csv_reader")
def csv_reader(path: str) -> dict:
    """Read a CSV file into {'headers': [...], 'rows': [dict, ...]} (all values are strings)."""
    p = _resolve(path)
    if not p.exists():
        raise ToolError(f"File not found: {path}")
    try:
        with open(p, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = [dict(r) for r in reader]
            headers = reader.fieldnames or []
    except Exception as e:  # malformed CSV
        raise ToolError(f"Could not parse CSV '{path}': {e}")
    return {"headers": headers, "rows": rows}


@tool("excel_csv_parser")
def excel_csv_parser(path: str) -> dict:
    """Read .csv / .xlsx / .xls(x) into {'headers', 'rows'}; first sheet, first row = header."""
    p = _resolve(path)
    if not p.exists():
        raise ToolError(f"File not found: {path}")
    suffix = p.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        return csv_reader(path)
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            ws = wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            headers = [str(h).strip() if h is not None else "" for h in next(it)]
            rows = [{h: ("" if v is None else str(v)) for h, v in zip(headers, r)} for r in it]
        except StopIteration:
            return {"headers": [], "rows": []}
        except Exception as e:
            raise ToolError(f"Could not parse Excel file '{path}': {e}")
        return {"headers": headers, "rows": rows}
    raise ToolError(f"Unsupported file type '{suffix}'. Use .csv or .xlsx")


@tool("json_reader")
def json_reader(path: str):
    import json
    p = _resolve(path)
    if not p.exists():
        raise ToolError(f"File not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ToolError(f"Invalid JSON in '{path}': {e}")


def write_csv(path: Path, rows: list[dict], headers: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = headers or (list(rows[0].keys()) if rows else [])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
