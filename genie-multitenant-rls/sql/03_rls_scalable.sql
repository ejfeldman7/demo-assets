-- Scalable design: ONE shared set of tables + ONE row-filter function keyed on
-- current_user(), backed by an entitlements lookup the provisioning job populates.
-- Scales to N firms with N rows in entitlements (no per-tenant views/Spaces).
-- To scale across MANY tables, attach this same function via an ABAC policy on a
-- governed tag instead of per-table ALTER statements.

-- entitlements lookup: SP application_id (== current_user() for an SP) -> firm
CREATE TABLE IF NOT EXISTS demos.genie_rls.entitlements (
  principal STRING COMMENT 'SP application_id, equals current_user() when the firm SP queries',
  firm_id   STRING,
  firm_name STRING
) COMMENT 'Populated by scripts/provision_sps.py from the firms source table';

-- One row-filter function: a principal sees a row only for its mapped firm.
CREATE OR REPLACE FUNCTION demos.genie_rls.rls_firm_filter(p_firm_id STRING)
RETURNS BOOLEAN
RETURN EXISTS (
  SELECT 1 FROM demos.genie_rls.entitlements e
  WHERE e.principal = current_user() AND e.firm_id = p_firm_id
);

-- Attach to the shared tables (definer's rights; current_user() runs as invoker).
ALTER TABLE demos.genie_rls.gl_transactions SET ROW FILTER demos.genie_rls.rls_firm_filter ON (firm_id);
ALTER TABLE demos.genie_rls.clients        SET ROW FILTER demos.genie_rls.rls_firm_filter ON (firm_id);
ALTER TABLE demos.genie_rls.invoices       SET ROW FILTER demos.genie_rls.rls_firm_filter ON (firm_id);
