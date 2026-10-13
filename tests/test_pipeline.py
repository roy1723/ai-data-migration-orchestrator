"""End to end: the retry loop, memory, explainer coverage and deliverables."""
import json

import sqlglot

from main import run_pipeline


def run(tmp_path, **kw):
    return run_pipeline("fake", out_dir=tmp_path / "out", memory_path=tmp_path / "memory.json", **kw)


def test_retry_loop_converges_on_second_attempt(tmp_path):
    state = run(tmp_path)
    assert state["passed"] and state["attempt"] == 2
    first = state["history"][0]
    assert not first["passed"]
    assert {f["code"] for f in first["fixable"]} == {"unknown_column", "required_missing"}


def test_findings_do_not_trigger_retries(tmp_path):
    state = run(tmp_path, max_attempts=1)
    assert not state["passed"] and state["attempt"] == 1  # no retries allowed, no loop either
    state = run(tmp_path, use_memory=False)
    assert state["attempt"] == 2  # the 12 findings did not cause extra attempts


def test_memory_removes_the_first_failure(tmp_path):
    run(tmp_path)  # learns
    state = run(tmp_path)  # recalls
    assert state["attempt"] == 1 and state["passed"]
    assert {h["action"] for h in state["memory_hints"]} == {"skip_column", "use_default"}


def test_no_memory_flag_ignores_store(tmp_path):
    run(tmp_path)
    assert run(tmp_path, use_memory=False)["attempt"] == 2


def test_max_attempts_stops_the_loop(tmp_path):
    state = run(tmp_path, max_attempts=1, use_memory=False)
    assert state["attempt"] == 1 and not state["passed"]
    assert "FAILED" in (tmp_path / "out" / "validation_report.md").read_text()


def test_all_deliverables_written_and_sql_parses(tmp_path):
    run(tmp_path)
    out = tmp_path / "out"
    for name in ["sample_output.sql", "validation_report.md", "sql_explanation.md", "agent_trace.jsonl"]:
        assert (out / name).stat().st_size > 0, name
    statements = [s for s in sqlglot.parse((out / "sample_output.sql").read_text(), read="sqlite") if s]
    assert len(statements) == 5 and all(s.key == "insert" for s in statements)


def test_explainer_covers_every_statement_and_column(tmp_path):
    state = run(tmp_path)
    text = (tmp_path / "out" / "sql_explanation.md").read_text()
    for s in state["explanation"]["statements"]:
        cols = [c.name for c in sqlglot.parse_one(s["sql"], read="sqlite").this.expressions]
        assert [t["field"] for t in s["transformations"]] == cols
        assert f"### " in text and s["target"] in text


def test_trace_records_the_conversation(tmp_path):
    run(tmp_path)
    msgs = [json.loads(l) for l in (tmp_path / "out" / "agent_trace.jsonl").read_text().splitlines()]
    senders = [m["from"] for m in msgs]
    assert senders[0] == "memory_agent" and senders[-1] == "memory_agent"
    assert senders.count("sql_generator") == 2 and senders.count("validator") == 2
    feedback = [m for m in msgs if m["kind"] == "validation_feedback"]
    assert feedback and feedback[0]["to"] == "sql_generator" and feedback[0]["payload"]["feedback"]


def test_report_has_the_pdf_example(tmp_path):
    run(tmp_path)
    assert "gift_total is decimal(12,2), but mapped to Text" in (tmp_path / "out" / "validation_report.md").read_text()
