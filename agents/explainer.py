"""Explainer Agent.

Writes a plain-English explanation of the final SQL for an auditor. One LLM call
per statement (purpose, per-column transformations, assumptions, risks) and one
for the executive overview. Code then checks coverage: every statement and every
column must be explained, and a missing sentence is filled with a neutral one
instead of being silently dropped. The audit tables are built from facts, not
from model output.
"""
from __future__ import annotations

from pathlib import Path

import sqlglot

from .base import BaseAgent
from .validator import SEVERITY_RANK, _table


def _insert_columns(sql: str) -> list[str]:
    try:
        return [c.name for c in sqlglot.parse_one(sql, read="sqlite").this.expressions]
    except Exception:
        return []


class ExplainerAgent(BaseAgent):
    name = "explainer"

    def run(self, state: dict) -> dict:
        facts, issues = state["facts"], state.get("issues", [])
        dry = state.get("dry_run", {})
        per_statement = []
        for st in state["statements"]:
            obj = st["target"]
            rows = [r for r in facts["mapping_status"] if r["target"].split(".")[0] == obj and r["status"] == "ok"]
            related = [i for i in issues if i["target"].split(".")[0] == obj]
            result = {}
            try:
                result = self.ask("explainer_statement", {
                    "target": obj, "sql": st["sql"], "mapping_rows": rows, "issues": related,
                    "dry_run": [r for r in dry.get("results", []) if r["target"] == obj],
                })
            except Exception as e:
                result = {"purpose": f"Migrates data into {obj}.", "summary": f"Explanation unavailable ({e}).",
                          "transformations": [], "assumptions": [], "risks": []}
            per_statement.append(self._cover(st, result))
        open_findings = [i for i in issues if i["class"] == "FINDING"]
        try:
            overview = self.ask("explainer_overview", {
                "load_order": facts["load_order"], "attempts": state.get("attempt", 1),
                "passed": state.get("passed", False), "row_counts": dry.get("target_row_counts", {}),
                "open_findings": [{"code": i["code"], "target": i["target"]} for i in open_findings],
            }).get("overview", "")
        except Exception as e:
            overview = f"Overview unavailable ({e})."
        explanation = {"overview": overview, "statements": per_statement}
        out_dir = (state.get("config") or {}).get("out_dir")
        if out_dir:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "sql_explanation.md").write_text(
                render_explanation(state, explanation, self.llm.describe()), encoding="utf-8")
        covered = sum(len(s["transformations"]) for s in per_statement)
        msg = self.message("memory_agent", "explanation_ready", {
            "statements": len(per_statement), "columns_explained": covered,
            "fallback_sentences": sum(s.get("fallbacks", 0) for s in per_statement),
        }, state.get("attempt", 0))
        return {"explanation": explanation, "messages": [msg]}

    @staticmethod
    def _cover(statement: dict, result: dict) -> dict:
        """Guarantee one sentence per inserted column, in column order."""
        cols = _insert_columns(statement["sql"])
        given = {t.get("field"): t.get("explanation", "") for t in result.get("transformations", []) if isinstance(t, dict)}
        out, fallbacks = [], 0
        for c in cols:
            text = given.get(c, "").strip()
            if not text:
                text, fallbacks = f"Populates {statement['target']}.{c} (no explanation was returned).", fallbacks + 1
            out.append({"field": c, "explanation": text})
        return {"target": statement["target"], "sql": statement["sql"],
                "purpose": result.get("purpose", ""), "summary": result.get("summary", ""),
                "transformations": out, "assumptions": list(result.get("assumptions", [])),
                "risks": list(result.get("risks", [])), "fallbacks": fallbacks}


def render_explanation(state: dict, explanation: dict, backend: str) -> str:
    facts = state["facts"]
    out = ["# SQL Explanation", "",
           f"LLM backend: {backend}. Written for an auditor or business stakeholder; no SQL knowledge is needed to read the prose.", "",
           "## Overview", "", explanation["overview"], "",
           "## Statement by statement", ""]
    for n, s in enumerate(explanation["statements"], start=1):
        out += [f"### {n}. {s['target']}", "", f"**Purpose.** {s['purpose']}", "", s["summary"], "",
                "**What happens to each field**", ""]
        out += [f"- `{t['field']}`: {t['explanation']}" for t in s["transformations"]]
        if s["assumptions"]:
            out += ["", "**Assumptions**", ""] + [f"- {a}" for a in s["assumptions"]]
        if s["risks"]:
            out += ["", "**Risks**", ""] + [f"- {r}" for r in s["risks"]]
        out += ["", "<details><summary>SQL</summary>", "", "```sql", s["sql"] + ";", "```", "", "</details>", ""]
    out += ["## Field-level audit table", "",
            _table(["Source field", "Target field", "Source type", "Target type", "Transformation in mapping sheet"],
                   [[r["source"], r["target"], r["src_type"], r["tgt_type"], r["transformation"]]
                    for r in facts["mapping_status"] if r["status"] == "ok"]), ""]
    skipped = [r for r in facts["mapping_status"] if r["status"] != "ok"]
    if skipped:
        out += ["### Mapping rows not migrated", "",
                _table(["Source field", "Target field", "Reason"],
                       [[r["source"], r["target"], r["status"].replace("_", " ")] for r in skipped]), ""]
    open_findings = sorted((i for i in state.get("issues", []) if i["class"] == "FINDING"),
                           key=lambda i: (SEVERITY_RANK.get(i["severity"], 3), i["target"]))
    out += ["## Open items for business review", ""]
    if open_findings:
        out.append(_table(["Severity", "Target", "Issue", "Recommendation"],
                          [[i["severity"], i["target"], i["message"], i["suggestion"]] for i in open_findings]))
    else:
        out.append("None.")
    out.append("")
    return "\n".join(out)
