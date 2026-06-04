# Databricks notebook source
# MAGIC %md
# MAGIC # Brand Intelligence Demo - Declarative Pipeline (Bronze + Silver)
# MAGIC
# MAGIC Uses `pyspark.pipelines` (Lakeflow Spark Declarative Pipelines).
# MAGIC
# MAGIC **Bronze:** Auto Loader streaming tables from raw CSV volume
# MAGIC **Silver:** ai_similarity() SKU resolution (materialized view), cleaned actuals (streaming), cleaned inventory (streaming)
# MAGIC
# MAGIC ### Design decisions:
# MAGIC - Bronze tables are **streaming tables** (append-only ingestion via Auto Loader)
# MAGIC - `sku_resolution` is a **materialized view** (batch ai_similarity() cross-join, benefits from incremental refresh)
# MAGIC - Silver actuals/inventory are **streaming tables** (append-only, processed once)
# MAGIC - Source tables should have deletion vectors + row tracking enabled for optimal MV incremental refresh

# COMMAND ----------

from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG = "brand_intel_demo"
VOLUME_PATH = f"/Volumes/{CATALOG}/assets/raw"

# =============================================================================
# BRONZE LAYER -- Auto Loader streaming tables (append-only ingestion)
# =============================================================================

@dp.table(
    name="raw_actuals",
    comment="Raw actuals from CSV drop zone. SKU names are unresolved aliases.",
    temporary=True,
)
def raw_actuals():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.schemaHints", """
            actual_id STRING, transaction_date DATE, customer_id STRING,
            sku_alias_name STRING, region STRING, channel STRING,
            actual_units DOUBLE, actual_revenue_usd DOUBLE,
            order_id STRING, demand_driver STRING
        """)
        .option("header", "true")
        .load(f"{VOLUME_PATH}/fact_actuals")
        .withColumn("ingested_at", F.current_timestamp())
    )


@dp.table(
    name="raw_inventory",
    comment="Raw daily inventory snapshots from CSV drop zone.",
    temporary=True,
)
def raw_inventory():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.schemaHints", """
            snapshot_date DATE, sku_id STRING, warehouse_region STRING,
            on_hand_units DOUBLE, on_order_units DOUBLE,
            safety_stock_units DOUBLE, days_of_supply DOUBLE,
            stockout_flag BOOLEAN
        """)
        .option("header", "true")
        .load(f"{VOLUME_PATH}/fact_inventory")
        .withColumn("ingested_at", F.current_timestamp())
    )


# Dimension tables use direct reads from the Delta tables written by the
# synthetic data generator. This avoids Auto Loader append-only semantics
# which would duplicate dimension rows on every pipeline run, causing the
# ai_similarity() cross-join in sku_resolution to grow quadratically.

@dp.materialized_view(
    name="raw_skus",
    comment="SKU dimension from raw Delta table.",
    temporary=True,
)
def raw_skus():
    return spark.sql(f"""
        SELECT *, current_timestamp() AS ingested_at
        FROM {CATALOG}.raw.dim_skus
    """)


@dp.materialized_view(
    name="raw_sku_aliases",
    comment="SKU alias names from raw Delta table. ai_similarity() resolves these in Silver.",
    temporary=True,
)
def raw_sku_aliases():
    return spark.sql(f"""
        SELECT *, current_timestamp() AS ingested_at
        FROM {CATALOG}.raw.dim_sku_aliases
    """)


# =============================================================================
# SILVER LAYER
# =============================================================================

# --- SKU Resolution: Materialized View ---
# Uses ai_similarity() cross-join which is a batch operation.
# MV is the right choice here: the alias list changes infrequently, and
# incremental refresh via Enzyme will only recompute changed aliases.
# The QUALIFY + window function pattern is supported for incremental refresh.

@dp.materialized_view(
    name="sku_resolution",
    comment="SKU alias resolution via ai_similarity(). Matches raw alias names to canonical SKU records. Scores >= 0.85 auto-resolved, 0.70-0.84 review required, < 0.70 unresolved.",
    table_properties={"quality": "silver"},
)
@dp.expect_or_drop("similarity_score_not_null", "similarity_score IS NOT NULL")
def sku_resolution():
    return spark.sql("""
        WITH scored AS (
            SELECT
                a.alias_name,
                a.source_system,
                s.sku_id             AS matched_sku_id,
                s.sku_name_canonical AS matched_sku_name,
                ai_similarity(a.alias_name, s.sku_name_canonical) AS similarity_score
            FROM LIVE.raw_sku_aliases a
            CROSS JOIN LIVE.raw_skus s
        )
        SELECT
            alias_name,
            source_system,
            matched_sku_id,
            matched_sku_name,
            similarity_score,
            CASE
                WHEN similarity_score >= 0.85 THEN 'auto_resolved'
                WHEN similarity_score >= 0.70 THEN 'review_required'
                ELSE 'unresolved'
            END AS resolution_status,
            current_timestamp() AS resolved_at
        FROM scored
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY alias_name
            ORDER BY similarity_score DESC
        ) = 1
    """)


# --- Silver Actuals: Materialized View ---
# MV instead of streaming table so it refreshes after sku_resolution is populated.
# Incremental refresh via Enzyme handles new data efficiently.
# Late-arriving corrections are absorbed by MV recompute.

actuals_expectations = {
    "actual_units_positive": "actual_units >= 0",
    "customer_id_not_null": "customer_id IS NOT NULL",
    "transaction_date_not_future": "transaction_date <= current_date()",
}

@dp.materialized_view(
    name="actuals",
    comment="Cleaned actuals with resolved SKU IDs. Alias names replaced via ai_similarity() resolution.",
    table_properties={"quality": "silver"},
)
@dp.expect_all_or_drop(actuals_expectations)
@dp.expect("sku_resolved", "resolution_status IN ('auto_resolved', 'review_required')")
def actuals():
    return spark.sql("""
        SELECT
            a.actual_id,
            a.transaction_date,
            a.customer_id,
            a.sku_alias_name,
            COALESCE(r.matched_sku_id, 'UNRESOLVED') AS sku_id,
            COALESCE(r.matched_sku_name, a.sku_alias_name) AS sku_name,
            r.resolution_status,
            r.similarity_score,
            s.product_category,
            s.unit_price_usd,
            a.region,
            a.channel,
            a.actual_units,
            a.actual_revenue_usd,
            a.order_id,
            a.demand_driver,
            a.ingested_at,
            DATE_TRUNC('week', a.transaction_date) AS week_start_date
        FROM LIVE.raw_actuals a
        LEFT JOIN LIVE.sku_resolution r ON a.sku_alias_name = r.alias_name
        LEFT JOIN LIVE.raw_skus s ON r.matched_sku_id = s.sku_id
    """)


# --- Silver Actuals DQ Failures: Materialized View ---
# Tracks unresolved SKUs for DQ monitoring. MV so it refreshes
# incrementally as sku_resolution updates.

@dp.materialized_view(
    name="actuals_dq_failures",
    comment="Quarantined actuals rows where SKU resolution failed.",
    table_properties={"quality": "silver"},
)
def actuals_dq_failures():
    return spark.sql("""
        SELECT
            a.actual_id,
            a.transaction_date,
            a.customer_id,
            a.sku_alias_name,
            r.similarity_score,
            r.resolution_status,
            'unresolved_sku' AS failure_reason,
            a.ingested_at
        FROM LIVE.raw_actuals a
        LEFT JOIN LIVE.sku_resolution r ON a.sku_alias_name = r.alias_name
        WHERE r.resolution_status = 'unresolved' OR r.matched_sku_id IS NULL
    """)


# --- Silver Inventory: Materialized View ---
# MV for consistent refresh ordering with dimension joins.

inventory_expectations = {
    "on_hand_not_negative": "on_hand_units >= 0",
    "sku_id_not_null": "sku_id IS NOT NULL",
}

@dp.materialized_view(
    name="inventory",
    comment="Cleaned inventory with risk levels. Critical: DOS < 7, Low: DOS < 14, Adequate otherwise.",
    table_properties={"quality": "silver"},
)
@dp.expect_all_or_drop(inventory_expectations)
def inventory():
    return spark.sql("""
        SELECT
            i.snapshot_date,
            i.sku_id,
            s.sku_name_canonical AS sku_name,
            s.product_category,
            s.unit_price_usd,
            s.lead_time_days,
            i.warehouse_region,
            i.on_hand_units,
            i.on_order_units,
            i.safety_stock_units,
            i.days_of_supply,
            i.stockout_flag,
            CASE
                WHEN i.days_of_supply < 7 THEN 'critical'
                WHEN i.days_of_supply < 14 THEN 'low'
                ELSE 'adequate'
            END AS risk_level,
            i.ingested_at
        FROM LIVE.raw_inventory i
        LEFT JOIN LIVE.raw_skus s ON i.sku_id = s.sku_id
    """)
