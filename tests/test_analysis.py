"""The Schema Analyst's deterministic facts: every planted issue must be found."""


def codes(facts):
    return {(f["code"], f["target"]) for f in facts["findings"]}


def test_load_order_puts_parents_first(facts):
    order = facts["load_order"]
    assert order == ["Account", "Contact", "Campaign", "GiftDesignation", "GiftTransaction"]


def test_decimal_to_text_is_flagged(facts):
    msgs = [f["message"] for f in facts["findings"] if f["target"] == "GiftTransaction.OriginalAmount"]
    assert any("decimal(12,2), but mapped to Text" in m for m in msgs)


def test_text_to_date_needs_parse(facts):
    assert any(f["target"] == "GiftTransaction.TransactionDate" and "parsed" in f["message"] for f in facts["findings"])


def test_length_overflow_email(facts):
    assert any(f["target"] == "Contact.Email" and "120" in f["message"] and "80" in f["message"] for f in facts["findings"])


def test_required_field_without_mapping(facts):
    assert "GiftTransaction.Status" in facts["required_unmapped"]


def test_mapping_to_nonexistent_field(facts):
    row = [r for r in facts["mapping_status"] if r["target"] == "GiftTransaction.PaymentChannel"][0]
    assert row["status"] == "unknown_target_field"


def test_unmapped_source_field_is_informational(facts):
    f = [f for f in facts["findings"] if "fund_description" in f["message"]]
    assert f and f[0]["severity"] == "info"


def test_analysis_findings_never_trigger_retry(facts):
    assert all(f["class"] == "FINDING" for f in facts["findings"])
