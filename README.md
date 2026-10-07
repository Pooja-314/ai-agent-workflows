# Excel-Driven AI Workflow Agent

One reusable agent that reads business workflows from an Excel file, picks the right one for a
natural-language request, executes its steps with tools, handles conditions/errors, and returns the
result together with a full trace (selected workflow → steps → tool calls → decisions → result).

**There are no 10 chatbots.** There is one router, one engine, one tool registry, and 10 small plug-in modules.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py --list                                   # shows the 10 workflows loaded from Excel
python main.py "Which products need restocking?"
python main.py --run-examples                           # all 10 Test_Questions + 10 edge cases
python -m pytest -q                                     # 20 tests
python main.py --interactive                            # chat-style; asks follow-up questions
streamlit run app.py                                    # browser-based demo for the Loom
```

**No API key is required.** Without one, routing uses a TF-IDF matcher and the three LLM workflows
(WF004 descriptions, WF007 messaging, WF008 intent) use a deterministic template engine; the trace
says `offline-simulated` whenever that happens. To use a real model copy `.env.example` to `.env`
and set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`; alternatively set `LLM_PROVIDER=ollama` to use a local Ollama model such as Mistral without a cloud API key; the trace then says `llm:anthropic` / `llm:openai`.
If the API call or its JSON fails, the agent falls back to offline mode instead of crashing.
(The live-LLM path is covered by mocked tests; run it once with your own key before demoing.)

## Demo application

The repository includes a Streamlit UI (`app.py`) for demonstrating the actual working agent. It shows the selected workflow, routing confidence, step-by-step execution trace, conditions/decisions, tool/API calls, warnings, and final result.

Run it with:

```bash
streamlit run app.py
```

The UI uses the same `Engine` as the CLI; it is not a separate implementation.

## Architecture

```
User request
   │
   ▼
Router  (agent/router.py)       LLM picks workflow + extracts params from the Excel catalogue
   │                            └─ fallback / sanity: TF-IDF over name+trigger+steps+outputs (+keywords)
   │                            └─ low confidence → "no_match": asks the user to clarify, never guesses
   ▼
Engine  (agent/engine.py)       runs the workflow's steps in the order defined in Excel
   │   ├─ RunContext            params, shared state, decisions, warnings, tool-call log
   │   ├─ Tool registry         csv_reader, excel_csv_parser, calculator, text_similarity,
   │   │  (agent/tools/)        order_api, shipment_api, json_reader, llm  – logged + retried on transient errors
   │   └─ Uniform control flow  NeedsInput → ask user | Escalation → hand to human |
   │                            optional step fails → warn and continue | step fails → stop, rest SKIPPED
   ▼
Result + trace  (agent/formatter.py)  → console / --json / outputs/examples/*.txt
```

| Layer | File | Responsibility |
|---|---|---|
| Source of truth | `data/workflows.xlsx` | ID, name, trigger, inputs, **ordered steps**, decision logic, tools, expected output, test requests |
| Binding | `agent/registry.py` | Loads Excel, binds `@step("WFxxx")` functions by order, **fails at startup if step counts disagree** |
| Plug-ins | `workflows/wfNNN_*.py` | The actual logic of each step (one file per workflow) |
| Selection | `agent/router.py` | LLM or TF-IDF routing |
| Execution | `agent/engine.py` | Step loop, error policy, retries, run log |

### How each Excel column is used
* **Steps** – split on `→`; they become the labels, order and count shown in the trace and are validated against code.
* **Trigger / Name / Inputs / Expected_Output** – feed the router's catalogue.
* **Decision_Logic** – implemented inside the relevant step *and* echoed in the trace under "Conditions / Decisions".
* **Test_Questions** – run by `--run-examples` and the test-suite.

## Adding workflow #11 (≈ 15 lines, no engine/router/CLI changes)

1. Add a row to the *Workflows* sheet (steps separated by `→`).
2. Create `workflows/wf011_whatever.py`:
```python
from agent.registry import step, workflow_meta
workflow_meta("WF011", keywords=["refund", "return"])          # optional routing hints / params

@step("WF011")                       # step 1 = first item in the Excel "Steps" cell
def load_order(ctx):
    ctx.state["order"] = ctx.call_tool("order_api", order_id=ctx.params["order_id"])[0]

@step("WF011")
def decide(ctx):
    ctx.decide("Eligible if within 30 days"); ctx.output = {...}; ctx.report = "..."
```
The module is auto-discovered. `tests/test_workflows.py::test_adding_an_11th_workflow...` does exactly this and passes.

## The 10 workflows

| ID | Workflow | Key decision logic (as implemented) |
|---|---|---|
| WF001 | Inventory Restock | `current < minimum` (equal is **not** restocked); unreadable rows skipped + warned; reorder = max_stock (or 2×min) − current |
| WF002 | Price Validation | SKU normalised; diff = (vendor−internal)/internal; flag if `abs > 10%` (exactly 10% passes; threshold can be given in the request, e.g. "more than 20%"); unmatched SKUs on both sides reported |
| WF003 | Vendor File | CSV/XLSX; header aliases ("Item Code"→sku); rows missing SKU or name are invalid; blank rows dropped; bad price / duplicate SKU = warning |
| WF004 | Description Generator | Missing attributes are **never invented**, listed as "MISSING INFORMATION"; length limits enforced after generation; guardrail warns if output mentions a material not in the input |
| WF005 | Order Status | Validates ID/email; not found → asks for another identifier; carrier lookup is an *optional* step (retry on timeout, degrades gracefully) |
| WF006 | Duplicates | Exact normalised SKU = DEFINITE; name+attribute similarity ≥ 0.80 = possible (HIGH/MEDIUM); same product in another colour/size = variant, not flagged; union-find grouping |
| WF007 | Campaign Brief | Goal or dates missing → asks **before** generating; dates validated; timeline computed in code (not by the LLM) |
| WF008 | SEO Keywords | De-dupe; LLM output validated to the 4 allowed intents; priority = intent + volume; unmapped keywords flagged |
| WF009 | Task Assignment | Role + required skills + spare hours must fit; ranked by 0.6·skill + 0.4·free-capacity; nobody suitable → **escalate** |
| WF010 | Performance Report | Failure rate per run; flags failure > 10% or avg > 3000 ms; `needs_input`/`escalated` runs are *not* counted as failures |

## Key technical decisions (for the Loom)
* **Excel as the contract, code as the implementation.** Step names/order come from Excel; a mismatch fails fast at startup.
* **Hand-rolled ~200-line engine instead of LangGraph/LangChain.** Workflows here are linear with early exits; a tiny engine makes every decision explicit and testable. The step list could be mapped onto a LangGraph `StateGraph` without changing workflow modules.
* **LLM only where language is the task** (routing, WF004/007/008). Numbers, dates, thresholds, matching and ranking are deterministic code. LLM outputs are validated, and every LLM call has an offline fallback.
* **Errors are first-class results**, not exceptions: `needs_input`, `escalated`, `failed`, `no_match`, `not_implemented`.
* **Simulated APIs** (`order_api`, `shipment_api`) live behind the same tool interface a real API client would use; the carrier mock fails once on purpose to show retries.
* **Self-observing:** every real run appends to `outputs/run_log.csv` (same schema WF010 analyses; use `--param include_live_runs=true`).

## Limitations / next steps
Linear workflows only (no branching graph yet); the lexical router can confuse very short requests; WF006 is O(n²) (use blocking on brand/category for large catalogues); "attachments" are simulated by default sample files – pass `--param vendor_file=...`, `keywords_csv=...`, etc. for your own data.

## Repo layout
```
main.py  agent/{config,models,registry,router,engine,llm,formatter}.py  agent/tools/
workflows/wf001…wf010_*.py   data/workflows.xlsx   data/samples/   tests/   outputs/examples/
```
