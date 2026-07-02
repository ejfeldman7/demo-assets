-- Synthetic demo catalog for the Interactive ERD Viewer.
-- Structure only (no rows) — a mock manufacturer "MegaCorp" with a factory-floor
-- production schema and a SAP-style ERP schema, cross-linked via shared material/plant keys.
-- factory is created first because erp has FKs pointing into it.

CREATE CATALOG IF NOT EXISTS megacorp COMMENT 'MegaCorp Industries — synthetic demo catalog for the ERD viewer. Structure only, no data.';

CREATE SCHEMA IF NOT EXISTS megacorp.factory COMMENT 'Factory-floor production data: plants, lines, machines, materials, work orders, quality.';
CREATE SCHEMA IF NOT EXISTS megacorp.erp COMMENT 'SAP-style ERP data: customers, vendors, sales/purchase orders, invoices, billing, GL.';

-- ============================================================
-- factory schema
-- ============================================================

CREATE TABLE IF NOT EXISTS megacorp.factory.plants (
  plant_id BIGINT NOT NULL,
  plant_name STRING,
  location STRING,
  country STRING,
  capacity_units_per_day BIGINT,
  CONSTRAINT plants_pk PRIMARY KEY (plant_id)
) USING delta COMMENT 'Physical manufacturing plants.';

CREATE TABLE IF NOT EXISTS megacorp.factory.materials (
  material_id BIGINT NOT NULL,
  material_name STRING,
  material_type STRING COMMENT 'RAW_MATERIAL, COMPONENT, or FINISHED_GOOD',
  unit_of_measure STRING,
  standard_cost DECIMAL(12,2),
  CONSTRAINT materials_pk PRIMARY KEY (material_id)
) USING delta COMMENT 'Materials master: raw materials, components, and finished goods.';

CREATE TABLE IF NOT EXISTS megacorp.factory.production_lines (
  production_line_id BIGINT NOT NULL,
  plant_id BIGINT,
  line_name STRING,
  line_type STRING,
  CONSTRAINT production_lines_pk PRIMARY KEY (production_line_id),
  CONSTRAINT production_lines_plant_id_fk FOREIGN KEY (plant_id) REFERENCES megacorp.factory.plants (plant_id)
) USING delta COMMENT 'Production lines within a plant.';

CREATE TABLE IF NOT EXISTS megacorp.factory.machines (
  machine_id BIGINT NOT NULL,
  production_line_id BIGINT,
  machine_type STRING,
  install_date DATE,
  status STRING,
  CONSTRAINT machines_pk PRIMARY KEY (machine_id),
  CONSTRAINT machines_production_line_id_fk FOREIGN KEY (production_line_id) REFERENCES megacorp.factory.production_lines (production_line_id)
) USING delta COMMENT 'Machines assigned to a production line.';

CREATE TABLE IF NOT EXISTS megacorp.factory.bill_of_materials (
  bom_id BIGINT NOT NULL,
  parent_material_id BIGINT,
  component_material_id BIGINT,
  quantity_required DECIMAL(12,4),
  CONSTRAINT bill_of_materials_pk PRIMARY KEY (bom_id),
  CONSTRAINT bom_parent_material_id_fk FOREIGN KEY (parent_material_id) REFERENCES megacorp.factory.materials (material_id),
  CONSTRAINT bom_component_material_id_fk FOREIGN KEY (component_material_id) REFERENCES megacorp.factory.materials (material_id)
) USING delta COMMENT 'Bill of materials: which component materials make up a parent (finished good) material.';

CREATE TABLE IF NOT EXISTS megacorp.factory.shifts (
  shift_id BIGINT NOT NULL,
  plant_id BIGINT,
  shift_name STRING,
  start_time STRING,
  end_time STRING,
  CONSTRAINT shifts_pk PRIMARY KEY (shift_id),
  CONSTRAINT shifts_plant_id_fk FOREIGN KEY (plant_id) REFERENCES megacorp.factory.plants (plant_id)
) USING delta COMMENT 'Shift definitions per plant.';

CREATE TABLE IF NOT EXISTS megacorp.factory.operators (
  operator_id BIGINT NOT NULL,
  plant_id BIGINT,
  operator_name STRING,
  hire_date DATE,
  certification_level STRING,
  CONSTRAINT operators_pk PRIMARY KEY (operator_id),
  CONSTRAINT operators_plant_id_fk FOREIGN KEY (plant_id) REFERENCES megacorp.factory.plants (plant_id)
) USING delta COMMENT 'Factory-floor operators assigned to a plant.';

CREATE TABLE IF NOT EXISTS megacorp.factory.work_orders (
  work_order_id BIGINT NOT NULL,
  production_line_id BIGINT,
  material_id BIGINT,
  quantity_planned BIGINT,
  quantity_produced BIGINT,
  start_date DATE,
  end_date DATE,
  status STRING,
  CONSTRAINT work_orders_pk PRIMARY KEY (work_order_id),
  CONSTRAINT work_orders_production_line_id_fk FOREIGN KEY (production_line_id) REFERENCES megacorp.factory.production_lines (production_line_id),
  CONSTRAINT work_orders_material_id_fk FOREIGN KEY (material_id) REFERENCES megacorp.factory.materials (material_id)
) USING delta COMMENT 'Production work orders for a given material on a given line.';

CREATE TABLE IF NOT EXISTS megacorp.factory.quality_inspections (
  inspection_id BIGINT NOT NULL,
  work_order_id BIGINT,
  inspector_name STRING,
  inspection_date DATE,
  result STRING,
  defect_count BIGINT,
  CONSTRAINT quality_inspections_pk PRIMARY KEY (inspection_id),
  CONSTRAINT quality_inspections_work_order_id_fk FOREIGN KEY (work_order_id) REFERENCES megacorp.factory.work_orders (work_order_id)
) USING delta COMMENT 'Quality inspection results tied to a work order.';

CREATE TABLE IF NOT EXISTS megacorp.factory.machine_sensor_readings (
  reading_id BIGINT NOT NULL,
  machine_id BIGINT,
  reading_timestamp TIMESTAMP,
  temperature_c DOUBLE,
  vibration_mm_s DOUBLE,
  pressure_bar DOUBLE,
  CONSTRAINT machine_sensor_readings_pk PRIMARY KEY (reading_id),
  CONSTRAINT machine_sensor_readings_machine_id_fk FOREIGN KEY (machine_id) REFERENCES megacorp.factory.machines (machine_id)
) USING delta COMMENT 'IoT sensor telemetry from factory machines.';

CREATE TABLE IF NOT EXISTS megacorp.factory.work_order_operators (
  work_order_id BIGINT NOT NULL,
  operator_id BIGINT NOT NULL,
  shift_id BIGINT NOT NULL,
  hours_worked DECIMAL(5,2),
  CONSTRAINT work_order_operators_pk PRIMARY KEY (work_order_id, operator_id, shift_id),
  CONSTRAINT wo_operators_work_order_id_fk FOREIGN KEY (work_order_id) REFERENCES megacorp.factory.work_orders (work_order_id),
  CONSTRAINT wo_operators_operator_id_fk FOREIGN KEY (operator_id) REFERENCES megacorp.factory.operators (operator_id),
  CONSTRAINT wo_operators_shift_id_fk FOREIGN KEY (shift_id) REFERENCES megacorp.factory.shifts (shift_id)
) USING delta COMMENT 'Junction table: which operators worked which work order during which shift (many-to-many).';

-- ============================================================
-- erp schema (SAP-style) — references factory.plants / factory.materials
-- ============================================================

CREATE TABLE IF NOT EXISTS megacorp.erp.customers (
  customer_id BIGINT NOT NULL,
  customer_name STRING,
  industry STRING,
  country STRING,
  credit_limit DECIMAL(14,2),
  created_at DATE,
  CONSTRAINT customers_pk PRIMARY KEY (customer_id)
) USING delta COMMENT 'ERP customer master.';

CREATE TABLE IF NOT EXISTS megacorp.erp.vendors (
  vendor_id BIGINT NOT NULL,
  vendor_name STRING,
  country STRING,
  payment_terms STRING,
  CONSTRAINT vendors_pk PRIMARY KEY (vendor_id)
) USING delta COMMENT 'ERP vendor/supplier master.';

CREATE TABLE IF NOT EXISTS megacorp.erp.cost_centers (
  cost_center_id BIGINT NOT NULL,
  cost_center_name STRING,
  plant_id BIGINT,
  CONSTRAINT cost_centers_pk PRIMARY KEY (cost_center_id),
  CONSTRAINT cost_centers_plant_id_fk FOREIGN KEY (plant_id) REFERENCES megacorp.factory.plants (plant_id)
) USING delta COMMENT 'Finance cost centers, each tied to a factory plant.';

CREATE TABLE IF NOT EXISTS megacorp.erp.sales_orders (
  sales_order_id BIGINT NOT NULL,
  customer_id BIGINT,
  order_date DATE,
  status STRING,
  total_amount DECIMAL(14,2),
  CONSTRAINT sales_orders_pk PRIMARY KEY (sales_order_id),
  CONSTRAINT sales_orders_customer_id_fk FOREIGN KEY (customer_id) REFERENCES megacorp.erp.customers (customer_id)
) USING delta COMMENT 'Customer sales orders.';

CREATE TABLE IF NOT EXISTS megacorp.erp.sales_order_lines (
  sales_order_line_id BIGINT NOT NULL,
  sales_order_id BIGINT,
  material_id BIGINT,
  quantity BIGINT,
  unit_price DECIMAL(12,2),
  CONSTRAINT sales_order_lines_pk PRIMARY KEY (sales_order_line_id),
  CONSTRAINT sales_order_lines_sales_order_id_fk FOREIGN KEY (sales_order_id) REFERENCES megacorp.erp.sales_orders (sales_order_id),
  CONSTRAINT sales_order_lines_material_id_fk FOREIGN KEY (material_id) REFERENCES megacorp.factory.materials (material_id)
) USING delta COMMENT 'Line items of a sales order, referencing the finished-goods material sold.';

CREATE TABLE IF NOT EXISTS megacorp.erp.purchase_orders (
  purchase_order_id BIGINT NOT NULL,
  vendor_id BIGINT,
  order_date DATE,
  status STRING,
  total_amount DECIMAL(14,2),
  CONSTRAINT purchase_orders_pk PRIMARY KEY (purchase_order_id),
  CONSTRAINT purchase_orders_vendor_id_fk FOREIGN KEY (vendor_id) REFERENCES megacorp.erp.vendors (vendor_id)
) USING delta COMMENT 'Purchase orders issued to vendors.';

CREATE TABLE IF NOT EXISTS megacorp.erp.purchase_order_lines (
  purchase_order_line_id BIGINT NOT NULL,
  purchase_order_id BIGINT,
  material_id BIGINT,
  quantity BIGINT,
  unit_cost DECIMAL(12,2),
  CONSTRAINT purchase_order_lines_pk PRIMARY KEY (purchase_order_line_id),
  CONSTRAINT purchase_order_lines_purchase_order_id_fk FOREIGN KEY (purchase_order_id) REFERENCES megacorp.erp.purchase_orders (purchase_order_id),
  CONSTRAINT purchase_order_lines_material_id_fk FOREIGN KEY (material_id) REFERENCES megacorp.factory.materials (material_id)
) USING delta COMMENT 'Line items of a purchase order, referencing the raw-material/component being procured.';

CREATE TABLE IF NOT EXISTS megacorp.erp.invoices (
  invoice_id BIGINT NOT NULL,
  sales_order_id BIGINT,
  customer_id BIGINT,
  invoice_date DATE,
  due_date DATE,
  amount DECIMAL(14,2),
  status STRING,
  CONSTRAINT invoices_pk PRIMARY KEY (invoice_id),
  CONSTRAINT invoices_sales_order_id_fk FOREIGN KEY (sales_order_id) REFERENCES megacorp.erp.sales_orders (sales_order_id),
  CONSTRAINT invoices_customer_id_fk FOREIGN KEY (customer_id) REFERENCES megacorp.erp.customers (customer_id)
) USING delta COMMENT 'Customer invoices billed against a sales order.';

CREATE TABLE IF NOT EXISTS megacorp.erp.invoice_line_items (
  invoice_line_item_id BIGINT NOT NULL,
  invoice_id BIGINT,
  description STRING,
  amount DECIMAL(12,2),
  tax_amount DECIMAL(12,2),
  CONSTRAINT invoice_line_items_pk PRIMARY KEY (invoice_line_item_id),
  CONSTRAINT invoice_line_items_invoice_id_fk FOREIGN KEY (invoice_id) REFERENCES megacorp.erp.invoices (invoice_id)
) USING delta COMMENT 'Line-item detail of an invoice.';

CREATE TABLE IF NOT EXISTS megacorp.erp.payments (
  payment_id BIGINT NOT NULL,
  invoice_id BIGINT,
  payment_date DATE,
  amount DECIMAL(14,2),
  payment_method STRING,
  CONSTRAINT payments_pk PRIMARY KEY (payment_id),
  CONSTRAINT payments_invoice_id_fk FOREIGN KEY (invoice_id) REFERENCES megacorp.erp.invoices (invoice_id)
) USING delta COMMENT 'Customer payments applied against an invoice.';

CREATE TABLE IF NOT EXISTS megacorp.erp.general_ledger (
  gl_entry_id BIGINT NOT NULL,
  cost_center_id BIGINT,
  reference_invoice_id BIGINT,
  account_code STRING,
  debit_amount DECIMAL(14,2),
  credit_amount DECIMAL(14,2),
  posting_date DATE,
  CONSTRAINT general_ledger_pk PRIMARY KEY (gl_entry_id),
  CONSTRAINT general_ledger_cost_center_id_fk FOREIGN KEY (cost_center_id) REFERENCES megacorp.erp.cost_centers (cost_center_id),
  CONSTRAINT general_ledger_reference_invoice_id_fk FOREIGN KEY (reference_invoice_id) REFERENCES megacorp.erp.invoices (invoice_id)
) USING delta COMMENT 'General ledger postings, optionally referencing the invoice that generated them.';
