import json
import shutil
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, llm
from agent.engine import Engine
from agent.registry import Registry, step, workflow_meta

CASE_FILE = Path(config.SAMPLES / "extra_requests.json")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    config.OUTPUT_DIR = tmp_path_factory.mktemp("out")
    config.RUN_LOG = config.OUTPUT_DIR / "run_log.csv"
    import agent.engine as e
    e.config = config
    return Engine()


# --- every Test_Questions row routes to the right workflow ------------------------------------
def test_all_excel_test_requests_route_correctly(engine):
    assert len(engine.registry.test_requests) == 10
    for t in engine.registry.test_requests:
        r = engine.run(t["Test_Request"])
        assert r.workflow_id == t["Workflow_ID"], (t["Test_Request"], r.workflow_id)


def test_excel_and_code_are_consistent(engine):
    assert len(engine.registry.specs) == 10
    assert all(engine.registry.implemented(w) for w in engine.registry.specs)


# --- business rules -----------------------------------------------------------------------------
def test_wf001_threshold_is_strict_less_than(engine):
    r = engine.run("Which products need restocking?")
    skus = {x["sku"] for x in r.output["restock_list"]}
    assert skus == {"SKU-001", "SKU-003", "SKU-005", "SKU-006"}
    assert "SKU-004" not in skus          # current == minimum -> NOT restocked
    assert "SKU-007" not in skus          # unreadable stock is skipped with a warning
    assert any("SKU-007" in w for w in r.warnings)


def test_wf002_exactly_10pct_not_flagged(engine):
    r = engine.run("Find products where vendor price differs by more than 10%.")
    flagged = {x["sku"] for x in r.output["exceptions"]}
    assert flagged == {"SKU-002", "SKU-003", "SKU-005"}
    assert r.output["unmatched_internal"] == ["SKU-009"] and r.output["unmatched_vendor"] == ["SKU-010"]


def test_wf002_threshold_from_request(engine):
    r = engine.run("Find products where vendor price differs by more than 20%.")
    assert {x["sku"] for x in r.output["exceptions"]} == {"SKU-005"}


def test_wf003_invalid_rows(engine):
    r = engine.run("Process this vendor spreadsheet and show invalid rows.")
    assert r.output["valid"] == 5 and r.output["invalid"] == 2
    reasons = {x["reason"] for x in r.output["invalid_rows"]}
    assert reasons == {"missing sku", "missing product_name"}


def test_wf003_missing_file_fails_cleanly(engine):
    r = engine.run("Process this vendor spreadsheet", {"vendor_file": "nope.csv"}, "WF003")
    assert r.status == "failed" and "not found" in r.message.lower()
    assert [s.status for s in r.steps][1:] == ["skipped"] * 5


def test_wf004_does_not_invent_missing_material(engine):
    r = engine.run("Generate SEO content for this product.")
    assert r.output["missing_information"] == ["material"]
    text = " ".join(str(v) for k, v in r.output.items() if k.endswith("description") or k == "seo_title").lower()
    assert "cotton" in text    # only because it is in the product NAME the user supplied
    assert len(r.output["seo_title"]) <= 60 and len(r.output["meta_description"]) <= 160


def test_wf004_needs_name_or_category(engine):
    r = engine.run("Generate SEO content for this product.", {"product": {"color": "red"}})
    assert r.status == "needs_input"


def test_wf005_found_and_not_found(engine):
    ok = engine.run("Where is order ORD-1001?")
    assert ok.status == "success" and "BD1234567" in ok.report
    nf = engine.run("Where is order ORD-9999?")
    assert nf.status == "needs_input" and "another identifier" in nf.message
    assert engine.run("What is the status of my order?").status == "needs_input"


def test_wf005_retry_and_graceful_degradation(engine):
    flaky = engine.run("Where is order ORD-1004?")
    assert flaky.status == "success" and any(c["attempt"] == 2 for c in flaky.tool_calls)
    broken = engine.run("Where is order ORD-1006?")
    assert broken.status == "success" and broken.warnings and "temporarily unavailable" in broken.report
    assert any(s.status == "warning" for s in broken.steps)


def test_wf006_confidence_and_variants(engine):
    r = engine.run("Find likely duplicate products in the catalog.")
    groups = r.output["groups"]
    assert groups[0]["confidence"] == "DEFINITE"
    all_skus = {p["sku"] for g in groups for p in g["products"]}
    assert "SKU-1003" not in all_skus     # black variant must not be flagged


def test_wf007_asks_then_generates(engine):
    r = engine.run("Create a campaign brief for the new collection.")
    assert r.status == "needs_input" and set(r.missing) == {"goal", "dates"}
    r2 = engine.run("Create a campaign brief for the new collection.",
                    {"goal": "Boost launch sales", "dates": "2026-11-01 to 2026-11-30"})
    assert r2.status == "success"
    assert {"objective", "messaging", "channels", "timeline", "checklist"} <= set(r2.output)
    bad = engine.run("Create a campaign brief", {"goal": "x", "dates": "2026-11-30 to 2026-11-01"}, "WF007")
    assert bad.status == "needs_input"


def test_wf008_dedupe_and_intents(engine):
    r = engine.run("Classify these keywords and map them to pages.")
    kws = {k["keyword"]: k for k in r.output["keywords"]}
    assert len(kws) == 11
    assert {k["intent"] for k in kws.values()} <= {"informational", "commercial", "transactional", "navigational"}
    assert kws["buy wireless mouse online"]["intent"] == "transactional"
    assert kws["hydropro login"]["intent"] == "navigational"


def test_wf009_ranking_and_escalation(engine):
    r = engine.run("Assign this urgent task to the best available developer.")
    assert r.output["assignee"] == "Karan Verma" and r.output["priority"] == "High"
    e = engine.run("Assign this urgent task to the best available developer.",
                   {"task": "Urgent: migrate the cluster to kubernetes"})
    assert e.status == "escalated"


def test_wf010_flags_thresholds(engine):
    r = engine.run("Which workflows are failing most often?")
    rows = {x["workflow"]: x for x in r.output["summary"]}
    assert rows["WF005"]["flags"].startswith("HIGH FAILURE")
    assert r.output["summary"][0]["failure_%"] >= r.output["summary"][-1]["failure_%"]


def test_unclear_request_is_not_guessed(engine):
    r = engine.run("hello there")
    assert r.status == "no_match" and "rephrase" in r.message


# --- LLM path (mocked) + fallback -----------------------------------------------------------------
def test_llm_router_and_fallback(engine, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(llm, "_call", lambda s, u: json.dumps(
        {"workflow_id": "WF002", "confidence": 0.93, "reasoning": "price comparison", "params": {"tolerance_pct": 5}}))
    r = engine.run("are our vendor numbers sane?")
    assert r.route.method == "llm" and r.workflow_id == "WF002" and r.route.params["tolerance_pct"] == 5

    monkeypatch.setattr(llm, "_call", lambda s, u: "not json at all")   # broken LLM -> lexical fallback
    r = engine.run("Which products need restocking?")
    assert r.route.method == "lexical" and r.workflow_id == "WF001"


def test_llm_generated_content_used_and_flagged_when_invented(engine, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(llm, "_call", lambda s, u: json.dumps({
        "workflow_id": "WF004", "confidence": 0.9, "reasoning": "content", "params": {},
        "description": "Soft polyester tee.", "short_description": "Tee", "seo_title": "T" * 80,
        "meta_description": "Great tee"}))
    r = engine.run("Generate SEO content for this product.")
    assert r.output["generated_by"] == "llm:anthropic"
    assert len(r.output["seo_title"]) <= 60                       # post-LLM validation trims
    assert any("polyester" in w for w in r.warnings)              # guardrail catches invented material


# --- the 11th workflow --------------------------------------------------------------------------------
def test_adding_an_11th_workflow_needs_only_excel_row_plus_one_module(tmp_path, monkeypatch):
    xlsx = tmp_path / "wf.xlsx"
    shutil.copy(config.WORKFLOW_XLSX, xlsx)
    wb = openpyxl.load_workbook(xlsx)
    wb["Workflows"].append(["WF011", "Refund Eligibility Check", "User asks whether an order can be refunded", "Order ID",
                            "Load order → check refund window → decide eligibility", "Eligible if within 30 days",
                            "Order database", "Eligibility decision with reason"])
    wb.save(xlsx)
    monkeypatch.setattr(config, "WORKFLOW_XLSX", xlsx)

    workflow_meta("WF011", keywords=["refund", "return", "eligible"])

    @step("WF011")
    def load(ctx): ctx.state["days"] = 12

    @step("WF011")
    def window(ctx): ctx.decide("30-day refund window")

    @step("WF011")
    def decide(ctx):
        ok = ctx.state["days"] <= 30
        ctx.output = {"eligible": ok}; ctx.report = f"Refund eligible: {ok}"

    eng = Engine(Registry.load())
    r = eng.run("Can this order be refunded? Is it eligible for a refund?")
    assert r.workflow_id == "WF011" and r.status == "success" and r.output["eligible"] is True
    # and the original 10 still route correctly
    assert eng.run("Where is order ORD-1001?").workflow_id == "WF005"
    from agent import registry
    registry._HANDLERS.pop("WF011", None); registry._META.pop("WF011", None)   # keep global state clean
