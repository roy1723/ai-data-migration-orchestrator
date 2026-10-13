"""Streamlit view of the multi-agent run.   streamlit run ui/app.py"""
import logging
import queue
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from main import configure_logging, run_pipeline

configure_logging()

st.set_page_config(page_title="Migration Orchestrator", layout="wide", page_icon="\U0001F9ED")

# --------------------------------------------------------------------- style
NAVY, TEAL, AMBER = "#1b2a4a", "#0d6e6e", "#b9770e"
st.markdown(f"""
<style>
.block-container {{ padding-top: 1.6rem; max-width: 1200px; }}
.stButton>button[kind="primary"] {{
    background: {TEAL}; border: none; font-weight: 700; letter-spacing: 0.02em;
    box-shadow: 0 2px 8px rgba(13,110,110,0.30);
}}
.stButton>button[kind="primary"]:hover {{ background: {NAVY}; }}
.hero {{
    background: linear-gradient(120deg, {NAVY} 0%, {TEAL} 100%);
    color: white; padding: 1.4rem 1.8rem; border-radius: 14px; margin-bottom: 1.1rem;
}}
.hero h1 {{ color: white; margin: 0 0 0.2rem 0; font-size: 1.9rem; }}
.hero p {{ margin: 0; opacity: 0.92; font-size: 0.96rem; }}
.step-pill {{
    display: inline-block; padding: 5px 15px; margin: 3px 4px 3px 0; border-radius: 999px;
    font-size: 0.80rem; font-weight: 700; font-family: inherit;
}}
.step-pending {{ background: #e9ecf2; color: #8a8f98; }}
.step-active {{ background: {TEAL}; color: white; box-shadow: 0 0 0 4px rgba(13,110,110,0.18); }}
.step-done {{ background: {NAVY}; color: white; opacity: 0.88; }}
.step-arrow {{ color: #c3c7d1; font-weight: 700; margin: 0 2px; }}
div[data-testid="stMetric"] {{
    border: 1px solid rgba(128,128,128,0.25); border-radius: 10px; padding: 0.6rem 0.8rem;
}}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="hero">
  <h1>AI Data Migration Orchestrator</h1>
  <p>Schema Analyst &rarr; SQL Generator &rarr; Validator (auto re-prompt) &rarr; Explainer, wrapped by a Memory Agent.</p>
</div>
""", unsafe_allow_html=True)

with st.expander("ℹ️  How this works, in plain terms"):
    st.markdown(
        "Four AI agents run in a fixed pipeline, with **no human in the loop**, to turn a legacy schema, a "
        "target schema and a field-mapping sheet into migration SQL:\n\n"
        "1. **Schema Analyst** - reads both schemas and the mapping, flags mismatches (wrong types, missing "
        "fields, length overflows).\n"
        "2. **SQL Generator** - writes the `INSERT ... SELECT` statements.\n"
        "3. **Validator** - checks the SQL three ways (syntax, a real SQLite dry run, then an LLM review) and, "
        "if it finds something the generator can fix, sends it back for another attempt - this is the "
        "**auto re-prompt** loop.\n"
        "4. **Explainer** - writes a plain-English write-up of the final SQL for a non-technical reviewer.\n\n"
        "**Memory Agent** - remembers defects that were fixed on earlier runs (for example, a mapping that points "
        "at a field that doesn't exist) and feeds those lessons back to the SQL Generator up front, so the same "
        "mistake isn't repeated on the next run. It reads `data/memory.json` before the run and updates it "
        "after.\n\n"
        "**Reset memory first** - deletes everything the Memory Agent has learned before this run starts, so you "
        "can watch the retry loop happen fresh instead of the Memory Agent already knowing the fix."
    )

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### Run settings")
    backend = st.radio(
        "LLM backend", ["fake", "gemini", "groq"],
        help="**fake** = offline rule-based stub, no key needed. **gemini** needs GEMINI_API_KEY. "
             "**groq** needs GROQ_API_KEY.",
    )
    retries = st.slider("Re-prompts allowed", 0, 4, 2,
                        help="How many times the Validator may send the SQL back to the Generator with "
                             "feedback before giving up and reporting FAILED.")
    use_memory = st.checkbox(
        "Memory Agent", value=True,
        help="When on, a fifth agent remembers defects resolved on earlier runs (in data/memory.json) and "
             "hands those lessons to the SQL Generator before it writes anything, so a past mistake isn't "
             "repeated. Turn it off to see the Generator solve every defect from scratch every time.",
    )
    reset = st.checkbox(
        "Reset memory first", value=False,
        help="Deletes data/memory.json before this run, erasing everything the Memory Agent has learned so "
             "far. Use this to watch the auto re-prompt loop happen fresh, instead of the Memory Agent already "
             "knowing the fix from a previous run.",
    )
    run = st.button("Run migration", type="primary", use_container_width=True)

# ------------------------------------------------------------- live run
PIPELINE = ["memory_recall", "schema_analyst", "sql_generator", "validator", "explainer", "memory_update"]
PIPELINE_LABELS = {
    "memory_recall": "Memory Agent (recall)", "schema_analyst": "Schema Analyst",
    "sql_generator": "SQL Generator", "validator": "Validator",
    "explainer": "Explainer", "memory_update": "Memory Agent (update)",
}
_NODE_TO_STEP = {
    "MemoryAgent": None,  # resolved below (recall vs. update) based on what's already been seen
    "SchemaAnalystAgent": "schema_analyst",
    "SQLGeneratorAgent": "sql_generator",
    "ValidationAgent": "validator",
    "ExplainerAgent": "explainer",
}
_AGENT_LINE_RE = re.compile(r"-> (\w+) \(attempt (-?\d+)\)")


class _QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(self.format(record))


def _render_steps(current_step: str | None, explainer_seen: bool) -> str:
    idx = PIPELINE.index(current_step) if current_step in PIPELINE else -1
    pills = []
    for i, key in enumerate(PIPELINE):
        cls = "step-done" if i < idx else ("step-active" if i == idx else "step-pending")
        pills.append(f'<span class="step-pill {cls}">{PIPELINE_LABELS[key]}</span>')
        if i < len(PIPELINE) - 1:
            pills.append('<span class="step-arrow">&rarr;</span>')
    return "".join(pills)


if run:
    out = Path(tempfile.mkdtemp(prefix="migration_out_"))
    log_queue: queue.Queue = queue.Queue()
    handler = _QueueHandler(log_queue)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(handler)

    result: dict = {}

    def _worker():
        try:
            result["state"] = run_pipeline(
                backend, out_dir=out, max_attempts=retries + 1, use_memory=use_memory,
                reset_memory=reset, memory_path=ROOT / "data" / "memory.json",
            )
        except Exception as e:  # surfaced in the main thread below
            result["error"] = e

    thread = threading.Thread(target=_worker, daemon=True)
    start = time.time()
    thread.start()

    step_box = st.empty()
    log_lines: list[str] = []
    current_step, explainer_seen, attempt_no = None, False, 0

    with st.status("Agents are working...", expanded=True) as status:
        log_box = st.empty()
        while thread.is_alive() or not log_queue.empty():
            drained = False
            while True:
                try:
                    line = log_queue.get_nowait()
                except queue.Empty:
                    break
                log_lines.append(line)
                drained = True
                m = _AGENT_LINE_RE.search(line)
                if m:
                    node, attempt_no = m.group(1), int(m.group(2))
                    if node == "MemoryAgent":
                        current_step = "memory_update" if explainer_seen else "memory_recall"
                    else:
                        current_step = _NODE_TO_STEP.get(node, current_step)
                        if node == "ExplainerAgent":
                            explainer_seen = True
            if drained:
                step_box.markdown(_render_steps(current_step, explainer_seen), unsafe_allow_html=True)
                log_box.code("\n".join(log_lines[-18:]), language="log")
                label = PIPELINE_LABELS.get(current_step, "starting up")
                status.update(label=f"Agents are working... {label}, attempt {max(attempt_no, 1)} "
                                     f"({time.time() - start:.0f}s elapsed)")
            time.sleep(0.12)
        thread.join()
        logging.getLogger().removeHandler(handler)

        if "error" in result:
            status.update(label="Run failed", state="error")
            st.error(f"Run failed: {result['error']}")
        else:
            status.update(label=f"Done in {time.time() - start:.1f}s", state="complete")
            st.session_state["state"] = result["state"]
            st.session_state["out"] = out

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

tabs = st.tabs(["\U0001F4AC Agent conversation", "\U0001F501 Retry timeline", "\U0001F4C4 SQL",
                "✅ Validation report", "\U0001F4DD Explanation"])

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
