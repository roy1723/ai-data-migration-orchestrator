# Validation Report

- Generated: 2026-10-13 16:11
- LLM backend: groq (openai/gpt-oss-20b)
- SQL attempts: 2
- Result: **PASSED** (blocking SQL issues: 0, findings for review: 15)

The Validation Agent separates two kinds of issue. **Blocking SQL issues** are defects the SQL Generator can repair, so they trigger a re-prompt. **Findings** are mapping or data problems that no SQL change can fix; they are listed for the business and do not stop the run.

## Findings

| Severity | Code | Target | Message | Recommendation | Source |
| --- | --- | --- | --- | --- | --- |
| error | unknown_target_field | GiftTransaction.PaymentChannel | Mapping row 21 writes to GiftTransaction.PaymentChannel, which does not exist in the target schema. | Confirm the correct target field with the Salesforce admin. The field is skipped in the SQL. | analyst |
| error | required_unmapped | GiftTransaction.Status | GiftTransaction.Status is required in Salesforce but has no mapping. | Agree a default value or a source field. The pipeline records its assumption in the report. | analyst |
| warning | length_overflow | Campaign.Name | appeal.appeal_name allows 100 characters but Campaign.Name allows 80; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. | analyst |
| warning | length_overflow | Contact.Email | constituent.email allows 120 characters but Contact.Email allows 80; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. | analyst |
| warning | length_overflow | Contact.FirstName | constituent.first_name allows 50 characters but Contact.FirstName allows 40; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. | analyst |
| warning | length_overflow | GiftDesignation.Name | fund.fund_name allows 100 characters but GiftDesignation.Name allows 80; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. | analyst |
| warning | rows_dropped_by_join | GiftTransaction | 2 of 7 source rows for GiftTransaction were not loaded because a join found no matching parent record. | Review the unmatched source records and migrate or map their parents (for example, a different lookup for organisation donors). | dry_run |
| warning | semantic_review | GiftTransaction.CampaignId | Left join on Campaign may produce null CampaignId, potentially creating orphaned transactions. | Validate that CampaignId is required and enforce a lookup or error for missing campaigns. | llm |
| warning | semantic_review | GiftTransaction.GiftDesignationId | Lookup for GiftDesignation may fail if fund_id is missing, leading to null GiftDesignationId. | Add a check for missing GiftDesignation and log or flag such records. | llm |
| warning | type_semantic | GiftTransaction.OriginalAmount | gift_total is decimal(12,2), but mapped to Text in Salesforce (GiftTransaction.OriginalAmount). | Prefer a Currency or Number target. If text is intended, cast explicitly and keep two decimals. | analyst |
| warning | semantic_review | GiftTransaction.Status | Defaulting Status to 'Completed' may mask missing or incorrect status values. | Map Status from source or flag records with missing status for review. | llm |
| warning | type_conversion | GiftTransaction.TransactionDate | gift_date is varchar(10) text but GiftTransaction.TransactionDate is a Date; the value must be parsed, not cast. | Parse with the legacy format and reject or log rows that do not match. | analyst |
| info | semantic_review | GiftTransaction.DonorId | Lookup of DonorId via Contact may fail if the donor is an Organization, causing missing donor references. | Ensure donor lookup includes both Contact and Account or handle missing donors appropriately. | llm |
| info | semantic_review | GiftTransaction.TransactionDate | Parsing gift_date as MM/DD/YYYY assumes all dates follow this format; inconsistent formats could cause silent failures. | Validate date format before parsing or use a more robust date parser. | llm |
| info | unmapped_source_field | fund.fund_description | fund.fund_description is not mapped, so its data will not be migrated. | Confirm it is safe to drop. | analyst |

## Dry run on sample data (SQLite)

| Target | Rows inserted | Rows expected before joins | Error |
| --- | --- | --- | --- |
| Account | 2 | 2 |  |
| Contact | 3 | 3 |  |
| Campaign | 2 | 2 |  |
| GiftDesignation | 3 | 3 |  |
| GiftTransaction | 5 | 7 |  |

## Assumptions recorded by the Schema Analyst

- **GiftTransaction.Status**: No legacy source field exists; a default status of "Completed" is used to satisfy the required field. (default: 'Completed')

## Attempt history

- Attempt 1: failed with 4 blocking issue(s): date_parse_mismatch on GiftTransaction.TransactionDate; date_parse_mismatch on GiftTransaction.TransactionDate; date_parse_mismatch on GiftTransaction.TransactionDate; date_parse_mismatch on GiftTransaction.TransactionDate
- Attempt 2: passed all blocking checks.

## Checks performed

1. SQL parses (sqlglot, SQLite dialect) and is exactly one INSERT ... SELECT per statement
2. Every table and column used exists in the source or target schema
3. Every usable mapping row is produced; every required target field is set
4. Numeric-to-text values are cast; text-to-date values are parsed, not just cast
5. Statements follow the dependency order (parents before children)
6. Dry run on an in-memory SQLite copy with sample rows; source rows dropped by joins are counted
7. Parsed dates are replayed against sample rows and checked against the documented format, catching field-order bugs (e.g. day/month swapped) that a bare parse-function check would miss
8. LLM semantic review of the final candidate (findings must cite a real statement and column)
