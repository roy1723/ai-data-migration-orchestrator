"""Dry-run the generated SQL on an in-memory SQLite copy of both schemas.

No Salesforce or Raiser's Edge access is needed. The source tables are seeded
from data/sample_rows.json, the target tables start empty, and each statement
is executed in dependency order. This proves the SQL runs and lets the
Validator reconcile row counts.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

import sqlglot
from sqlglot import exp

from .schema_utils import type_category

_DATE_PATTERN = re.compile(r"(YYYY|MM|DD)([/\-.])(YYYY|MM|DD)\2(YYYY|MM|DD)")
_DATE_TOKEN_FMT = {"YYYY": "%Y", "MM": "%m", "DD": "%d"}


def _date_format_from_text(text: str) -> str | None:
    """Turn a mapping note like 'parse date MM/DD/YYYY' into a strptime format.

    Returns None if the note does not spell out all three of YYYY, MM, DD once
    each with a consistent separator, which is the only case we can check.
    """
    m = _DATE_PATTERN.search((text or "").upper())
    if not m:
        return None
    sep = m.group(2)
    return sep.join(_DATE_TOKEN_FMT[t] for t in (m.group(1), m.group(3), m.group(4)))


def check_date_parsing(conn: sqlite3.Connection, mapping_status: list[dict], sample_rows: dict) -> list[dict]:
    """Cross-check every text-to-date mapping against sample rows with a known format.

    sqlglot's static check only confirms a parse function was used, not a bare
    cast - it cannot tell a correct 'YYYY-MM-DD' from a 'YYYY-DD-MM' built from
    swapped substring positions, since both are syntactically a parsed date.
    This replays each sample row's source date through the documented format
    (from the mapping notes) and compares it to what actually landed in the
    target table, catching field-order bugs the LLM may have introduced.
    """
    by_target_obj: dict[str, dict] = {}
    for row in mapping_status:
        if row["status"] == "ok":
            obj, fld = row["target"].split(".", 1)
            by_target_obj.setdefault(obj, {})[fld] = row

    mismatches = []
    for row in mapping_status:
        if row["status"] != "ok":
            continue
        if type_category(row.get("src_type")) != "text" or type_category(row.get("tgt_type")) != "date":
            continue
        fmt = _date_format_from_text(f"{row['transformation']} {row.get('notes') or ''}")
        if fmt is None:
            continue
        obj, fld = row["target"].split(".", 1)
        id_row = by_target_obj.get(obj, {}).get("Legacy_Id__c")
        if id_row is None:
            continue
        src_table, src_id_field = id_row["source"].split(".", 1)
        _, src_date_field = row["source"].split(".", 1)
        for rec in sample_rows.get(src_table, []):
            raw_date = rec.get(src_date_field)
            if not raw_date:
                continue
            try:
                expected = datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue  # unparseable with the documented format is a separate, already-reported issue
            legacy_id = str(rec[src_id_field])
            try:
                found = conn.execute(
                    f'SELECT "{fld}" FROM "{obj}" WHERE "Legacy_Id__c" = ?', (legacy_id,)
                ).fetchone()
            except sqlite3.Error:
                continue
            if found is None:
                continue  # row not inserted (e.g. dropped by a join); unrelated to date parsing
            actual = found[0]
            if actual != expected:
                mismatches.append({
                    "target": f"{obj}.{fld}", "source": row["source"], "legacy_id": legacy_id,
                    "raw_value": raw_date, "expected": expected, "actual": actual,
                })
    return mismatches


def _sqlite_type(meta: dict) -> str:
    cat = type_category(meta["type"])
    return {"numeric": "NUMERIC", "text": "TEXT", "date": "TEXT", "reference": "INTEGER"}.get(cat, "TEXT")


def build_db(source: dict, target: dict, sample_rows: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for table, meta in source["tables"].items():
        cols = []
        for fname, f in meta["fields"].items():
            col = f'"{fname}" {_sqlite_type(f)}'
            if not f.get("nullable", True):
                col += " NOT NULL"
            cols.append(col)
        conn.execute(f'CREATE TABLE "{table}" ({", ".join(cols)})')
        for r in sample_rows.get(table, []):
            names = ", ".join(f'"{k}"' for k in r)
            marks = ", ".join("?" for _ in r)
            conn.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', list(r.values()))
    for obj, meta in target["objects"].items():
        cols = []
        for fname, f in meta["fields"].items():
            if fname == "Id":
                cols.append('"Id" INTEGER PRIMARY KEY AUTOINCREMENT')
                continue
            col = f'"{fname}" {_sqlite_type(f)}'
            if f.get("required"):
                col += " NOT NULL"
            cols.append(col)
        conn.execute(f'CREATE TABLE "{obj}" ({", ".join(cols)})')
    conn.commit()
    return conn


def _expected_rows_without_joins(conn: sqlite3.Connection, sql: str) -> int | None:
    """Count the rows the SELECT would return if its joins did not filter anything.

    The gap between this number and the rows actually inserted is the number of
    source rows dropped by INNER JOINs (for example, gifts whose donor was not
    migrated). Returns None if the statement is not a simple INSERT ... SELECT.
    """
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
        select = tree.expression
        if not isinstance(select, exp.Select):
            return None
        select = select.copy()
        select.set("joins", None)
        select.set("expressions", [exp.Count(this=exp.Star())])
        select.set("order", None)
        return conn.execute(select.sql(dialect="sqlite")).fetchone()[0]
    except Exception:
        return None


def dry_run(source: dict, target: dict, sample_rows: dict, statements: list[dict],
            mapping_status: list[dict] | None = None) -> dict:
    """Execute every statement and report rows inserted, errors and expected counts.

    If mapping_status is given, also replays every text-to-date mapping's sample
    rows through its documented format and compares against what actually landed
    in the target table (see check_date_parsing).
    """
    conn = build_db(source, target, sample_rows)
    results = []
    for st in statements:
        entry = {"target": st["target"], "rows_inserted": 0, "expected_rows": None, "error": None}
        entry["expected_rows"] = _expected_rows_without_joins(conn, st["sql"])
        try:
            before = conn.total_changes
            conn.execute(st["sql"])
            conn.commit()
            entry["rows_inserted"] = conn.total_changes - before
        except sqlite3.Error as e:
            conn.rollback()
            entry["error"] = str(e)
        results.append(entry)
    counts = {obj: conn.execute(f'SELECT COUNT(*) FROM "{obj}"').fetchone()[0] for obj in target["objects"]}
    date_mismatches = check_date_parsing(conn, mapping_status, sample_rows) if mapping_status else []
    conn.close()
    return {"results": results, "target_row_counts": counts, "date_mismatches": date_mismatches}
