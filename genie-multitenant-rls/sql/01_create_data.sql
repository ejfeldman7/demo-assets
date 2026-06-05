-- Multi-Tenant Genie RLS demo — synthetic multi-tenant accounting (GL) data
-- demos.genie_rls | 3 firms (tenants), firm_id-tagged.
-- Each firm has DISTINCT clients and a DISTINCT vendor mix (minor intentional overlap);
-- the chart of accounts is shared (realistic — every firm uses a standard CoA).

CREATE CATALOG IF NOT EXISTS demos;
CREATE SCHEMA IF NOT EXISTS demos.genie_rls;

-- ---------- firms (tenants) ----------
CREATE OR REPLACE TABLE demos.genie_rls.firms (
  firm_id   STRING,
  firm_name STRING,
  segment   STRING,
  region    STRING
) COMMENT 'Accounting firms = tenants for the multi-tenant demo';

INSERT INTO demos.genie_rls.firms VALUES
  ('firm_001','Harbor & Vale CPA','Mid-market','US-East'),
  ('firm_002','Summit Ledger Partners','SMB','US-West'),
  ('firm_003','Cedar Creek Accounting','Mid-market','US-Central');

-- ---------- clients (8 distinct per firm; minor overlap across firms) ----------
CREATE OR REPLACE TABLE demos.genie_rls.clients AS
WITH firm_clients AS (
  SELECT 'firm_001' AS firm_id, array(
    'Acme Manufacturing','Bluewave Logistics','Cypress Retail Group','Delta Health Partners',
    'Evergreen Foods','Foxtrot Media','Granite Construction','Harbor Imports') AS names
  UNION ALL SELECT 'firm_002', array(
    'Ironwood Brewing','Jetstream Aviation','Kestrel Apparel','Lumen Clinics',
    'Meridian Freight','Northstar Realty','Cypress Retail Group','Pinnacle Foods')
  UNION ALL SELECT 'firm_003', array(
    'Quartz Media','Redwood Builders','Summit Outdoors','Talon Security',
    'Umbra Design','Vertex Labs','Willow Hospitality','Delta Health Partners')
)
SELECT
  concat(fc.firm_id, '_c', lpad(cast(p.pos + 1 AS string), 3, '0')) AS client_id,
  fc.firm_id,
  p.name AS client_name,
  element_at(array('Manufacturing','Logistics','Retail','Healthcare',
                   'Food & Bev','Media','Construction','Real Estate'), p.pos + 1) AS industry
FROM firm_clients fc
LATERAL VIEW posexplode(fc.names) p AS pos, name;

-- ---------- gl_transactions (~250/firm; shared CoA, per-firm vendor mix, firm's clients) ----------
CREATE OR REPLACE TABLE demos.genie_rls.gl_transactions AS
WITH firm_vendors AS (
  SELECT 'firm_001' AS firm_id, array('ADP Payroll','AWS','Verizon','Stripe','WeWork','Staples','Delta Airlines') AS v
  UNION ALL SELECT 'firm_002', array('ADP Payroll','AWS','Verizon','QuickBooks','Slack','United Airlines','Office Depot')
  UNION ALL SELECT 'firm_003', array('ADP Payroll','AWS','Verizon','Bill.com','Zoom','Southwest Airlines','Brex')
)
SELECT
  concat(f.firm_id, '_t', lpad(cast(s.n AS string), 5, '0'))                            AS txn_id,
  f.firm_id,
  date_add(DATE'2025-06-01', cast(rand()*365 AS int))                                   AS txn_date,
  acct.code   AS account_code,
  acct.name   AS account_name,
  acct.type   AS account_type,
  round(rand()*9000 + 100, 2)                                                           AS amount,
  CASE WHEN acct.type IN ('Revenue','Liability') THEN 'CREDIT' ELSE 'DEBIT' END         AS debit_credit,
  element_at(fv.v, cast(rand()*size(fv.v) AS int) + 1)                                  AS vendor,
  concat(f.firm_id, '_c', lpad(cast(rand()*8 AS int) + 1, 3, '0'))                       AS client_id,
  concat('Entry for ', acct.name)                                                       AS memo
FROM demos.genie_rls.firms f
JOIN firm_vendors fv ON fv.firm_id = f.firm_id
LATERAL VIEW explode(sequence(1, 250)) s AS n
LATERAL VIEW explode(array(
    named_struct('code','1000','name','Cash','type','Asset'),
    named_struct('code','1200','name','Accounts Receivable','type','Asset'),
    named_struct('code','2000','name','Accounts Payable','type','Liability'),
    named_struct('code','4000','name','Service Revenue','type','Revenue'),
    named_struct('code','5000','name','Cost of Services','type','Expense'),
    named_struct('code','6000','name','Payroll','type','Expense'),
    named_struct('code','6100','name','Rent','type','Expense'),
    named_struct('code','6200','name','Software Subscriptions','type','Expense'),
    named_struct('code','6300','name','Travel','type','Expense'),
    named_struct('code','6400','name','Professional Fees','type','Expense')
  )) a AS acct
WHERE rand() < 0.1;

-- ---------- invoices (~40/firm; firm's own clients) ----------
CREATE OR REPLACE TABLE demos.genie_rls.invoices AS
SELECT
  concat(f.firm_id, '_inv', lpad(cast(s.n AS string), 4, '0'))                          AS invoice_id,
  f.firm_id,
  concat(f.firm_id, '_c', lpad(cast(rand()*8 AS int) + 1, 3, '0'))                       AS client_id,
  round(rand()*15000 + 500, 2)                                                          AS amount,
  element_at(array('PAID','OPEN','OVERDUE','DRAFT'), cast(rand()*4 AS int) + 1)          AS status,
  date_add(DATE'2025-06-01', cast(rand()*330 AS int))                                   AS issue_date,
  date_add(DATE'2025-06-01', cast(rand()*330 AS int) + 30)                              AS due_date
FROM demos.genie_rls.firms f
LATERAL VIEW explode(sequence(1, 40)) s AS n;

-- ---------- tenant directory (trusted tenant_id the app passes -> firm) ----------
CREATE OR REPLACE TABLE demos.genie_rls.tenant_directory (
  tenant_id STRING,
  firm_id   STRING,
  firm_name STRING
) COMMENT 'Maps the trusted tenant_id the app passes to a firm_id used for routing';

INSERT INTO demos.genie_rls.tenant_directory VALUES
  ('firm_001','firm_001','Harbor & Vale CPA'),
  ('firm_002','firm_002','Summit Ledger Partners'),
  ('firm_003','firm_003','Cedar Creek Accounting');
