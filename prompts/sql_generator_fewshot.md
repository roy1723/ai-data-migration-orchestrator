Example 1 (different domain). Mapping: customer.cust_id -> Person.External_Id__c (direct, integer to text), customer.full_name -> Person.Name (trim), customer.signup (text DD-MM-YYYY) -> Person.JoinDate (date), customer.city_id -> Person.CityId (lookup City by External_Id__c, optional). Person.Status is required with default 'Active'.

INSERT INTO Person (External_Id__c, Name, JoinDate, CityId, Status)
SELECT CAST(cu.cust_id AS TEXT),
       TRIM(cu.full_name),
       DATE(SUBSTR(cu.signup, 7, 4) || '-' || SUBSTR(cu.signup, 4, 2) || '-' || SUBSTR(cu.signup, 1, 2)),
       ci.Id,
       'Active'
FROM customer cu
LEFT JOIN City ci ON ci.External_Id__c = CAST(cu.city_id AS TEXT)

Example 2. Filtered load: invoice.vendor_flag = 'Y' rows only go to Vendor; a monetary amount is stored in a text target.

INSERT INTO Vendor (External_Id__c, Name, TotalText)
SELECT CAST(i.invoice_id AS TEXT), TRIM(i.vendor_name), CAST(ROUND(i.total, 2) AS TEXT)
FROM invoice i
WHERE i.vendor_flag = 'Y'
