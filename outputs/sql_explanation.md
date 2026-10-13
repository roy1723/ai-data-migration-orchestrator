# SQL Explanation

LLM backend: groq (openai/gpt-oss-20b). Written for an auditor or business stakeholder; no SQL knowledge is needed to read the prose.

## Overview

The migration script processed five tables in the order Account, Contact, Campaign, GiftDesignation, and GiftTransaction, ensuring referential integrity by loading parent records before child records. Two attempts were required to resolve initial data type and length issues, after which the script passed all automated checks. A total of 15 rows were migrated across the tables: 2 Accounts, 3 Contacts, 2 Campaigns, 3 GiftDesignations, and 5 GiftTransactions. Unresolved findings include length overflows on Contact.FirstName, Contact.Email, Campaign.Name, and GiftDesignation.Name; type and semantic mismatches on GiftTransaction.OriginalAmount and GiftTransaction.TransactionDate; unknown and unmapped fields such as GiftTransaction.PaymentChannel, GiftTransaction.Status, and fund.fund_description; and rows dropped due to join mismatches. These issues will need manual review before final deployment.

## Statement by statement

### 1. Account

**Purpose.** Migrates organization records from the constituent table into the Account object.

All rows where the constituent_type is 'Organization' are inserted into Account. The legacy ID is copied as text, the organization name is trimmed of surrounding spaces, and the phone number is copied unchanged.

**What happens to each field**

- `Legacy_Id__c`: Copies constituent_id from constituent as text into Account.Legacy_Id__c.
- `Name`: Copies org_name from constituent, removing leading and trailing spaces, into Account.Name.
- `Phone`: Copies phone from constituent unchanged into Account.Phone.

**Assumptions**

- Only rows with constituent_type='Organization' are processed.
- The CAST to TEXT preserves the numeric value of constituent_id.

**Risks**

- If org_name contains internal spaces, only outer spaces are removed; internal formatting is unchanged.
- Phone numbers are copied without validation; invalid formats may be stored.

<details><summary>SQL</summary>

```sql
INSERT INTO Account (Legacy_Id__c, Name, Phone) SELECT CAST(c.constituent_id AS TEXT), TRIM(c.org_name), c.phone FROM constituent c WHERE c.constituent_type='Organization';
```

</details>

### 2. Contact

**Purpose.** Migrates individual constituent records into the Contact table.

The statement copies data from the constituent table for individuals only, converting the ID to text, trimming first and last names, lowercasing the email, and copying the phone number. It inserts these values into the Contact table’s corresponding fields.

**What happens to each field**

- `Legacy_Id__c`: Copies constituent_id as text into Contact.Legacy_Id__c.
- `FirstName`: Copies first_name, removing surrounding spaces, into Contact.FirstName.
- `LastName`: Copies last_name, removing surrounding spaces, into Contact.LastName.
- `Email`: Copies email, converting all letters to lower case, into Contact.Email.
- `Phone`: Copies phone directly into Contact.Phone.

**Assumptions**

- Only rows where constituent_type is 'Individual' are processed.
- The source data fits within the target field lengths unless noted.

**Risks**

- FirstName values longer than 40 characters may be truncated or rejected.
- Email values longer than 80 characters may be truncated or rejected.

<details><summary>SQL</summary>

```sql
INSERT INTO Contact (Legacy_Id__c, FirstName, LastName, Email, Phone) SELECT CAST(c.constituent_id AS TEXT), TRIM(c.first_name), TRIM(c.last_name), LOWER(c.email), c.phone FROM constituent c WHERE c.constituent_type='Individual';
```

</details>

### 3. Campaign

**Purpose.** Migrates appeal records into Campaign records

The statement copies each appeal into a new Campaign, converting the appeal ID to text, trimming spaces from the appeal name, and copying the start date unchanged. Two records are expected to be inserted.

**What happens to each field**

- `Legacy_Id__c`: Copies appeal.appeal_id as text into Campaign.Legacy_Id__c.
- `Name`: Copies appeal.appeal_name into Campaign.Name after removing leading and trailing spaces.
- `StartDate`: Copies appeal.start_date into Campaign.StartDate unchanged.

**Assumptions**

- All appeal records are included; no filtering or joins are applied.

**Risks**

- Appeal names longer than 80 characters may be truncated or rejected due to Campaign.Name length limit.

<details><summary>SQL</summary>

```sql
INSERT INTO Campaign (Legacy_Id__c, Name, StartDate) SELECT CAST(a.appeal_id AS TEXT), TRIM(a.appeal_name), a.start_date FROM appeal a;
```

</details>

### 4. GiftDesignation

**Purpose.** Migrates fund records into GiftDesignation.

The statement copies each fund’s ID and name into the GiftDesignation table. It converts the numeric ID to text and removes any leading or trailing spaces from the name.

**What happens to each field**

- `Legacy_Id__c`: Copies fund.fund_id and casts it to text.
- `Name`: Copies fund.fund_name and removes surrounding spaces.

**Assumptions**

- All fund records are inserted; no filtering or joins are applied.

**Risks**

- GiftDesignation.Name may truncate values longer than 80 characters, potentially losing data.

<details><summary>SQL</summary>

```sql
INSERT INTO GiftDesignation (Legacy_Id__c, Name) SELECT CAST(f.fund_id AS TEXT), TRIM(f.fund_name) FROM fund f;
```

</details>

### 5. GiftTransaction

**Purpose.** Migrates gift records into Salesforce GiftTransaction objects

The statement copies data from the legacy gift table, converting and looking up related records, and inserts new GiftTransaction records with a default status of "Completed".

**What happens to each field**

- `Legacy_Id__c`: Copies gift.gift_id as text into GiftTransaction.Legacy_Id__c.
- `DonorId`: Looks up Contact.Id where Contact.Legacy_Id__c matches gift.constituent_id and stores that Id in GiftTransaction.DonorId.
- `OriginalAmount`: Rounds gift.gift_total to two decimals, casts to text, and stores in GiftTransaction.OriginalAmount.
- `TransactionDate`: Parses gift.gift_date from MM/DD/YYYY text into a Date and stores in GiftTransaction.TransactionDate.
- `PaymentMethod`: Copies gift.payment_type directly into GiftTransaction.PaymentMethod.
- `CampaignId`: Looks up Campaign.Id where Campaign.Legacy_Id__c matches gift.appeal_id and stores in GiftTransaction.CampaignId; may be null if no match.
- `GiftDesignationId`: Looks up GiftDesignation.Id where GiftDesignation.Legacy_Id__c matches gift.fund_id and stores in GiftTransaction.GiftDesignationId; may be null if no match.
- `Status`: Sets the value "Completed" for every inserted GiftTransaction record.

**Assumptions**

- gift_date is always in MM/DD/YYYY format
- gift.gift_total has at most two decimal places
- lookups on Contact, Campaign, and GiftDesignation use Legacy_Id__c
- all source rows have a matching Contact record

**Risks**

- Rows may be dropped if no matching Contact, Campaign, or GiftDesignation is found
- TransactionDate parsing may fail for non‑MM/DD/YYYY dates
- OriginalAmount stored as text may cause numeric issues
- CampaignId and GiftDesignationId can be null, creating orphaned records
- Status is hard‑coded to "Completed", potentially masking missing status data

<details><summary>SQL</summary>

```sql
INSERT INTO GiftTransaction (Legacy_Id__c, DonorId, OriginalAmount, TransactionDate, PaymentMethod, CampaignId, GiftDesignationId, Status) SELECT CAST(g.gift_id AS TEXT), c.Id, CAST(ROUND(g.gift_total,2) AS TEXT), DATE(SUBSTR(g.gift_date,7,4)||'-'||SUBSTR(g.gift_date,1,2)||'-'||SUBSTR(g.gift_date,4,2)), g.payment_type, a.Id, fd.Id, 'Completed' FROM gift g INNER JOIN Contact c ON c.Legacy_Id__c = CAST(g.constituent_id AS TEXT) LEFT JOIN Campaign a ON a.Legacy_Id__c = CAST(g.appeal_id AS TEXT) LEFT JOIN GiftDesignation fd ON fd.Legacy_Id__c = CAST(g.fund_id AS TEXT);
```

</details>

## Field-level audit table

| Source field | Target field | Source type | Target type | Transformation in mapping sheet |
| --- | --- | --- | --- | --- |
| constituent.constituent_id | Account.Legacy_Id__c | INTEGER | TEXT | direct |
| constituent.org_name | Account.Name | VARCHAR | TEXT | trim |
| constituent.phone | Account.Phone | VARCHAR | PHONE | direct |
| constituent.constituent_id | Contact.Legacy_Id__c | INTEGER | TEXT | direct |
| constituent.first_name | Contact.FirstName | VARCHAR | TEXT | trim |
| constituent.last_name | Contact.LastName | VARCHAR | TEXT | trim |
| constituent.email | Contact.Email | VARCHAR | EMAIL | lowercase |
| constituent.phone | Contact.Phone | VARCHAR | PHONE | direct |
| appeal.appeal_id | Campaign.Legacy_Id__c | INTEGER | TEXT | direct |
| appeal.appeal_name | Campaign.Name | VARCHAR | TEXT | trim |
| appeal.start_date | Campaign.StartDate | DATE | DATE | direct |
| fund.fund_id | GiftDesignation.Legacy_Id__c | INTEGER | TEXT | direct |
| fund.fund_name | GiftDesignation.Name | VARCHAR | TEXT | trim |
| gift.gift_id | GiftTransaction.Legacy_Id__c | INTEGER | TEXT | direct |
| gift.constituent_id | GiftTransaction.DonorId | INTEGER | LOOKUP | lookup Contact by Legacy_Id__c |
| gift.gift_total | GiftTransaction.OriginalAmount | DECIMAL | TEXT | cast to text with 2 decimals |
| gift.gift_date | GiftTransaction.TransactionDate | VARCHAR | DATE | parse date MM/DD/YYYY |
| gift.payment_type | GiftTransaction.PaymentMethod | VARCHAR | TEXT | direct |
| gift.appeal_id | GiftTransaction.CampaignId | INTEGER | LOOKUP | lookup Campaign by Legacy_Id__c |
| gift.fund_id | GiftTransaction.GiftDesignationId | INTEGER | LOOKUP | lookup GiftDesignation by Legacy_Id__c |

### Mapping rows not migrated

| Source field | Target field | Reason |
| --- | --- | --- |
| gift.payment_type | GiftTransaction.PaymentChannel | unknown target field |

## Open items for business review

| Severity | Target | Issue | Recommendation |
| --- | --- | --- | --- |
| error | GiftTransaction.PaymentChannel | Mapping row 21 writes to GiftTransaction.PaymentChannel, which does not exist in the target schema. | Confirm the correct target field with the Salesforce admin. The field is skipped in the SQL. |
| error | GiftTransaction.Status | GiftTransaction.Status is required in Salesforce but has no mapping. | Agree a default value or a source field. The pipeline records its assumption in the report. |
| warning | Campaign.Name | appeal.appeal_name allows 100 characters but Campaign.Name allows 80; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. |
| warning | Contact.Email | constituent.email allows 120 characters but Contact.Email allows 80; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. |
| warning | Contact.FirstName | constituent.first_name allows 50 characters but Contact.FirstName allows 40; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. |
| warning | GiftDesignation.Name | fund.fund_name allows 100 characters but GiftDesignation.Name allows 80; longer values will be truncated or rejected. | Profile the longest values, then shorten the source data or truncate deliberately. |
| warning | GiftTransaction | 2 of 7 source rows for GiftTransaction were not loaded because a join found no matching parent record. | Review the unmatched source records and migrate or map their parents (for example, a different lookup for organisation donors). |
| warning | GiftTransaction.CampaignId | Left join on Campaign may produce null CampaignId, potentially creating orphaned transactions. | Validate that CampaignId is required and enforce a lookup or error for missing campaigns. |
| warning | GiftTransaction.GiftDesignationId | Lookup for GiftDesignation may fail if fund_id is missing, leading to null GiftDesignationId. | Add a check for missing GiftDesignation and log or flag such records. |
| warning | GiftTransaction.OriginalAmount | gift_total is decimal(12,2), but mapped to Text in Salesforce (GiftTransaction.OriginalAmount). | Prefer a Currency or Number target. If text is intended, cast explicitly and keep two decimals. |
| warning | GiftTransaction.Status | Defaulting Status to 'Completed' may mask missing or incorrect status values. | Map Status from source or flag records with missing status for review. |
| warning | GiftTransaction.TransactionDate | gift_date is varchar(10) text but GiftTransaction.TransactionDate is a Date; the value must be parsed, not cast. | Parse with the legacy format and reject or log rows that do not match. |
| info | GiftTransaction.DonorId | Lookup of DonorId via Contact may fail if the donor is an Organization, causing missing donor references. | Ensure donor lookup includes both Contact and Account or handle missing donors appropriately. |
| info | GiftTransaction.TransactionDate | Parsing gift_date as MM/DD/YYYY assumes all dates follow this format; inconsistent formats could cause silent failures. | Validate date format before parsing or use a more robust date parser. |
| info | fund.fund_description | fund.fund_description is not mapped, so its data will not be migrated. | Confirm it is safe to drop. |
