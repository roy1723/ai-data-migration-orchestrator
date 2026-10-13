"""AI Data Migration Orchestrator: multi-agent SQL generation with LangGraph.

    python main.py                  # offline stub LLM, no key needed
    python main.py --llm gemini     # real model (needs GEMINI_API_KEY)

Flow:  memory_recall -> schema_analyst -> sql_generator -> validator
       validator --FIXABLE issues and attempts left--> sql_generator   (auto re-prompt)
       validator --otherwise--> explainer -> memory_update -> END
Agents never call each other. Each node reads the shared state, returns a partial
update and one message; the wrapper below appends that message to the trace file.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from agents import ExplainerAgent, MemoryAgent, SchemaAnalystAgent, SQLGeneratorAgent, ValidationAgent
from core.schema_utils import load_inputs
from core.state import MigrationState
from core.trace import TraceLog
from llm.base import make_client

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("orchestrator")

try:  # read GEMINI_API_KEY etc. from .env if python-dotenv is installed; real environment variables win
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def configure_logging(level=logging.INFO) -> None:
    """Make agent/LLM-client log messages visible in the terminal (or Streamlit's console)."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def build_graph(llm, memory: MemoryAgent, trace: TraceLog):
    analyst, generator = SchemaAnalystAgent(llm), SQLGeneratorAgent(llm)
    validator, explainer = ValidationAgent(llm), ExplainerAgent(llm)

    def node(fn):
        """Run an agent step, log its message to the trace, return the state update."""
        name = getattr(fn, "__self__", fn).__class__.__name__

        def wrapped(state):
            log.info("-> %s (attempt %s)", name, state.get("attempt"))
            update = fn(state)
            for m in update.get("messages", []):
                log.info("   %s -> %s [%s]", m["from"], m["to"], m["kind"])
            trace.write(update.get("messages", []))
            return update
        return wrapped

    def route_after_validation(state) -> str:
        retries_left = state["attempt"] < state["config"]["max_attempts"]
        return "sql_generator" if state["feedback"] and retries_left else "explainer"

    g = StateGraph(MigrationState)
    g.add_node("memory_recall", node(memory.recall))
    g.add_node("schema_analyst", node(analyst.run))
    g.add_node("sql_generator", node(generator.run))
    g.add_node("validator", node(validator.run))
    g.add_node("explainer", node(explainer.run))
    g.add_node("memory_update", node(memory.update))
    g.add_edge(START, "memory_recall")
    g.add_edge("memory_recall", "schema_analyst")
    g.add_edge("schema_analyst", "sql_generator")
    g.add_edge("sql_generator", "validator")
    g.add_conditional_edges("validator", route_after_validation, ["sql_generator", "explainer"])
    g.add_edge("explainer", "memory_update")
    g.add_edge("memory_update", END)
    return g.compile()


def run_pipeline(llm_kind: str = "fake", data_dir: Path = ROOT / "data", out_dir: Path = ROOT / "outputs",
                 max_attempts: int = 3, use_memory: bool = True, reset_memory: bool = False,
                 memory_path: Path | None = None, llm=None) -> dict:
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    memory_path = Path(memory_path or data_dir / "memory.json")
    if reset_memory and memory_path.exists():
        memory_path.unlink()
    llm = llm or make_client(llm_kind)
    log.info("Starting pipeline with backend=%s", llm.describe())
    trace = TraceLog(out_dir / "agent_trace.jsonl")
    graph = build_graph(llm, MemoryAgent(llm, memory_path, enabled=use_memory), trace)
    inputs = load_inputs(data_dir)
    try:
        state = graph.invoke({**inputs, "attempt": 0, "history": [], "messages": [],
                              "config": {"max_attempts": max_attempts, "out_dir": str(out_dir)}})
    except Exception:
        log.exception("Pipeline failed")
        raise
    (out_dir / "sample_output.sql").write_text(state["sql_text"], encoding="utf-8")
    state["llm_description"] = llm.describe()
    log.info("Pipeline finished: passed=%s attempts=%s", state["passed"], state["attempt"])
    return state


def print_summary(state: dict, out_dir: Path) -> None:
    print(f"\nMigration run   LLM: {state['llm_description']}")
    print(f"Load order      {' -> '.join(state['facts']['load_order'])}\n")
    print(f"{'Attempt':<8}{'Result':<8}Blocking issues")
    for h in state["history"]:
        detail = "; ".join(f"{f['code']} ({f['target']})" for f in h["fixable"]) or "none"
        print(f"{h['attempt']:<8}{'pass' if h['passed'] else 'FAIL':<8}{detail}")
    counts = state["dry_run"].get("target_row_counts", {})
    if counts:
        print("\nDry run rows    " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    findings = [i for i in state["issues"] if i["class"] == "FINDING"]
    print(f"\nFindings for business review: {len(findings)}")
    for i in sorted(findings, key=lambda i: {"error": 0, "warning": 1}.get(i["severity"], 2)):
        print(f"  [{i['severity']:<7}] {i['target']}: {i['message']}")
    print(f"\nResult: {'PASSED' if state['passed'] else 'FAILED'} after {state['attempt']} attempt(s)")
    print(f"Files written to {out_dir}: sample_output.sql, validation_report.md, sql_explanation.md, agent_trace.jsonl")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Multi-agent SQL migration generator")
    p.add_argument("--llm", choices=["fake", "gemini", "groq"], default="fake", help="LLM backend (default: offline stub)")
    p.add_argument("--max-retries", type=int, default=2, help="re-prompts allowed after the first attempt (default 2)")
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--out-dir", default=str(ROOT / "outputs"))
    p.add_argument("--no-memory", action="store_true", help="disable the Memory Agent")
    p.add_argument("--reset-memory", action="store_true", help="delete stored memory before running")
    a = p.parse_args(argv)
    configure_logging()
    try:
        state = run_pipeline(a.llm, a.data_dir, a.out_dir, a.max_retries + 1, not a.no_memory, a.reset_memory)
    except Exception as e:  # clear message for a missing key, bad JSON, etc.
        print(f"Run failed: {e}", file=sys.stderr)
        return 1
    print_summary(state, Path(a.out_dir))
    return 0 if state["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
