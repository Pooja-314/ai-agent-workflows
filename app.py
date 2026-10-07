"""Streamlit demo for the Excel-driven AI Workflow Agent."""
from __future__ import annotations

import json
from dataclasses import asdict

import streamlit as st

from agent import llm
from agent.engine import Engine
from agent.formatter import to_json


st.set_page_config(page_title="AI Workflow Agent", page_icon="🤖", layout="wide")

@st.cache_resource
def get_engine() -> Engine:
    return Engine()


def status_badge(status: str) -> str:
    return {
        "success": "🟢 SUCCESS",
        "needs_input": "🟡 NEEDS INPUT",
        "escalated": "🟠 ESCALATED",
        "failed": "🔴 FAILED",
        "no_match": "⚪ NO MATCH",
        "not_implemented": "🔴 NOT IMPLEMENTED",
    }.get(status, status.upper())


engine = get_engine()

st.title("🤖 AI Agent Workflow Automation")
st.caption("Excel-driven workflow selection, tool execution, conditions, error handling, and traceability")

with st.sidebar:
    st.header("System")
    st.metric("Workflows loaded", len(engine.registry.specs))
    st.write(f"**LLM provider:** `{llm.provider()}`")
    st.write("**Source of truth:** `data/workflows.xlsx`")
    st.divider()
    st.subheader("Architecture")
    st.code("User Request\n   ↓\nWorkflow Router\n   ↓\nGeneric Executor\n   ↓\nTools / APIs\n   ↓\nConditions & Errors\n   ↓\nFinal Result", language="text")

run_tab, workflows_tab, json_tab = st.tabs(["🚀 Run Agent", "📋 Workflows", "🔎 Raw Result"])

with run_tab:
    st.subheader("Natural-language request")
    examples = [
        "Which products need restocking?",
        "Find products where vendor price differs by more than 10%.",
        "Process this vendor spreadsheet and show invalid rows.",
        "Generate SEO content for this product.",
        "Where is order ORD-1001?",
        "Find likely duplicate products in the catalog.",
        "Create a campaign brief for the new collection.",
        "Classify these SEO keywords by search intent.",
        "Assign this urgent task to the best available developer.",
        "Which workflows are failing most often?",
    ]
    request = st.text_area("What would you like the agent to do?", height=90, placeholder=examples[0])
    st.caption("Try: " + " • ".join(examples[:3]))

    with st.expander("Optional parameters", expanded=False):
        st.caption(
            "Optional workflow-specific inputs. "
            "Use key=value pairs separated by commas. "
            "Leave blank if the request already contains the required information."
        )
        raw_params = st.text_input(
            "Parameters (key=value, comma-separated)",
            placeholder="e.g. order_id=ORD-1001"
        )

    if st.button("▶ Run Agent", type="primary", disabled=not request.strip()):
        params = {}
        if raw_params.strip():
            for item in raw_params.split(","):
                key, sep, value = item.partition("=")
                if sep and key.strip():
                    params[key.strip()] = value.strip()

        with st.spinner("Routing request and executing workflow..."):
            result = engine.run(request.strip(), params)
        st.session_state["last_result"] = result

    result = st.session_state.get("last_result")
    if result:
        st.divider()
        if result.workflow_id:
            c1, c2, c3 = st.columns(3)
            c1.metric("Selected workflow", result.workflow_id)
            c2.metric("Confidence", f"{result.route.confidence:.0%}")
            c3.metric("Status", status_badge(result.status))
            st.info(f"**{result.workflow_name}** — {result.route.reasoning}")
        else:
            st.warning(result.message)

        if result.steps:
            st.subheader("Execution trace")
            for step in result.steps:
                icon = {"ok": "✅", "failed": "❌", "skipped": "⏭️", "warning": "⚠️"}.get(step.status, "•")
                label = f"{icon} **{step.index}. {step.name}** — `{step.status}` — {step.duration_ms} ms"
                with st.expander(label, expanded=step.status in {"failed", "warning"}):
                    if step.detail:
                        st.write(step.detail)
                    if step.error:
                        st.error(step.error)
                    st.write(f"Attempts: {step.attempts}")

        if result.decisions:
            st.subheader("Conditions / decisions")
            for decision in result.decisions:
                st.write("• " + decision)

        if result.tool_calls:
            st.subheader("Tool / API calls")
            st.dataframe(result.tool_calls, use_container_width=True, hide_index=True)

        if result.warnings:
            st.subheader("Warnings")
            for warning in result.warnings:
                st.warning(warning)

        st.subheader("Final result")
        if result.report and result.status == "success":
            st.code(result.report, language="text")
        else:
            st.write(result.message)
        if result.missing:
            st.write("**Missing input:** " + ", ".join(result.missing))
        if result.artifacts:
            st.write("**Artifacts:** " + ", ".join(result.artifacts))

with workflows_tab:
    st.subheader("Excel workflow catalogue")
    rows = []
    for wid, spec in engine.registry.specs.items():
        rows.append({
            "ID": wid,
            "Workflow": spec.name,
            "Steps": len(spec.steps),
            "Tools": spec.tools,
            "Trigger": spec.trigger,
            "Implemented": engine.registry.implemented(wid),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    selected = st.selectbox("Inspect workflow", list(engine.registry.specs))
    spec = engine.registry.specs[selected]
    st.markdown(f"### {spec.id} — {spec.name}")
    st.write("**Trigger:**", spec.trigger)
    st.write("**Inputs:**", spec.inputs)
    st.write("**Decision logic:**", spec.decision_logic)
    st.write("**Tools:**", spec.tools)
    st.write("**Expected output:**", spec.expected_output)
    st.write("**Steps:**")
    for i, step in enumerate(spec.steps, 1):
        st.write(f"{i}. {step}")

with json_tab:
    result = st.session_state.get("last_result")
    if result:
        st.json(json.loads(to_json(result)))
    else:
        st.info("Run a request first.")
