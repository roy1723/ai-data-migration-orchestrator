"""Deterministic schema analysis.

The Schema Analyst agent starts from these facts and only then asks the LLM to
interpret them. Keeping facts out of the model means type mismatches, unknown
fields and load order are always correct, whatever model is plugged in.
"""
from __future__ import annotations

from .schema_utils import (
    dependency_order,
    qualified,
    source_field,
    target_field,
    type_category,
)


def make_issue(code, severity, target, message, suggestion="", cls="FINDING", source="analyst"):
    """One validation issue.

    cls is FIXABLE when regenerating the SQL can resolve it, FINDING when it is a
    data or mapping problem that only belongs in the report.
    """
    return {
        "code": code,
        "class": cls,
        "severity": severity,
        "target": target,
        "message": message,
        "suggestion": suggestion,
        "source": source,
    }


def _describe_source_type(meta: dict) -> str:
    t = meta["type"].lower()
    if meta.get("precision"):
        return f"{t}({meta['precision']},{meta.get('scale', 0)})"
    if meta.get("length"):
        return f"{t}({meta['length']})"
    return t


def analyze(source: dict, target: dict, mapping: list[dict]) -> dict:
    """Return load order, per-row mapping status and deterministic findings."""
    findings: list[dict] = []
    rows: list[dict] = []
    mapped_targets: set[str] = set()
    mapped_sources: set[str] = set()

    for row in mapping:
        s_tbl, s_fld = row["source_table"], row["source_field"]
        t_obj, t_fld = row["target_object"], row["target_field"]
        s_meta = source_field(source, s_tbl, s_fld)
        t_meta = target_field(target, t_obj, t_fld)
        entry = {
            "row_id": row["row_id"],
            "source": f"{s_tbl}.{s_fld}",
            "target": qualified(t_obj, t_fld),
            "transformation": row["transformation"],
            "notes": row["notes"],
            "status": "ok",
            "src_type": s_meta["type"] if s_meta else None,
            "tgt_type": t_meta["type"] if t_meta else None,
            "src_length": (s_meta or {}).get("length"),
            "tgt_length": (t_meta or {}).get("length"),
        }
        if s_meta is None:
            entry["status"] = "unknown_source_field"
            findings.append(make_issue(
                "unknown_source_field", "error", entry["source"],
                f"Mapping row {row['row_id']} reads {entry['source']}, which is not in the source schema.",
                "Correct the mapping row or add the field to the source schema."))
        elif t_meta is None:
            entry["status"] = "unknown_target_field"
            findings.append(make_issue(
                "unknown_target_field", "error", entry["target"],
                f"Mapping row {row['row_id']} writes to {entry['target']}, which does not exist in the target schema.",
                "Confirm the correct target field with the Salesforce admin. The field is skipped in the SQL."))
        else:
            mapped_targets.add(entry["target"])
            mapped_sources.add(entry["source"])
            is_lookup = "lookup" in row["transformation"].lower()
            s_cat, t_cat = type_category(s_meta["type"]), type_category(t_meta["type"])
            if not is_lookup and not t_fld.endswith("Legacy_Id__c"):
                if s_cat == "numeric" and t_cat == "text":
                    findings.append(make_issue(
                        "type_semantic", "warning", entry["target"],
                        f"{s_fld} is {_describe_source_type(s_meta)}, but mapped to {t_meta['type'].capitalize()} in Salesforce ({entry['target']}).",
                        "Prefer a Currency or Number target. If text is intended, cast explicitly and keep two decimals."))
                elif s_cat == "text" and t_cat == "date":
                    findings.append(make_issue(
                        "type_conversion", "warning", entry["target"],
                        f"{s_fld} is {_describe_source_type(s_meta)} text but {entry['target']} is a Date; the value must be parsed, not cast.",
                        "Parse with the legacy format and reject or log rows that do not match."))
                elif s_cat != t_cat and not (s_cat == "text" and t_cat == "text"):
                    findings.append(make_issue(
                        "type_mismatch", "warning", entry["target"],
                        f"{s_fld} ({s_meta['type'].lower()}) does not match {entry['target']} ({t_meta['type'].lower()}).",
                        "Add an explicit conversion."))
            if s_cat == "text" and t_cat == "text" and s_meta.get("length") and t_meta.get("length"):
                if s_meta["length"] > t_meta["length"]:
                    findings.append(make_issue(
                        "length_overflow", "warning", entry["target"],
                        f"{entry['source']} allows {s_meta['length']} characters but {entry['target']} allows {t_meta['length']}; longer values will be truncated or rejected.",
                        "Profile the longest values, then shorten the source data or truncate deliberately."))
        rows.append(entry)

    # Required target fields (other than Id) with no valid mapping.
    required_unmapped: list[str] = []
    for obj, meta in target["objects"].items():
        for fname, fmeta in meta["fields"].items():
            q = qualified(obj, fname)
            if fmeta.get("required") and fname != "Id" and q not in mapped_targets:
                required_unmapped.append(q)
                findings.append(make_issue(
                    "required_unmapped", "error", q,
                    f"{q} is required in Salesforce but has no mapping.",
                    "Agree a default value or a source field. The pipeline records its assumption in the report."))

    # Source fields that nothing maps and nothing filters on.
    all_notes = " ".join(r["notes"] for r in mapping)
    unmapped_source: list[str] = []
    for tbl, tmeta in source["tables"].items():
        for fname in tmeta["fields"]:
            q = qualified(tbl, fname)
            if q not in mapped_sources and fname not in all_notes:
                unmapped_source.append(q)
                findings.append(make_issue(
                    "unmapped_source_field", "info", q,
                    f"{q} is not mapped, so its data will not be migrated.",
                    "Confirm it is safe to drop."))

    by_target: dict[str, dict] = {}
    for r in rows:
        if r["status"] != "ok":
            continue
        obj = r["target"].split(".")[0]
        by_target.setdefault(obj, {"source_tables": [], "mapped_fields": []})
        tbl = r["source"].split(".")[0]
        if tbl not in by_target[obj]["source_tables"]:
            by_target[obj]["source_tables"].append(tbl)
        by_target[obj]["mapped_fields"].append(r["target"].split(".")[1])
    for obj, meta in target["objects"].items():
        by_target.setdefault(obj, {"source_tables": [], "mapped_fields": []})
        by_target[obj]["required_fields"] = [f for f, m in meta["fields"].items() if m.get("required") and f != "Id"]

    return {
        "load_order": dependency_order(target),
        "mapping_status": rows,
        "findings": findings,
        "required_unmapped": required_unmapped,
        "unmapped_source_fields": unmapped_source,
        "targets": by_target,
    }
