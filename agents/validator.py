"""Validation Agent.

Verifies data type compatibility and identifies errors or missing mappings.
Layers, cheapest first:
  1. Static checks with sqlglot: syntax, statement type, tables and columns exist,
     every mapped field is produced, required fields are set, casts and date
     parsing are present, load order is respected.
  2. Dry run on an in-memory SQLite copy of both schemas (only if step 1 is clean).
  3. LLM semantic review for problems code cannot see (only if steps 1 and 2 are clean).
Issues are classed FIXABLE (the SQL Generator can repair them, so they trigger a
re-prompt) or FINDING (data or mapping problems that belong in the report).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from core.analysis import make_issue
from core.schema_utils import type_category
from core.sqlite_runner import dry_run

from .base import BaseAgent

CHECKS = [
    "SQL parses (sqlglot, SQLite dialect) and is exactly one INSERT ... SELECT per statement",
    "Every table and column used exists in the source or target schema",
    "Every usable mapping row is produced; every required target field is set",
    "Numeric-to-text values are cast; text-to-date values are parsed, not just cast",
    "Statements follow the dependency order (parents before children)",
    "Dry run on an in-memory SQLite copy with sample rows; source rows dropped by joins are counted",
    "Parsed dates are replayed against sample rows and checked against the documented format, "
    "catching field-order bugs (e.g. day/month swapped) that a bare parse-function check would miss",
    "LLM semantic review of the final candidate (findings must cite a real statement and column)",
]


def _fixable(code: str, target: str, message: str, suggestion: str, source: str = "static") -> dict:
    return make_issue(code, "error", target, message, suggestion, cls="FIXABLE", source=source)


def static_checks(statements: list[dict], source: dict, target: dict, facts: dict, brief: dict) -> list[dict]:
    issues: list[dict] = []
    src_cols = {t.lower(): {f.lower() for f in m["fields"]} for t, m in source["tables"].items()}
    tgt_cols = {o.lower(): {f.lower() for f in m["fields"]} for o, m in target["objects"].items()}
    canon_tgt = {o.lower(): o for o in target["objects"]}
    load_order = facts["load_order"]
    defaults = {a["target"]: a.get("default_value") for a in brief.get("assumptions", [])}
    seen: list[str] = []

    for st in statements:
        obj_raw, sql = st["target"], st["sql"]
        obj = canon_tgt.get(obj_raw.lower())
        if obj is None:
            issues.append(_fixable("unknown_table", obj_raw, f"Statement targets {obj_raw}, which is not in the target schema.",
                                   "Use one of the target objects from the schema."))
            continue
        if obj in seen:
            issues.append(_fixable("duplicate_statement", obj, f"More than one statement loads {obj}.",
                                   "Write exactly one INSERT ... SELECT per target object."))
        seen.append(obj)
        try:
            trees = [t for t in sqlglot.parse(sql, read="sqlite") if t is not None]
        except ParseError as e:
            issues.append(_fixable("parse_error", obj, f"SQL does not parse: {str(e).splitlines()[0]}", "Fix the syntax."))
            continue
        if len(trees) != 1:
            issues.append(_fixable("multiple_statements", obj, "Each entry must contain exactly one statement.",
                                   "Remove extra statements and semicolons."))
            continue
        node = trees[0]
        if not isinstance(node, exp.Insert):
            issues.append(_fixable("forbidden_statement", obj,
                                   f"Only INSERT ... SELECT is allowed; found {type(node).__name__.upper()}.",
                                   "Replace it with an INSERT ... SELECT."))
            continue
        select = node.expression
        if not isinstance(select, exp.Select):
            issues.append(_fixable("not_insert_select", obj, "The INSERT must take its rows from a SELECT.",
                                   "Use INSERT INTO <object> (<columns>) SELECT ..."))
            continue
        schema_node = node.this
        if not isinstance(schema_node, exp.Schema) or not schema_node.expressions:
            issues.append(_fixable("missing_column_list", obj, "The INSERT does not list its columns.",
                                   "List the target columns explicitly."))
            continue
        columns = [c.name for c in schema_node.expressions]
        exprs = select.expressions
        if len(columns) != len(exprs):
            issues.append(_fixable("column_count_mismatch", obj,
                                   f"{len(columns)} columns but {len(exprs)} values in the SELECT.",
                                   "Make the column list and SELECT list the same length."))
            continue

        # tables and columns
        amap: dict[str, str] = {}
        for t in select.find_all(exp.Table):
            amap[(t.alias_or_name or t.name).lower()] = t.name
            if t.name.lower() not in src_cols and t.name.lower() not in tgt_cols:
                issues.append(_fixable("unknown_table", f"{obj}", f"{t.name} is not a source table or target object.",
                                       "Use only tables from the schemas."))
        for c in select.find_all(exp.Column):
            if c.name == "*":
                continue
            q = c.table.lower() if c.table else None
            real = amap.get(q).lower() if q and amap.get(q) else None
            pool = [real] if real else [t.lower() for t in amap.values()]
            exists = any(c.name.lower() in src_cols.get(p, set()) | tgt_cols.get(p, set()) for p in pool)
            if not exists:
                issues.append(_fixable("unknown_source_column", obj,
                                       f"Column {c.sql()} used in the SELECT does not exist.",
                                       "Use a column that exists in the schema."))
        for col in columns:
            if col.lower() not in tgt_cols[obj.lower()]:
                issues.append(_fixable("unknown_column", f"{obj}.{col}",
                                       f"{obj}.{col} does not exist in the target schema.",
                                       f"Remove {col} from the INSERT."))

        # mapping coverage
        produced = {c.lower() for c in columns}
        rows = [r for r in facts["mapping_status"] if r["status"] == "ok" and r["target"].split(".")[0] == obj]
        for r in rows:
            fld = r["target"].split(".")[1]
            if fld.lower() not in produced:
                issues.append(_fixable("missing_mapped_column", r["target"],
                                       f"{r['target']} is mapped from {r['source']} but the INSERT does not set it.",
                                       f"Add {fld} using {r['source']} ({r['transformation']})."))
        for fld, meta in target["objects"][obj]["fields"].items():
            if meta.get("required") and fld != "Id" and fld.lower() not in produced:
                d = defaults.get(f"{obj}.{fld}")
                issues.append(_fixable("required_missing", f"{obj}.{fld}",
                                       f"{obj}.{fld} is required in Salesforce but the statement does not set it.",
                                       f"Set it to the documented default {d!r}." if d is not None else "Set it from a source field."))

        # types
        by_col = dict(zip((c.lower() for c in columns), exprs))
        for r in rows:
            fld = r["target"].split(".")[1]
            e = by_col.get(fld.lower())
            if e is None or "lookup" in r["transformation"].lower() or fld.endswith("Legacy_Id__c") and type_category(r["src_type"]) != "numeric":
                continue
            s_cat, t_cat = type_category(r["src_type"]), type_category(r["tgt_type"])
            if s_cat == "numeric" and t_cat == "text" and not e.find(exp.Cast):
                issues.append(_fixable("missing_cast", r["target"],
                                       f"{r['source']} is numeric but {r['target']} is text and the SQL has no CAST.",
                                       f"Wrap the expression in CAST(... AS TEXT)."))
            if s_cat == "text" and t_cat == "date":
                bare = isinstance(e, exp.Column) or (isinstance(e, exp.Cast) and isinstance(e.this, exp.Column))
                if bare:
                    issues.append(_fixable("missing_date_parse", r["target"],
                                           f"{r['source']} is text but {r['target']} is a date; a bare column or CAST does not parse it.",
                                           "Parse the text with the legacy date format."))

        # joins: parents must be loaded earlier
        for t in select.find_all(exp.Table):
            ref = canon_tgt.get(t.name.lower())
            if ref and ref != obj and ref in load_order and obj in load_order:
                if load_order.index(ref) > load_order.index(obj):
                    issues.append(_fixable("order_violation", obj,
                                           f"{obj} reads {ref}, which loads after it.",
                                           f"Load {ref} before {obj}."))

    # statement order and completeness
    positions = [load_order.index(o) for o in seen if o in load_order]
    if positions != sorted(positions):
        issues.append(_fixable("order_violation", "script",
                               f"Statements are not in load order {' -> '.join(load_order)}.",
                               "Reorder the statements to follow the load order."))
    for obj in load_order:
        usable = any(r["status"] == "ok" and r["target"].split(".")[0] == obj for r in facts["mapping_status"])
        if usable and obj not in seen:
            issues.append(_fixable("missing_statement", obj, f"No statement loads {obj}.",
                                   f"Add an INSERT ... SELECT for {obj}."))
    return issues


def dry_run_issues(result: dict) -> list[dict]:
    issues = []
    for r in result["results"]:
        if r["error"]:
            issues.append(_fixable("execution_error", r["target"],
                                   f"Executing the {r['target']} statement on SQLite failed: {r['error']}",
                                   "Fix the statement so it runs.", source="dry_run"))
        elif r["expected_rows"] is not None and r["rows_inserted"] < r["expected_rows"]:
            dropped = r["expected_rows"] - r["rows_inserted"]
            issues.append(make_issue(
                "rows_dropped_by_join", "warning", r["target"],
                f"{dropped} of {r['expected_rows']} source rows for {r['target']} were not loaded because a join found no matching parent record.",
                "Review the unmatched source records and migrate or map their parents (for example, a different lookup for organisation donors).",
                source="dry_run"))
    for m in result.get("date_mismatches", []):
        issues.append(_fixable(
            "date_parse_mismatch", m["target"],
            f"{m['target']} should be {m['expected']} for {m['source']}={m['raw_value']!r} "
            f"(Legacy_Id__c {m['legacy_id']}), but the generated SQL produced {m['actual']!r}.",
            "Check the field order in the date-parsing expression against the documented source "
            "format exactly (for example a swapped day/month).", source="dry_run"))
    return issues


class ValidationAgent(BaseAgent):
    name = "validator"

    def run(self, state: dict) -> dict:
        attempt = state.get("attempt", 1)
        statements, facts, brief = state["statements"], state["facts"], state["brief"]
        issues = list(facts["findings"])  # deterministic findings from the analyst
        static = static_checks(statements, state["source_schema"], state["target_schema"], facts, brief)
        issues += static
        dry = {"skipped": "static errors must be fixed first"}
        if not static:
            dry = dry_run(state["source_schema"], state["target_schema"], state.get("sample_rows", {}), statements,
                          facts["mapping_status"])
            issues += dry_run_issues(dry)
        fixable = [i for i in issues if i["class"] == "FIXABLE"]
        if not fixable:
            issues += self._semantic_review(state, issues, dry)
            fixable = []
        passed = not fixable
        feedback = [{"code": i["code"], "target": i["target"], "message": i["message"], "fix": i["suggestion"]}
                    for i in fixable]
        history = [{"attempt": attempt, "passed": passed,
                    "fixable": [{"code": i["code"], "target": i["target"], "message": i["message"]} for i in fixable]}]
        update = {"issues": issues, "feedback": feedback, "passed": passed, "dry_run": dry, "history": history}
        merged = {**state, **update, "history": state.get("history", []) + history}
        out_dir = (state.get("config") or {}).get("out_dir")
        if out_dir:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "validation_report.md").write_text(render_report(merged, self.llm.describe()), encoding="utf-8")
        msg = self.message("sql_generator" if not passed else "explainer",
                           "validation_feedback" if not passed else "validation_passed", {
                               "passed": passed,
                               "fixable": len(fixable),
                               "findings": sum(1 for i in issues if i["class"] == "FINDING"),
                               "feedback": feedback,
                           }, attempt)
        return {**update, "messages": [msg]}

    def _semantic_review(self, state: dict, issues: list[dict], dry: dict) -> list[dict]:
        try:
            reply = self.ask("validator_review", {
                "statements": [{"target": s["target"], "sql": s["sql"]} for s in state["statements"]],
                "mapping_rows": [r for r in state["facts"]["mapping_status"] if r["status"] == "ok"],
                "static_issues": [{"code": i["code"], "target": i["target"]} for i in issues],
                "dry_run": dry,
            })
        except Exception:
            return []
        valid = {s["target"]: _columns_of(s["sql"]) for s in state["statements"]}
        out = []
        for f in reply.get("findings", [])[:5]:
            stmt, col = f.get("statement"), f.get("column")
            if stmt in valid and col in valid[stmt]:  # grounding: drop anything that cites a non-existent column
                out.append(make_issue("semantic_review", f.get("severity", "info"), f"{stmt}.{col}",
                                      f.get("message", ""), f.get("suggestion", ""), source="llm"))
        return out


def _columns_of(sql: str) -> set[str]:
    try:
        node = sqlglot.parse_one(sql, read="sqlite")
        return {c.name for c in node.this.expressions}
    except Exception:
        return set()


# ----------------------------------------------------------------------- report
SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def _table(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def render_report(state: dict, backend: str) -> str:
    issues = state.get("issues", [])
    passed = state.get("passed", False)
    attempt = state.get("attempt", 1)
    blocking = [i for i in issues if i["class"] == "FIXABLE"]
    findings = sorted((i for i in issues if i["class"] == "FINDING"),
                      key=lambda i: (SEVERITY_RANK.get(i["severity"], 3), i["target"]))
    out = ["# Validation Report", ""]
    out.append(f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append(f"- LLM backend: {backend}")
    out.append(f"- SQL attempts: {attempt}")
    out.append(f"- Result: **{'PASSED' if passed else 'FAILED'}** (blocking SQL issues: {len(blocking)}, findings for review: {len(findings)})")
    out.append("")
    out.append("The Validation Agent separates two kinds of issue. **Blocking SQL issues** are defects the SQL Generator can repair, so they trigger a re-prompt. **Findings** are mapping or data problems that no SQL change can fix; they are listed for the business and do not stop the run.")
    out.append("")
    if blocking:
        out += ["## Blocking SQL issues (unresolved)", "",
                _table(["Code", "Target", "Message", "Suggested fix"],
                       [[i["code"], i["target"], i["message"], i["suggestion"]] for i in blocking]), ""]
    out += ["## Findings", ""]
    if findings:
        out.append(_table(["Severity", "Code", "Target", "Message", "Recommendation", "Source"],
                          [[i["severity"], i["code"], i["target"], i["message"], i["suggestion"], i["source"]] for i in findings]))
    else:
        out.append("No findings.")
    out += ["", "## Dry run on sample data (SQLite)", ""]
    dry = state.get("dry_run", {})
    if "results" in dry:
        out.append(_table(["Target", "Rows inserted", "Rows expected before joins", "Error"],
                          [[r["target"], r["rows_inserted"], "n/a" if r["expected_rows"] is None else r["expected_rows"], r["error"] or ""]
                           for r in dry["results"]]))
    else:
        out.append(f"Skipped: {dry.get('skipped', 'not run')}.")
    out += ["", "## Assumptions recorded by the Schema Analyst", ""]
    assumptions = state.get("brief", {}).get("assumptions", [])
    if assumptions:
        out += [f"- **{a['target']}**: {a['assumption']} (default: {a.get('default_value')!r})" for a in assumptions]
    else:
        out.append("None.")
    out += ["", "## Attempt history", ""]
    for h in state.get("history", []):
        if h["fixable"]:
            out.append(f"- Attempt {h['attempt']}: failed with {len(h['fixable'])} blocking issue(s): "
                       + "; ".join(f"{f['code']} on {f['target']}" for f in h["fixable"]))
        else:
            out.append(f"- Attempt {h['attempt']}: passed all blocking checks.")
    out += ["", "## Checks performed", ""] + [f"{n}. {c}" for n, c in enumerate(CHECKS, start=1)] + [""]
    return "\n".join(out)
