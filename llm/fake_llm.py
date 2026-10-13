"""Offline stand-in for a real LLM. THIS IS A STUB, NOT A MODEL.

Why it exists: the project must run, and be testable, with no API key and no
network. The stub reads the same prompts the real model reads (each prompt ends
with a <task> tag and an <input> JSON block) and answers with rule-based
logic written for this case study's data.

It deliberately behaves like a naive model on the first attempt: the plan keeps
every row of the mapping sheet, including one that points at a field that does
not exist, and it forgets the default for a required field. That makes the
validator find real problems and exercises the retry loop. It also honours
memory hints, so a second run is clean.

Outputs generated with this backend are labelled as such. Use --llm gemini for
genuinely model-generated output.
"""
from __future__ import annotations

import json
import re

import sqlglot
from sqlglot import exp

from core.schema_utils import type_category

from .base import LLMClient


def _alias(table: str, used: set[str]) -> str:
    for n in range(1, len(table) + 1):
        a = table[:n].lower()
        if a not in used:
            return a
    return table.lower() + "1"


def _quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class FakeLLM(LLMClient):
    name = "fake"
    model = "rule-based-stub"

    def __init__(self, naive: bool = True):
        self.naive = naive
        self.calls: list[str] = []

    # ------------------------------------------------------------------ entry
    def complete(self, system: str, prompt: str, json_mode: bool = True) -> str:
        task = re.search(r"<task>(.*?)</task>", prompt).group(1).strip()
        body = re.search(r"<input>\s*(.*)\s*</input>", prompt, re.S).group(1)
        payload = json.loads(body)
        self.calls.append(task)
        handler = getattr(self, "_" + task.replace(".", "_"))
        return json.dumps(handler(payload))

    # ---------------------------------------------------------- schema analyst
    def _schema_analyst_brief(self, facts: dict) -> dict:
        findings = facts["findings"]
        ok_rows = [r for r in facts["mapping_status"] if r["status"] == "ok"]
        skipped = len(facts["mapping_status"]) - len(ok_rows)
        warnings = sum(1 for f in findings if f["severity"] == "warning")
        summary = (
            f"{len(facts['load_order'])} target objects load in the order {', '.join(facts['load_order'])}. "
            f"{len(ok_rows)} mapping rows are usable and {skipped} are skipped because they reference fields that do not exist. "
            f"{len(facts['required_unmapped'])} required field(s) have no mapping and {warnings} warning(s) need attention."
        )
        assumptions = []
        for q in facts["required_unmapped"]:
            default = "Completed" if q.endswith(".Status") else None
            assumptions.append({
                "target": q,
                "assumption": (f"Legacy data has no equivalent, so the fixed value '{default}' is used and flagged for business review."
                               if default else "No safe default exists; the field needs a business decision."),
                "default_value": default,
            })
        return {
            "summary": summary,
            "load_order": facts["load_order"],
            "risks": [{"target": f["target"], "risk": f["message"], "severity": f["severity"]}
                      for f in findings if f["severity"] in ("warning", "error")],
            "assumptions": assumptions,
        }

    # ------------------------------------------------------------ sql generator
    def _expression(self, row: dict, alias: str) -> str:
        src_field = row["source"].split(".")[1]
        src = f"{alias}.{src_field}"
        t = row["transformation"].lower()
        s_cat, t_cat = type_category(row["src_type"]), type_category(row["tgt_type"])
        if "parse date" in t:
            if "dd/mm/yyyy" in t:
                return f"DATE(SUBSTR({src}, 7, 4) || '-' || SUBSTR({src}, 4, 2) || '-' || SUBSTR({src}, 1, 2))"
            return f"DATE(SUBSTR({src}, 7, 4) || '-' || SUBSTR({src}, 1, 2) || '-' || SUBSTR({src}, 4, 2))"
        if "cast to text" in t:
            return f"CAST(ROUND({src}, 2) AS TEXT)" if "2 decimals" in t else f"CAST({src} AS TEXT)"
        if t.startswith("trim"):
            return f"TRIM({src})"
        if t.startswith("lower"):
            return f"LOWER({src})"
        if s_cat == "numeric" and t_cat == "text":
            return f"CAST({src} AS TEXT)"
        return src

    def _sql_generator_plan(self, inp: dict) -> dict:
        facts, brief, hints = inp["facts"], inp["brief"], inp.get("memory_hints") or []
        skip = {h["target"] for h in hints if h.get("action") == "skip_column"}
        hint_defaults = {h["target"]: h.get("value") for h in hints if h.get("action") == "use_default"}
        brief_defaults = {a["target"]: a["default_value"] for a in brief.get("assumptions", [])
                          if a.get("default_value") is not None}
        statements = []
        for obj in facts["load_order"]:
            rows = [r for r in facts["mapping_status"] if r["target"].split(".")[0] == obj]
            usable = [r for r in rows if r["status"] == "ok"
                      or (r["status"] == "unknown_target_field" and self.naive and r["target"] not in skip)]
            if not usable:
                continue
            tables = []
            for r in usable:
                tbl = r["source"].split(".")[0]
                if tbl not in tables:
                    tables.append(tbl)
            used: set[str] = set()
            alias_map = {}
            for tbl in tables:
                alias_map[tbl] = _alias(tbl, used)
                used.add(alias_map[tbl])
            main = tables[0]
            a = alias_map[main]
            notes = " ".join(r["notes"] for r in usable)
            filters = []
            for field, value in re.findall(r"(\w+) = '([^']+)'", notes):
                filters.append(f"{a}.{field} = '{value}'")
            if not filters:
                if "organizations only" in notes.lower():
                    filters.append(f"{a}.constituent_type = 'Organization'")
                elif "individuals only" in notes.lower():
                    filters.append(f"{a}.constituent_type = 'Individual'")
            joins, columns, skipped = [], [], []
            for r in usable:
                field = r["target"].split(".")[1]
                m = re.match(r"lookup (\w+) by (\w+)", r["transformation"], re.I)
                if m:
                    parent, key = m.groups()
                    ja = _alias(parent, used | {j["alias"] for j in joins})
                    src = f"{alias_map[r['source'].split('.')[0]]}.{r['source'].split('.')[1]}"
                    rhs = f"CAST({src} AS TEXT)" if type_category(r["src_type"]) == "numeric" else src
                    required = field in facts["targets"][obj]["required_fields"]
                    if not any(j["object"] == parent for j in joins):
                        joins.append({"object": parent, "alias": ja, "type": "INNER" if required else "LEFT",
                                      "on": f"{ja}.{key} = {rhs}"})
                    ja = next(j["alias"] for j in joins if j["object"] == parent)
                    expr = f"{ja}.Id"
                else:
                    expr = self._expression(r, alias_map[r["source"].split(".")[0]])
                reason = "mapping sheet" if r["status"] == "ok" else "taken from the mapping sheet as written"
                columns.append({"target_field": field, "expression": expr, "row_ids": [r["row_id"]], "reason": reason})
            if not self.naive:
                skipped = [{"row_id": r["row_id"], "target_field": r["target"].split(".")[1],
                            "reason": "target field does not exist"}
                           for r in rows if r["status"] == "unknown_target_field"]
            present = {c["target_field"] for c in columns}
            for req in facts["targets"][obj]["required_fields"]:
                q = f"{obj}.{req}"
                default = hint_defaults.get(q) or (brief_defaults.get(q) if not self.naive else None)
                if req not in present and default is not None:
                    columns.append({"target_field": req, "expression": _quote(default), "row_ids": [],
                                    "reason": "documented default for a required field"})
            statements.append({"target": obj, "source_tables": tables, "alias_map": alias_map,
                               "filters": filters, "joins": joins, "columns": columns, "skipped": skipped})
        return {"statements": statements}

    def _sql_generator_sql(self, inp: dict) -> dict:
        plan, brief, feedback = inp["plan"], inp["brief"], inp.get("feedback") or []
        defaults = {a["target"]: a["default_value"] for a in brief.get("assumptions", [])
                    if a.get("default_value") is not None}
        out = []
        for st in plan["statements"]:
            columns = [dict(c) for c in st["columns"]]
            for fb in feedback:
                obj, _, fld = fb["target"].partition(".")
                if obj != st["target"]:
                    continue
                if fb["code"] == "unknown_column":
                    columns = [c for c in columns if c["target_field"] != fld]
                elif fb["code"] == "required_missing" and not any(c["target_field"] == fld for c in columns):
                    columns.append({"target_field": fld, "expression": _quote(defaults.get(fb["target"], "Unknown")),
                                    "row_ids": [], "reason": "default"})
            main = st["source_tables"][0]
            sql = f"INSERT INTO {st['target']} ({', '.join(c['target_field'] for c in columns)})\n"
            sql += "SELECT " + ",\n       ".join(c["expression"] for c in columns)
            sql += f"\nFROM {main} {st['alias_map'][main]}"
            for j in st["joins"]:
                sql += f"\n{j['type']} JOIN {j['object']} {j['alias']} ON {j['on']}"
            if st["filters"]:
                sql += "\nWHERE " + " AND ".join(st["filters"])
            bits = []
            if st["filters"]:
                bits.append("filters rows with " + " and ".join(st["filters"]))
            if st["joins"]:
                bits.append("resolves lookups through " + ", ".join(j["object"] for j in st["joins"]))
            if any(not c["row_ids"] for c in columns):
                bits.append("fills required fields with documented defaults")
            out.append({"target": st["target"], "sql": sql,
                        "rationale": f"Loads {st['target']} from {', '.join(st['source_tables'])}"
                                     + (": " + "; ".join(bits) if bits else "") + "."})
        return {"statements": out}

    # ---------------------------------------------------------------- validator
    @staticmethod
    def _parse_insert(sql: str):
        tree = sqlglot.parse_one(sql, read="sqlite")
        cols = [c.name for c in tree.this.expressions]
        exprs = tree.expression.expressions
        return cols, exprs

    def _validator_review(self, inp: dict) -> dict:
        existing = {(i["code"], i["target"].split(".")[0]) for i in inp.get("static_issues", [])}
        findings = []
        for st in inp["statements"]:
            try:
                cols, exprs = self._parse_insert(st["sql"])
            except Exception:
                continue
            by_col = dict(zip(cols, exprs))
            inner = re.findall(r"INNER JOIN (\w+) (\w+)", st["sql"])
            for parent, alias in inner:
                if ("rows_dropped_by_join", st["target"]) in existing:
                    continue
                col = next((c for c, e in by_col.items() if f"{alias}.Id" in e.sql()), None)
                if col:
                    findings.append({"statement": st["target"], "column": col, "severity": "warning",
                                     "message": f"The INNER JOIN to {parent} drops any source record whose {parent} was not migrated first.",
                                     "suggestion": f"Reconcile counts after loading and migrate the missing parents first."})
            for c, e in by_col.items():
                text = e.sql()
                if text.startswith("DATE(") and "SUBSTR" in text:
                    findings.append({"statement": st["target"], "column": c, "severity": "info",
                                     "message": f"{c} assumes every legacy date is MM/DD/YYYY; a malformed value would become NULL instead of failing.",
                                     "suggestion": "Profile the legacy dates before loading and reject non-matching values."})
                if isinstance(e, exp.Literal) and e.is_string:
                    findings.append({"statement": st["target"], "column": c, "severity": "info",
                                     "message": f"{c} is filled with the fixed value {text}, which is an assumption and not legacy data.",
                                     "suggestion": "Have the business confirm the default before go-live."})
        return {"findings": findings[:5]}

    # ---------------------------------------------------------------- explainer
    def _explainer_statement(self, inp: dict) -> dict:
        target, sql = inp["target"], inp["sql"]
        cols, exprs = self._parse_insert(sql)
        rows = {r["target"].split(".")[1]: r for r in inp["mapping_rows"]}
        transformations = []
        for c, e in zip(cols, exprs):
            text, r = e.sql(), rows.get(c)
            src = r["source"] if r else None
            if isinstance(e, exp.Literal) and e.is_string:
                s = f"Sets {target}.{c} to the fixed value {text} for every record, because the legacy data has no equivalent."
            elif text.startswith("DATE("):
                s = f"Reads {src} as text in MM/DD/YYYY form and converts it to a real date for {target}.{c}."
            elif text.startswith("CAST(ROUND"):
                s = f"Rounds {src} to two decimals and stores it as text in {target}.{c}."
            elif text.startswith("CAST("):
                s = f"Converts {src} to text and stores it in {target}.{c}."
            elif text.startswith("TRIM("):
                s = f"Removes spaces around {src} and stores it in {target}.{c}."
            elif text.startswith("LOWER("):
                s = f"Converts {src} to lower case and stores it in {target}.{c}."
            elif re.match(r"\w+\.Id$", text) and r:
                s = f"Finds the matching record for {src} (by its legacy identifier) and stores its new Salesforce Id in {target}.{c}."
            else:
                s = f"Copies {src or text} into {target}.{c} without change."
            transformations.append({"field": c, "explanation": s})
        issues = inp.get("issues", [])
        assumptions = [i["message"] for i in issues if i["code"] in ("required_unmapped",)]
        assumptions += [t["explanation"] for t in transformations if "fixed value" in t["explanation"]]
        risks = [i["message"] for i in issues if i["severity"] in ("warning", "error")]
        tables = sorted(set(re.findall(r"(?:FROM|JOIN) (\w+)", sql)))
        where = re.search(r"WHERE (.*)", sql)
        summary = (f"Loads {target} using data from {', '.join(tables)}. "
                   + (f"Only records where {where.group(1)} are included. " if where else "All source records are included. ")
                   + f"The statement writes {len(cols)} fields.")
        return {"purpose": f"Migrates legacy data into the {target} object.", "summary": summary,
                "transformations": transformations, "assumptions": assumptions, "risks": risks}

    def _explainer_overview(self, inp: dict) -> dict:
        counts = ", ".join(f"{k}: {v}" for k, v in inp["row_counts"].items())
        status = ("All blocking checks passed" if inp["passed"] else "Some blocking checks still fail")
        return {"overview": (
            f"The script loads {len(inp['load_order'])} objects in this order: {', '.join(inp['load_order'])}. "
            f"Parents load before children so that lookups can be resolved. "
            f"The SQL needed {inp['attempts']} attempt(s). {status}. "
            f"A dry run on sample data produced these row counts: {counts}. "
            f"{len(inp['open_findings'])} finding(s) remain open for business review.")}
