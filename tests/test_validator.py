"""The Validator must reject bad SQL on its own, whatever the LLM wrote."""
from agents.validator import dry_run_issues, static_checks
from core.sqlite_runner import dry_run
from llm.fake_llm import FakeLLM
from agents import SchemaAnalystAgent, SQLGeneratorAgent


def good_statements(inputs):
    """Run the stub in non-naive mode to obtain a clean candidate."""
    llm = FakeLLM(naive=False)
    st = dict(inputs, attempt=0, history=[], messages=[], memory_hints=[])
    st.update(SchemaAnalystAgent(llm).run(st))
    st.update(SQLGeneratorAgent(llm).run(st))
    return st


def check(st, statements):
    return static_checks(statements, st["source_schema"], st["target_schema"], st["facts"], st["brief"])


def swap(st, target, sql):
    return [dict(s, sql=sql) if s["target"] == target else s for s in st["statements"]]


def test_clean_candidate_has_no_static_issues(inputs):
    st = good_statements(inputs)
    assert check(st, st["statements"]) == []


def test_drop_table_is_rejected(inputs):
    st = good_statements(inputs)
    issues = check(st, swap(st, "Account", "DROP TABLE Account"))
    assert "forbidden_statement" in {i["code"] for i in issues}


def test_unknown_column_is_rejected(inputs):
    st = good_statements(inputs)
    sql = "INSERT INTO Account (Legacy_Id__c, Name, Phone) SELECT CAST(c.constituent_id AS TEXT), TRIM(c.org_nme), c.phone FROM constituent c"
    codes = {i["code"] for i in check(st, swap(st, "Account", sql))}
    assert "unknown_source_column" in codes


def test_missing_mapped_column_is_rejected(inputs):
    st = good_statements(inputs)
    sql = "INSERT INTO Account (Legacy_Id__c, Name) SELECT CAST(c.constituent_id AS TEXT), TRIM(c.org_name) FROM constituent c"
    issues = check(st, swap(st, "Account", sql))
    assert any(i["code"] == "missing_mapped_column" and i["target"] == "Account.Phone" for i in issues)


def test_parse_error_is_rejected(inputs):
    st = good_statements(inputs)
    issues = check(st, swap(st, "Account", "INSERT INTO Account (Name SELECT FROM"))
    assert "parse_error" in {i["code"] for i in issues}


def test_missing_cast_and_date_parse(inputs):
    st = good_statements(inputs)
    gift = next(s for s in st["statements"] if s["target"] == "GiftTransaction")["sql"]
    bad = gift.replace("CAST(ROUND(g.gift_total, 2) AS TEXT)", "g.gift_total")
    bad = bad.replace("DATE(SUBSTR(g.gift_date, 7, 4) || '-' || SUBSTR(g.gift_date, 1, 2) || '-' || SUBSTR(g.gift_date, 4, 2))", "g.gift_date")
    codes = {i["code"] for i in check(st, swap(st, "GiftTransaction", bad))}
    assert {"missing_cast", "missing_date_parse"} <= codes


def test_required_field_missing(inputs):
    st = good_statements(inputs)
    gift = next(s for s in st["statements"] if s["target"] == "GiftTransaction")["sql"]
    bad = gift.replace(", Status)", ")").replace(",\n       'Completed'", "")
    assert "required_missing" in {i["code"] for i in check(st, swap(st, "GiftTransaction", bad))}


def test_wrong_order_is_rejected(inputs):
    st = good_statements(inputs)
    issues = check(st, list(reversed(st["statements"])))
    assert "order_violation" in {i["code"] for i in issues}


def test_dry_run_reports_rows_dropped_by_join(inputs):
    st = good_statements(inputs)
    result = dry_run(st["source_schema"], st["target_schema"], st["sample_rows"], st["statements"],
                     st["facts"]["mapping_status"])
    issues = dry_run_issues(result)
    drop = [i for i in issues if i["code"] == "rows_dropped_by_join"]
    assert drop and drop[0]["class"] == "FINDING" and "2 of 7" in drop[0]["message"]
    assert result["target_row_counts"]["GiftTransaction"] == 5


def test_dry_run_accepts_correctly_parsed_dates(inputs):
    """The clean candidate's date expression (YYYY-MM-DD) must raise no mismatch."""
    st = good_statements(inputs)
    result = dry_run(st["source_schema"], st["target_schema"], st["sample_rows"], st["statements"],
                     st["facts"]["mapping_status"])
    assert result["date_mismatches"] == []


def test_dry_run_catches_swapped_month_day_in_date_parsing(inputs):
    """A sqlglot-valid but wrong date expression (day/month swapped) must be flagged.

    sqlglot's own static check only confirms *a* parse function was used, not a bare
    CAST - it cannot tell 'YYYY-MM-DD' from 'YYYY-DD-MM' since both are syntactically
    a parsed date. This is exactly the bug an LLM can introduce by mixing up which
    SUBSTR offset is the month and which is the day.
    """
    st = good_statements(inputs)
    gift = next(s for s in st["statements"] if s["target"] == "GiftTransaction")["sql"]
    swapped = gift.replace(
        "DATE(SUBSTR(g.gift_date, 7, 4) || '-' || SUBSTR(g.gift_date, 1, 2) || '-' || SUBSTR(g.gift_date, 4, 2))",
        "DATE(SUBSTR(g.gift_date, 7, 4) || '-' || SUBSTR(g.gift_date, 4, 2) || '-' || SUBSTR(g.gift_date, 1, 2))")
    assert swapped != gift  # guard against the fixture's SQL text changing under us
    result = dry_run(st["source_schema"], st["target_schema"], st["sample_rows"],
                     swap(st, "GiftTransaction", swapped), st["facts"]["mapping_status"])
    issues = dry_run_issues(result)
    mismatches = [i for i in issues if i["code"] == "date_parse_mismatch"]
    assert mismatches and mismatches[0]["class"] == "FIXABLE"
    assert mismatches[0]["target"] == "GiftTransaction.TransactionDate"
