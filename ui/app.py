"""Streamlit view of the multi-agent run.   streamlit run ui/app.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from main import configure_logging, run_pipeline

configure_logging()

st.set_page_config(page_title="Migration Orchestrator", layout="wide")
st.title("AI Data Migration Orchestrator")
st.caption("Schema Analyst -> SQL Generator -> Validator (auto re-prompt) -> Explainer, with a Memory Agent around them.")

with st.sidebar:
    backend = st.radio("LLM backend", ["fake", "gemini", "groq"],
                        help="fake = offline rule-based stub; gemini needs GEMINI_API_KEY; groq needs GROQ_API_KEY")
    retries = st.slider("Re-prompts allowed", 0, 4, 2)
    use_memory = st.checkbox("Memory Agent", value=True)
    reset = st.checkbox("Reset memory first", value=False)
    run = st.button("Run migration", type="primary", use_container_width=True)

if run:
    out = Path(tempfile.mkdtemp(prefix="migration_out_"))
    try:
        with st.spinner("Agents are working..."):
            st.session_state["state"] = run_pipeline(backend, out_dir=out, max_attempts=retries + 1, use_memory=use_memory,
                                                     reset_memory=reset, memory_path=ROOT / "data" / "memory.json")
            st.session_state["out"] = out
    except Exception as e:
        st.error(f"Run failed: {e}")

state = st.session_state.get("state")
if not state:
    st.info("Pick a backend and press **Run migration**. The offline stub needs no key.")
    st.stop()

out = st.session_state["out"]
ok = state["passed"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Result", "PASSED" if ok else "FAILED")
c2.metric("SQL attempts", state["attempt"])
c3.metric("Findings for review", sum(1 for i in state["issues"] if i["class"] == "FINDING"))
c4.metric("Memory hints used", len(state.get("memory_hints", [])))
if state["llm_description"].startswith("fake"):
    st.warning("Output comes from the offline rule-based stub, not a language model.")

tabs = st.tabs(["Agent conversation", "Retry timeline", "SQL", "Validation report", "Explanation"])

with tabs[0]:
    for m in state["messages"]:
        with st.expander(f"{m['from']} -> {m['to']}   [{m['kind']}]   attempt {m['attempt']}"):
            st.json(m["payload"])

with tabs[1]:
    for h in state["history"]:
        (st.success if h["passed"] else st.error)(f"Attempt {h['attempt']}: {'passed' if h['passed'] else 'failed'}")
        if h["fixable"]:
            st.table(pd.DataFrame(h["fixable"]))

with tabs[2]:
    st.code(state["sql_text"], language="sql")
    st.download_button("Download SQL", state["sql_text"], file_name="sample_output.sql")

with tabs[3]:
    st.markdown((out / "validation_report.md").read_text(encoding="utf-8"))

with tabs[4]:
    st.markdown((out / "sql_explanation.md").read_text(encoding="utf-8"), unsafe_allow_html=True)
