-- Per-firm RLS as an ABAC policy (replaces the classic ALTER TABLE row filter),
-- with a workspace-'admins' bypass. APPLIED via scripts/apply_abac.py.
--
-- Why a governed tag: ABAC row-filter policies map columns ONLY via governed tags
-- (MATCH COLUMNS has_tag(...) → USING COLUMNS alias); USING COLUMNS does not accept
-- literal column names. Governed tags are created in SQL (workspace admins have
-- CREATE). is_member('admins') checks the WORKSPACE admins group (is_account_group_member
-- would not match workspace admins).

-- 1. Row-filter function: explicit default-deny (ELSE FALSE) — anyone who is neither a
--    workspace admin nor mapped in entitlements for this row's firm gets NO rows.
CREATE OR REPLACE FUNCTION demos.genie_rls.rls_firm_filter(p_firm_id STRING)
RETURNS BOOLEAN
RETURN
  CASE
    WHEN is_member('admins') THEN TRUE                          -- workspace admins: full access
    WHEN EXISTS (SELECT 1 FROM demos.genie_rls.entitlements e
                 WHERE e.principal = current_user() AND e.firm_id = p_firm_id) THEN TRUE
    ELSE FALSE                                                  -- everyone else: nothing
  END;

-- 2. Governed tag + apply to the firm_id columns (what the policy matches on).
CREATE GOVERNED TAG rls_firm VALUES ('true');
ALTER TABLE demos.genie_rls.gl_transactions ALTER COLUMN firm_id SET TAGS ('rls_firm' = 'true');
ALTER TABLE demos.genie_rls.clients         ALTER COLUMN firm_id SET TAGS ('rls_firm' = 'true');
ALTER TABLE demos.genie_rls.invoices        ALTER COLUMN firm_id SET TAGS ('rls_firm' = 'true');

-- 3. Remove any classic row filters (superseded by the ABAC policy).
ALTER TABLE demos.genie_rls.gl_transactions DROP ROW FILTER;
ALTER TABLE demos.genie_rls.clients         DROP ROW FILTER;
ALTER TABLE demos.genie_rls.invoices        DROP ROW FILTER;

-- 4. ABAC policy on the schema: applies the row filter to every firm_id-tagged column
--    (only the 3 data tables are tagged — entitlements/firms are not, so no recursion).
--    TO is the allow-list of principals SUBJECT to filtering: account users + the firm
--    service principals. Admins bypass via the function. NOTE: the firm SPs are
--    enumerated here for the demo; at scale use an ACCOUNT-LEVEL group in TO (managed by
--    the provisioning job) so the policy never changes when a firm is added.
DROP POLICY firm_rls ON SCHEMA demos.genie_rls;   -- omit on first create
CREATE POLICY firm_rls
ON SCHEMA demos.genie_rls
ROW FILTER demos.genie_rls.rls_firm_filter
TO `account users`, `<firm-sp-application-id-1>`, `<firm-sp-application-id-2>`, `<firm-sp-application-id-3>`
FOR TABLES
MATCH COLUMNS has_tag('rls_firm') AS fcol
USING COLUMNS (fcol);
