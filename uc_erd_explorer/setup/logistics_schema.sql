-- A second synthetic demo catalog for the Interactive ERD Viewer, cross-linked to the
-- megacorp catalog (see megacorp_schema.sql) -- for exercising the app's multi-catalog
-- rendering over a real cross-catalog foreign key, not just multiple schemas in one
-- catalog. Structure only (no rows). "megacorp" here is a literal placeholder,
-- independent of the "logistics" one -- see create_logistics_demo.py's
-- substitute_catalogs(), which lets prod (logistics -> megacorp) and test
-- (logistics_ts -> megacorp_ts) each get their own consistently-paired pair of catalogs.

CREATE CATALOG IF NOT EXISTS logistics COMMENT 'Logistics Partners — synthetic demo catalog for the ERD viewer, cross-linked to megacorp. Structure only, no data.';

CREATE SCHEMA IF NOT EXISTS logistics.shipping COMMENT 'Carrier and shipment tracking data, cross-linked to megacorp sales orders.';

CREATE TABLE IF NOT EXISTS logistics.shipping.carriers (
  carrier_id BIGINT NOT NULL,
  carrier_name STRING,
  carrier_type STRING COMMENT 'One of: FREIGHT, PARCEL, OCEAN, RAIL',
  country STRING,
  CONSTRAINT carriers_pk PRIMARY KEY (carrier_id)
) USING delta COMMENT 'Shipping carrier master.';

CREATE TABLE IF NOT EXISTS logistics.shipping.warehouses (
  warehouse_id BIGINT NOT NULL,
  warehouse_name STRING,
  country STRING,
  CONSTRAINT warehouses_pk PRIMARY KEY (warehouse_id)
) USING delta COMMENT 'Distribution warehouses that fulfill sales orders.';

CREATE TABLE IF NOT EXISTS logistics.shipping.shipments (
  shipment_id BIGINT NOT NULL,
  sales_order_id BIGINT,
  carrier_id BIGINT,
  warehouse_id BIGINT,
  ship_date DATE,
  delivery_date DATE,
  status STRING,
  tracking_number STRING,
  CONSTRAINT shipments_pk PRIMARY KEY (shipment_id),
  CONSTRAINT shipments_sales_order_id_fk FOREIGN KEY (sales_order_id) REFERENCES megacorp.erp.sales_orders (sales_order_id),
  CONSTRAINT shipments_carrier_id_fk FOREIGN KEY (carrier_id) REFERENCES logistics.shipping.carriers (carrier_id),
  CONSTRAINT shipments_warehouse_id_fk FOREIGN KEY (warehouse_id) REFERENCES logistics.shipping.warehouses (warehouse_id)
) USING delta COMMENT 'Shipment tracking for a sales order, cross-linked to megacorp.erp.sales_orders.';

CREATE TABLE IF NOT EXISTS logistics.shipping.shipment_items (
  shipment_item_id BIGINT NOT NULL,
  shipment_id BIGINT,
  sales_order_line_id BIGINT,
  quantity_shipped BIGINT,
  CONSTRAINT shipment_items_pk PRIMARY KEY (shipment_item_id),
  CONSTRAINT shipment_items_shipment_id_fk FOREIGN KEY (shipment_id) REFERENCES logistics.shipping.shipments (shipment_id),
  CONSTRAINT shipment_items_sales_order_line_id_fk FOREIGN KEY (sales_order_line_id) REFERENCES megacorp.erp.sales_order_lines (sales_order_line_id)
) USING delta COMMENT 'Per-line shipment quantities, cross-linked to megacorp.erp.sales_order_lines.';
