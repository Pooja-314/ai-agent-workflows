"""Generates data/samples/execution_logs.csv (seeded, reproducible) from the Excel step lists.
Failure/slowness hot-spots are planted deliberately so WF010 has something to find."""
import csv, random, sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.registry import Registry

random.seed(7)
reg = Registry.load(strict=False)
rows, t = [], datetime(2026, 9, 1, 9, 0)
# (failure probability, failing step index (1-based), error text, slow step index, slow ms)
profile = {
 "WF001": (0.03, 1, "FileNotFoundError: inventory.csv", None, 0),
 "WF002": (0.08, 3, "KeyError: sku", None, 0),
 "WF003": (0.25, 2, "ParserError: unexpected delimiter", 1, 2600),
 "WF004": (0.12, 2, "LLMTimeout: description generation", 2, 3400),
 "WF005": (0.30, 4, "ShipmentAPITimeout: carrier unreachable", 4, 3900),
 "WF006": (0.05, 5, "MemoryError: comparison matrix too large", 5, 1800),
 "WF007": (0.10, 5, "LLMRateLimit: 429", 4, 3000),
 "WF008": (0.15, 3, "LLMTimeout: intent classification", 3, 5200),
 "WF009": (0.04, 3, "DBConnectionError: employee table", None, 0),
 "WF010": (0.02, 1, "FileNotFoundError: logs", None, 0),
}
for wid, spec in reg.specs.items():
    pf, fail_step, err, slow_step, slow_ms = profile[wid]
    for n in range(1, 21):
        t += timedelta(minutes=random.randint(7, 55))
        run_id = f"{wid.lower()}-{n:02d}"
        fails = random.random() < pf
        for i, name in enumerate(spec.steps, start=1):
            dur = random.randint(40, 380)
            if slow_step == i:
                dur = int(slow_ms * random.uniform(0.8, 1.2))
            if fails and i == fail_step:
                rows.append([t.isoformat(timespec="seconds"), run_id, wid, name, "failed", dur, err]); break
            rows.append([t.isoformat(timespec="seconds"), run_id, wid, name, "success", dur, ""])
out = Path(__file__).resolve().parent.parent / "data" / "samples" / "execution_logs.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["timestamp","run_id","workflow_id","step_name","status","duration_ms","error"]); w.writerows(rows)
print("wrote", out, len(rows), "rows")
