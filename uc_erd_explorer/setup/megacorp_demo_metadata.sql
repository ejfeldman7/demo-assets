-- Illustrative COMMENT/TAG metadata on a handful of megacorp columns and tables, purely
-- to demo the ERD viewer's comment/tag surfacing feature (see server/graph.py) end to
-- end. None of this is required for the feature itself -- it renders correctly against
-- any Unity Catalog comments/tags, or shows nothing when a deployment's catalog has none.

COMMENT ON COLUMN megacorp.erp.customers.customer_name IS 'Customer legal/display name.';
COMMENT ON COLUMN megacorp.erp.customers.credit_limit IS 'Maximum outstanding balance approved for this customer, in USD.';
COMMENT ON COLUMN megacorp.erp.vendors.vendor_name IS 'Vendor legal/display name.';
COMMENT ON COLUMN megacorp.factory.operators.operator_name IS 'Operator full name.';
COMMENT ON COLUMN megacorp.factory.quality_inspections.inspector_name IS 'Inspector full name.';
COMMENT ON COLUMN megacorp.factory.work_orders.status IS 'One of: PLANNED, IN_PROGRESS, COMPLETED, CANCELLED.';

ALTER TABLE megacorp.erp.customers SET TAGS ('domain' = 'sales', 'contains_pii' = 'true');
ALTER TABLE megacorp.erp.invoices SET TAGS ('domain' = 'finance');

-- NOTE: this workspace has a governed tag policy on the "pii" key restricting its
-- allowed values (e.g. to ssn/address) -- discovered via a real INVALID_PARAMETER_VALUE
-- response. Using "contains_pii" here instead, which isn't policy-governed.
ALTER TABLE megacorp.erp.customers ALTER COLUMN customer_name SET TAGS ('contains_pii' = 'true');
ALTER TABLE megacorp.erp.vendors ALTER COLUMN vendor_name SET TAGS ('contains_pii' = 'true');
ALTER TABLE megacorp.factory.operators ALTER COLUMN operator_name SET TAGS ('contains_pii' = 'true');
ALTER TABLE megacorp.factory.quality_inspections ALTER COLUMN inspector_name SET TAGS ('contains_pii' = 'true');
-- Same discovery for "sensitivity": governed to [pii, internal, public].
ALTER TABLE megacorp.erp.customers ALTER COLUMN credit_limit SET TAGS ('sensitivity' = 'internal');
