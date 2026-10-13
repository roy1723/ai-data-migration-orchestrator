"""Loading and small helpers shared by every agent.

Everything here is deterministic. Agents use these helpers for facts (types,
dependency order, which fields exist) so the LLM is never trusted for them.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

NUMERIC = {"INTEGER", "INT", "BIGINT", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "CURRENCY"}
TEXT = {"VARCHAR", "CHAR", "TEXT", "STRING", "EMAIL", "PHONE", "PICKLIST"}
DATE = {"DATE", "DATETIME", "TIMESTAMP"}
REFERENCE = {"ID", "LOOKUP"}


def type_category(type_name: str) -> str:
    """Map a source or target type name to a coarse category."""
    t = (type_name or "").upper()
    if t in NUMERIC:
        return "numeric"
    if t in TEXT:
        return "text"
    if t in DATE:
        return "date"
    if t in REFERENCE:
        return "reference"
    return "other"


def load_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_mapping(path: str | Path) -> list[dict]:
    """Read field_mapping.csv into a list of dicts with stripped strings."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]
    for i, row in enumerate(rows, start=1):
        row["row_id"] = i
    return rows


def load_inputs(data_dir: str | Path) -> dict:
    d = Path(data_dir)
    return {
        "source_schema": load_json(d / "source_schema.json"),
        "target_schema": load_json(d / "target_schema.json"),
        "mapping": load_mapping(d / "field_mapping.csv"),
        "sample_rows": load_json(d / "sample_rows.json") if (d / "sample_rows.json").exists() else {},
    }


def source_field(schema: dict, table: str, field: str) -> dict | None:
    return schema.get("tables", {}).get(table, {}).get("fields", {}).get(field)


def target_field(schema: dict, obj: str, field: str) -> dict | None:
    return schema.get("objects", {}).get(obj, {}).get("fields", {}).get(field)


def dependency_order(target_schema: dict) -> list[str]:
    """Parents first. Edges come from LOOKUP fields. Ties keep file order."""
    objects = list(target_schema.get("objects", {}))
    deps: dict[str, set[str]] = {o: set() for o in objects}
    for obj, meta in target_schema["objects"].items():
        for f in meta["fields"].values():
            ref = f.get("references")
            if ref and ref in deps and ref != obj:
                deps[obj].add(ref)
    order: list[str] = []
    remaining = list(objects)
    while remaining:
        ready = [o for o in remaining if deps[o] <= set(order)]
        if not ready:  # cycle: fall back to file order so the run still completes
            order.extend(remaining)
            break
        order.append(ready[0])
        remaining.remove(ready[0])
    return order


def qualified(obj: str, field: str) -> str:
    return f"{obj}.{field}"
