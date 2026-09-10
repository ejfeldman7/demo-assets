-- Synthetic DIMENSIONAL (star / galaxy schema) demo catalog for the Interactive ERD Viewer.
-- Structure only (no rows). A retail-sales constellation with THREE facts sharing conformed
-- dimensions, two SNOWFLAKED sub-dimensions (outriggers), and a many-to-many bridge -- named
-- with the fact_/dim_/bridge_ conventions so the Star layout's classifier resolves every
-- table on NAMING (its high-confidence path) and centers fact_sales automatically.
--
-- Shape (why it's a good test bed for the Star/Galaxy layout):
--   facts:   fact_sales, fact_inventory_snapshot, fact_returns   (three hubs)
--   dims:    dim_date, dim_customer, dim_product, dim_store, dim_promotion, dim_channel
--   sub-dims (snowflake outriggers): dim_category  (<- dim_product),
--                                    dim_region    (<- dim_store)
--   bridge:  bridge_promotion_product
-- The three facts share dim_date/dim_product/dim_store (a fact constellation), and the two
-- outriggers sit one hop beyond their parent dimension -- so Galaxy mode shows multiple hubs,
-- conformed dims between them, and sub-dims trailing outward.
--
-- This complements the normalized megacorp/logistics demo (OLTP, no dim_/fact_ names, which
-- exercises the classifier's STRUCTURAL fallback). Together they cover both worlds.
--
-- Keys are declared as UC informational PRIMARY/FOREIGN KEY constraints (not enforced -- the
-- Databricks default), which is what the ERD reads from information_schema, so the star
-- renders from real declared relationships. No governance TAGS are set here on purpose --
-- SET TAGS can be rejected by a workspace's governed tag policies, and the demo should deploy
-- cleanly anywhere, so comments carry the documentation here.
-- "retail_star" is a single literal placeholder the loader substitutes at run time.

CREATE CATALOG IF NOT EXISTS retail_star COMMENT 'Retail Star — synthetic dimensional (star/galaxy schema) demo catalog for the ERD viewer. Structure only, no data.';

CREATE SCHEMA IF NOT EXISTS retail_star.sales COMMENT 'Retail sales galaxy: three facts sharing conformed dimensions, two snowflaked sub-dimensions, and a promotion/product bridge.';

-- ============================================================
-- Snowflake sub-dimensions (outriggers) -- created first, since dimensions reference them.
-- ============================================================

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_category (
  category_key BIGINT NOT NULL,
  category_name STRING,
  department STRING,
  CONSTRAINT dim_category_pk PRIMARY KEY (category_key)
) USING delta COMMENT 'Product category outrigger (snowflake off dim_product).';

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_region (
  region_key BIGINT NOT NULL,
  region_name STRING,
  country STRING,
  CONSTRAINT dim_region_pk PRIMARY KEY (region_key)
) USING delta COMMENT 'Sales region outrigger (snowflake off dim_store).';

-- ============================================================
-- Conformed dimensions (shared across the facts)
-- ============================================================

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_date (
  date_key BIGINT NOT NULL,
  full_date DATE,
  day_of_week STRING,
  month_number INT,
  month_name STRING,
  quarter INT,
  year INT,
  is_weekend BOOLEAN,
  CONSTRAINT dim_date_pk PRIMARY KEY (date_key)
) USING delta COMMENT 'Date dimension (one row per calendar day). Conformed across all facts.';

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_customer (
  customer_key BIGINT NOT NULL,
  customer_id STRING,
  customer_name STRING,
  segment STRING,
  city STRING,
  region STRING,
  country STRING,
  CONSTRAINT dim_customer_pk PRIMARY KEY (customer_key)
) USING delta COMMENT 'Customer dimension. Lookup table for who placed a sale.';

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_product (
  product_key BIGINT NOT NULL,
  sku STRING,
  product_name STRING,
  category_key BIGINT,
  subcategory STRING,
  brand STRING,
  unit_cost DECIMAL(12,2),
  CONSTRAINT dim_product_pk PRIMARY KEY (product_key),
  CONSTRAINT dim_product_category_fk FOREIGN KEY (category_key) REFERENCES retail_star.sales.dim_category (category_key)
) USING delta COMMENT 'Product dimension. Snowflaked: references dim_category.';

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_store (
  store_key BIGINT NOT NULL,
  store_code STRING,
  store_name STRING,
  region_key BIGINT,
  city STRING,
  store_format STRING,
  CONSTRAINT dim_store_pk PRIMARY KEY (store_key),
  CONSTRAINT dim_store_region_fk FOREIGN KEY (region_key) REFERENCES retail_star.sales.dim_region (region_key)
) USING delta COMMENT 'Store/location dimension. Snowflaked: references dim_region.';

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_promotion (
  promotion_key BIGINT NOT NULL,
  promo_code STRING,
  promo_name STRING,
  promo_type STRING,
  discount_pct DECIMAL(5,2),
  CONSTRAINT dim_promotion_pk PRIMARY KEY (promotion_key)
) USING delta COMMENT 'Promotion dimension (the campaign a sale was attributed to).';

CREATE TABLE IF NOT EXISTS retail_star.sales.dim_channel (
  channel_key BIGINT NOT NULL,
  channel_name STRING,
  channel_type STRING,
  CONSTRAINT dim_channel_pk PRIMARY KEY (channel_key)
) USING delta COMMENT 'Sales channel dimension (store / web / marketplace).';

-- ============================================================
-- Facts (reference the conformed dimensions above)
-- ============================================================

CREATE TABLE IF NOT EXISTS retail_star.sales.fact_sales (
  sales_key BIGINT NOT NULL,
  date_key BIGINT,
  customer_key BIGINT,
  product_key BIGINT,
  store_key BIGINT,
  promotion_key BIGINT,
  channel_key BIGINT,
  quantity INT,
  unit_price DECIMAL(12,2),
  gross_amount DECIMAL(14,2),
  discount_amount DECIMAL(14,2),
  net_amount DECIMAL(14,2),
  CONSTRAINT fact_sales_pk PRIMARY KEY (sales_key),
  CONSTRAINT fact_sales_date_fk FOREIGN KEY (date_key) REFERENCES retail_star.sales.dim_date (date_key),
  CONSTRAINT fact_sales_customer_fk FOREIGN KEY (customer_key) REFERENCES retail_star.sales.dim_customer (customer_key),
  CONSTRAINT fact_sales_product_fk FOREIGN KEY (product_key) REFERENCES retail_star.sales.dim_product (product_key),
  CONSTRAINT fact_sales_store_fk FOREIGN KEY (store_key) REFERENCES retail_star.sales.dim_store (store_key),
  CONSTRAINT fact_sales_promotion_fk FOREIGN KEY (promotion_key) REFERENCES retail_star.sales.dim_promotion (promotion_key),
  CONSTRAINT fact_sales_channel_fk FOREIGN KEY (channel_key) REFERENCES retail_star.sales.dim_channel (channel_key)
) USING delta COMMENT 'Sales fact table (grain: one row per order line). The primary hub -- six conformed dimensions.';

CREATE TABLE IF NOT EXISTS retail_star.sales.fact_inventory_snapshot (
  snapshot_key BIGINT NOT NULL,
  date_key BIGINT,
  product_key BIGINT,
  store_key BIGINT,
  on_hand_qty INT,
  on_order_qty INT,
  reorder_point INT,
  days_of_supply DECIMAL(8,2),
  CONSTRAINT fact_inventory_snapshot_pk PRIMARY KEY (snapshot_key),
  CONSTRAINT fact_inventory_date_fk FOREIGN KEY (date_key) REFERENCES retail_star.sales.dim_date (date_key),
  CONSTRAINT fact_inventory_product_fk FOREIGN KEY (product_key) REFERENCES retail_star.sales.dim_product (product_key),
  CONSTRAINT fact_inventory_store_fk FOREIGN KEY (store_key) REFERENCES retail_star.sales.dim_store (store_key)
) USING delta COMMENT 'Inventory snapshot fact (grain: product x store x day). Shares conformed dimensions with the other facts.';

CREATE TABLE IF NOT EXISTS retail_star.sales.fact_returns (
  return_key BIGINT NOT NULL,
  date_key BIGINT,
  customer_key BIGINT,
  product_key BIGINT,
  store_key BIGINT,
  return_qty INT,
  refund_amount DECIMAL(14,2),
  reason_code STRING,
  CONSTRAINT fact_returns_pk PRIMARY KEY (return_key),
  CONSTRAINT fact_returns_date_fk FOREIGN KEY (date_key) REFERENCES retail_star.sales.dim_date (date_key),
  CONSTRAINT fact_returns_customer_fk FOREIGN KEY (customer_key) REFERENCES retail_star.sales.dim_customer (customer_key),
  CONSTRAINT fact_returns_product_fk FOREIGN KEY (product_key) REFERENCES retail_star.sales.dim_product (product_key),
  CONSTRAINT fact_returns_store_fk FOREIGN KEY (store_key) REFERENCES retail_star.sales.dim_store (store_key)
) USING delta COMMENT 'Returns fact table (grain: one row per returned line). Shares conformed dimensions with fact_sales.';

-- ============================================================
-- Bridge (many-to-many)
-- ============================================================

CREATE TABLE IF NOT EXISTS retail_star.sales.bridge_promotion_product (
  promotion_key BIGINT NOT NULL,
  product_key BIGINT NOT NULL,
  CONSTRAINT bridge_promotion_product_pk PRIMARY KEY (promotion_key, product_key),
  CONSTRAINT bridge_promo_product_promotion_fk FOREIGN KEY (promotion_key) REFERENCES retail_star.sales.dim_promotion (promotion_key),
  CONSTRAINT bridge_promo_product_product_fk FOREIGN KEY (product_key) REFERENCES retail_star.sales.dim_product (product_key)
) USING delta COMMENT 'Bridge table: which products participate in which promotions (many-to-many).';
