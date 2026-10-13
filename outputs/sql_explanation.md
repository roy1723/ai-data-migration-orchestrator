# SQL Explanation

LLM backend: groq (openai/gpt-oss-20b). Written for an auditor or business stakeholder; no SQL knowledge is needed to read the prose.

## Overview

The migration script processed five entity types in the order Account, Contact, Campaign, GiftDesignation, and GiftTransaction, ensuring referential integrity by loading parent records before child records. One successful attempt was required, with 2 Accounts, 3 Contacts, 2 Campaigns, 3 GiftDesignations, and 5 GiftTransactions transferred. All rows were retained except for those dropped due to join mismatches in GiftTransaction. Unresolved issues include length overflows on Contact.FirstName, Contact.Email, Campaign.Name, and GiftDesignation.Name; type and semantic mismatches on GiftTransaction.OriginalAmount and GiftTransaction.TransactionDate; unknown and unmapped fields such as GiftTransaction.PaymentChannel, GiftTransaction.Status, fund.fund_description; and semantic reviews needed for GiftTransaction.CampaignId and GiftTransaction.PaymentMethod. These findings must be addressed before final validation. 

## Statement by statement

### 1. Account

**Purpose.** Migrates organization records from the constituent table into the Account object.

The statement copies each organization’s ID, name, and phone number into a new Account record. It converts the numeric ID to text, removes leading and trailing spaces from the name, and copies the phone number as is. Only rows where the constituent_type is 'Organization' are included.

**What happens to each field**

- `Legacy_Id__c`: Copies constituent_id from constituent and converts it to text.
- `Name`: Copies org_name from constituent and removes surrounding spaces.
- `Phone`: Copies phone from constituent without change.

**Assumptions**

- Only rows with constituent_type='Organization' are processed.
- The phone number format is acceptable for the PHONE field.

**Risks**

- If phone numbers contain non-numeric characters, they may not be stored correctly in the PHONE field.
- If org_name contains only spaces, the resulting Name will be empty.

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

The statement copies each fund’s ID and name into the GiftDesignation table. It converts the numeric ID to text and removes leading/trailing spaces from the name.

**What happens to each field**

- `Legacy_Id__c`: Copies fund.fund_id and casts it to text.
- `Name`: Copies fund.fund_name and removes surrounding spaces.

**Assumptions**

- All fund records are inserted; no filtering or joins are applied.

**Risks**

- fund.fund_name values longer than 80 characters may be truncated or rejected due to GiftDesignation.Name length limit.

<details><summary>SQL</summary>

```sql
INSERT INTO GiftDesignation (Legacy_Id__c, Name) SELECT CAST(f.fund_id AS TEXT), TRIM(f.fund_name) FROM fund f;
```

</details>

### 5. GiftTransaction

**Purpose.** Migrates data into GiftTransaction.

Explanation unavailable (Groq call failed after 3 attempts: HTTP 400: {"error":{"message":"Failed to generate JSON. Please adjust your prompt. See 'failed_generation' for more details.","type":"invalid_request_error","code":"json_validate_failed","failed_generation":"{\"purpose\":\"Migrates gift records into the GiftTransaction object.\",\"summary\":\"The statement copies each gift record, converting identifiers to text, formatting amounts and dates, and linking to related Contact, Campaign, and GiftDesignation records. It sets the status to \\\"Completed\\\" for all inserted transactions.\",\"transformations\":[{\"field\":\"Legacy_Id__c\",\"explanation\":\"Copies gift.gift_id as text into GiftTransaction.Legacy_Id__c.\"},{\"field\":\"DonorId\",\"explanation\":\"Looks up the Contact whose Legacy_Id__c matches gift.constituent_id and stores that Contact Id in GiftTransaction.DonorId.\"},{\"field\":\"OriginalAmount\",\"explanation\":\"Rounds gift.gift_total to two decimals, casts it to text, and stores it in GiftTransaction.OriginalAmount.\"},{\"field\":\"TransactionDate\",\"explanation\":\"Parses the MM/DD/YYYY string in gift.gift_date into a Date and stores it in GiftTransaction.TransactionDate.\"},{\"field\":\"PaymentMethod\",\"explanation\":\"Copies gift.payment_type directly into GiftTransaction.PaymentMethod.\"},{\"field\":\"CampaignId\",\"explanation\":\"Looks up the Campaign whose Legacy_Id__c matches gift.appeal_id and stores that Campaign Id in GiftTransaction.CampaignId, or leaves null if none.\"},{\"field\":\"GiftDesignationId\",\"explanation\":\"Looks up the GiftDesignation whose Legacy_Id__c matches gift.fund_id and stores that Id in GiftTransaction.GiftDesignationId.\"},{\"field\":\"Status\",\"explanation\":\"Sets the literal value \\\"Completed\\\" in GiftTransaction.Status for every inserted record.\"}],\"assumptions\":[\"Assumes every gift has a matching Contact record; otherwise the row is dropped.\"],\"risks\":[\"Rows may be dropped if no matching Contact, Campaign, or GiftDesignation is found; two source rows were dropped in the dry run.\"],\" }}"}}
).

**What happens to each field**

- `Legacy_Id__c`: Populates GiftTransaction.Legacy_Id__c (no explanation was returned).
- `DonorId`: Populates GiftTransaction.DonorId (no explanation was returned).
- `OriginalAmount`: Populates GiftTransaction.OriginalAmount (no explanation was returned).
- `TransactionDate`: Populates GiftTransaction.TransactionDate (no explanation was returned).
- `PaymentMethod`: Populates GiftTransaction.PaymentMethod (no explanation was returned).
- `CampaignId`: Populates GiftTransaction.CampaignId (no explanation was returned).
- `GiftDesignationId`: Populates GiftTransaction.GiftDesignationId (no explanation was returned).
- `Status`: Populates GiftTransaction.Status (no explanation was returned).

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
| warning | GiftTransaction.CampaignId | CampaignId is left joined and may be null for some gifts, potentially losing campaign context. | Add a check to ensure CampaignId is populated or handle nulls appropriately. |
| warning | GiftTransaction.OriginalAmount | gift_total is decimal(12,2), but mapped to Text in Salesforce (GiftTransaction.OriginalAmount). | Prefer a Currency or Number target. If text is intended, cast explicitly and keep two decimals. |
| warning | GiftTransaction.TransactionDate | gift_date is varchar(10) text but GiftTransaction.TransactionDate is a Date; the value must be parsed, not cast. | Parse with the legacy format and reject or log rows that do not match. |
| info | GiftTransaction.PaymentMethod | PaymentMethod values are copied directly; if the source contains unexpected codes, they may not map to the target system's enum. | Validate PaymentMethod against the target system's allowed values and map or flag unknown codes. |
| info | fund.fund_description | fund.fund_description is not mapped, so its data will not be migrated. | Confirm it is safe to drop. |
