"""Central configuration. Everything tunable lives here or in .env."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # optional dependency
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass

WORKFLOW_XLSX = Path(os.getenv("WORKFLOW_XLSX", ROOT / "data" / "workflows.xlsx"))
SAMPLES = ROOT / "data" / "samples"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ROOT / "outputs"))
RUN_LOG = OUTPUT_DIR / "run_log.csv"

# --- LLM -------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()   # auto | anthropic | openai | offline
LLM_MODEL = os.getenv("LLM_MODEL", "")                      # empty -> provider default
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# --- Business thresholds (documented in README) ------------------------------
PRICE_TOLERANCE_PCT = float(os.getenv("PRICE_TOLERANCE_PCT", 10))
FAILURE_RATE_THRESHOLD_PCT = float(os.getenv("FAILURE_RATE_THRESHOLD_PCT", 10))
AVG_EXEC_TIME_THRESHOLD_MS = float(os.getenv("AVG_EXEC_TIME_THRESHOLD_MS", 3000))
DUPLICATE_POSSIBLE_THRESHOLD = float(os.getenv("DUPLICATE_POSSIBLE_THRESHOLD", 0.80))
ROUTER_MIN_SCORE = float(os.getenv("ROUTER_MIN_SCORE", 0.06))
TOOL_MAX_ATTEMPTS = int(os.getenv("TOOL_MAX_ATTEMPTS", 3))
