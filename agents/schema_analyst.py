"""Schema Analyst Agent.

Reads the source and target schemas and the mapping sheet, extracts structure and
detects mismatches. Facts (types, lengths, unknown fields, dependency order) are
computed by code; the LLM then writes the analysis brief: priorities, risks and
documented assumptions for required fields that have no mapping.
"""
from __future__ import annotations

from core.analysis import analyze

from .base import BaseAgent


class SchemaAnalystAgent(BaseAgent):
    name = "schema_analyst"

    def run(self, state: dict) -> dict:
        facts = analyze(state["source_schema"], state["target_schema"], state["mapping"])
        try:
            brief = self.ask("schema_analyst_brief", {"facts": _compact(facts)})
        except Exception as e:  # keep the pipeline alive: fall back to facts only
            brief = {"summary": f"LLM brief unavailable ({e}); using deterministic facts only.",
                     "load_order": facts["load_order"], "risks": [], "assumptions": []}
        brief = self._reconcile(brief, facts)
        msg = self.message("sql_generator", "analysis_brief", {
            "load_order": facts["load_order"],
            "mapping_rows": len(facts["mapping_status"]),
            "usable_rows": sum(1 for r in facts["mapping_status"] if r["status"] == "ok"),
            "findings": len(facts["findings"]),
            "required_unmapped": facts["required_unmapped"],
            "assumptions": brief.get("assumptions", []),
            "summary": brief.get("summary", ""),
        })
        return {"facts": facts, "brief": brief, "messages": [msg]}

    @staticmethod
    def _reconcile(brief: dict, facts: dict) -> dict:
        """Deterministic facts win: the model may not change the load order or drop assumptions."""
        brief = dict(brief)
        if brief.get("load_order") != facts["load_order"]:
            brief["load_order"] = facts["load_order"]
            brief["reconciled"] = "load_order replaced by the deterministic dependency order"
        covered = {a.get("target") for a in brief.get("assumptions", [])}
        for q in facts["required_unmapped"]:
            if q not in covered:
                brief.setdefault("assumptions", []).append(
                    {"target": q, "assumption": "No assumption was supplied; a business decision is needed.",
                     "default_value": None})
        return brief


def _compact(facts: dict) -> dict:
    """Trim facts to what the LLM needs, keeping the prompt small."""
    return {
        "load_order": facts["load_order"],
        "required_unmapped": facts["required_unmapped"],
        "unmapped_source_fields": facts["unmapped_source_fields"],
        "mapping_status": facts["mapping_status"],
        "findings": facts["findings"],
    }
