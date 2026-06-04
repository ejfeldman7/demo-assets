# Databricks notebook source
# MAGIC %md
# MAGIC # Brand Intelligence Demo - Gold Layer: Metric Views
# MAGIC
# MAGIC Creates metric views (governed measures + dimensions) that power the Genie spaces.
# MAGIC Uses `CREATE OR REPLACE VIEW ... WITH METRICS LANGUAGE YAML` syntax.
# MAGIC
# MAGIC Metric views define joins inline via YAML where possible (star-schema patterns).
# MAGIC Base views are only used where pre-aggregation or CTEs are required.
# MAGIC
# MAGIC **Base views (required for pre-aggregation):**
# MAGIC - gold.weekly_forecast_vs_actuals (aggregates daily actuals to weekly, joins to ai_forecasts + dims)
# MAGIC - gold.inventory_forecast_alignment_base (CTE for 4-week forecast demand)
# MAGIC
# MAGIC **Metric views (governed measures + dimensions for Genie):**
# MAGIC - gold.demand_forecast_metrics (Demand Forecast Genie)
# MAGIC - gold.revenue_opportunity_metrics (Revenue Opportunity analysis)
# MAGIC - gold.brand_manager_metrics (Brand Manager performance)
# MAGIC - gold.seasonal_demand_metrics (Seasonal pattern analysis)
# MAGIC - gold.inventory_risk_metrics (Inventory & Channel Genie -- uses YAML joins)
# MAGIC - gold.inventory_forecast_metrics (Inventory vs forecast alignment)

# COMMAND ----------

CATALOG = "brand_intel_demo"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Base View: gold.weekly_forecast_vs_actuals
# MAGIC Required because we need to aggregate daily actuals to weekly grain before joining to ai_forecasts.
# MAGIC This can't be expressed as a metric view join since it requires GROUP BY pre-aggregation.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.weekly_forecast_vs_actuals
COMMENT 'Weekly actuals aggregated and joined to ai_forecast() predictions. Uses only the LATEST forecast vintage per series-date to prevent duplication across pipeline runs. Includes computed columns for accuracy, variance, and revenue gap.'
AS
SELECT
    f.forecast_date AS week_start_date,
    a.customer_id,
    a.sku_id,
    f.forecasted_units,
    f.forecast_lower_bound,
    f.forecast_upper_bound,
    f.confidence_width,
    f.model_used AS forecast_model,
    a.actual_units,
    a.actual_revenue_usd,
    ROUND(f.forecasted_units * s.unit_price_usd, 2) AS forecasted_revenue_usd,
    ROUND(a.actual_units - f.forecasted_units, 2) AS unit_variance,
    ROUND(a.actual_revenue_usd - (f.forecasted_units * s.unit_price_usd), 2) AS revenue_gap_usd,
    ROUND(GREATEST(0.0, 1.0 - ABS(a.actual_units - f.forecasted_units) / NULLIF(a.actual_units, 0)), 4) AS accuracy_pct,
    CASE
        WHEN a.actual_units BETWEEN f.forecast_lower_bound AND f.forecast_upper_bound THEN TRUE
        ELSE FALSE
    END AS is_within_confidence_interval
FROM (
    SELECT
        DATE(week_start_date) AS week_date,
        customer_id,
        sku_id,
        SUM(actual_units) AS actual_units,
        SUM(actual_revenue_usd) AS actual_revenue_usd
    FROM {CATALOG}.silver.actuals
    GROUP BY DATE(week_start_date), customer_id, sku_id
    HAVING DATE(week_start_date) < DATE_TRUNC('week', CURRENT_DATE())
) a
JOIN (
    -- Deduplicate forecasts: keep only the latest vintage per series-date.
    -- Multiple pipeline runs INSERT into ai_forecasts; this ensures only the
    -- most recent forecast is used for accuracy calculations.
    SELECT *
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY customer_id, sku_id, forecast_date
                ORDER BY generated_at DESC
            ) AS _forecast_rank
        FROM {CATALOG}.gold.ai_forecasts
    )
    WHERE _forecast_rank = 1
) f
    ON a.customer_id = f.customer_id
    AND a.sku_id = f.sku_id
    AND (a.week_date = f.forecast_date
         OR a.week_date = DATE_ADD(f.forecast_date, 1))
JOIN {CATALOG}.raw.dim_skus s ON a.sku_id = s.sku_id
""")

print("Created gold.weekly_forecast_vs_actuals")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Metric View: gold.demand_forecast_metrics
# MAGIC Primary metric view for the Demand Forecast Genie.
# MAGIC Source is the pre-aggregated base view; YAML joins bring in customer + SKU dimensions.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.demand_forecast_metrics
WITH METRICS
LANGUAGE YAML
AS $$
  version: 1.1
  comment: "Demand forecast accuracy and revenue opportunity metrics powered by ai_forecast(). Measures are governed -- accuracy, revenue gap, and bias are computed consistently regardless of how the question is phrased."
  source: {CATALOG}.gold.weekly_forecast_vs_actuals
  joins:
    - name: customer
      source: {CATALOG}.raw.dim_customers
      "on": source.customer_id = customer.customer_id
    - name: sku
      source: {CATALOG}.raw.dim_skus
      "on": source.sku_id = sku.sku_id
  dimensions:
    - name: Week
      expr: week_start_date
      comment: "ISO week start date (Monday)"
    - name: Month
      expr: DATE_TRUNC('MONTH', week_start_date)
      comment: "Month of the forecast period"
    - name: Quarter
      expr: DATE_TRUNC('QUARTER', week_start_date)
      comment: "Quarter of the forecast period"
    - name: Customer Name
      expr: customer.customer_name
      comment: "Customer account name"
    - name: SKU Name
      expr: sku.sku_name_canonical
      comment: "Canonical SKU product name"
    - name: Product Category
      expr: sku.product_category
      comment: "Product category: Electronics, Home_Kitchen, Health_Beauty, Sports_Outdoor, Office_Supplies"
    - name: Region
      expr: customer.region
      comment: "Geographic region: Northeast, Southeast, Midwest, Southwest, West"
    - name: Channel
      expr: customer.channel
      comment: "Sales channel: retail, direct, ecomm"
    - name: Account Tier
      expr: customer.account_tier
      comment: "Customer tier: gold, silver, bronze"
    - name: Forecast Model
      expr: forecast_model
      comment: "AI model used by ai_forecast() for this series"
  measures:
    - name: Forecast Accuracy
      expr: AVG(accuracy_pct)
      comment: "MAPE-style accuracy, 1.0 = perfect. Applied to any grain the user specifies."
    - name: Revenue Opportunity
      expr: SUM(revenue_gap_usd) FILTER (WHERE revenue_gap_usd > 0)
      comment: "Cumulative missed demand in USD -- only counts under-forecasts, not over-forecasts."
    - name: Forecast Bias
      expr: AVG(unit_variance)
      comment: "Average unit variance. Positive = systematic under-forecast, negative = over-forecast."
    - name: Model Confidence
      expr: AVG(1 - confidence_width / NULLIF(forecasted_units, 0))
      comment: "How narrow the AI confidence interval is. Higher = more reliable series."
    - name: Weeks at Risk
      expr: COUNT(DISTINCT week_start_date) FILTER (WHERE accuracy_pct < 0.70)
      comment: "Number of weeks with forecast accuracy below 70% in the selected period."
    - name: Total Actual Revenue
      expr: SUM(actual_revenue_usd)
      comment: "Total actual shipment revenue in USD."
    - name: Total Forecasted Revenue
      expr: SUM(forecasted_revenue_usd)
      comment: "Total AI-forecasted revenue in USD."
    - name: Confidence Interval Hit Rate
      expr: AVG(CAST(is_within_confidence_interval AS DOUBLE))
      comment: "Fraction of weeks where actuals fell within the AI confidence interval. Higher = better calibrated model."
$$
""")

print("Created gold.demand_forecast_metrics")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Metric View: gold.revenue_opportunity_metrics
# MAGIC Trailing 52-week revenue opportunity analysis with customer/SKU joins.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.revenue_opportunity_metrics
WITH METRICS
LANGUAGE YAML
AS $$
  version: 1.1
  comment: "Revenue opportunity analysis over trailing 52 weeks. Surfaces customer-SKU pairs where ai_forecast() under-predicted demand. The 1M dollar gap emerges organically from opportunity SKUs."
  source: {CATALOG}.gold.weekly_forecast_vs_actuals
  filter: week_start_date >= DATE_ADD(CURRENT_DATE(), -364)
  joins:
    - name: customer
      source: {CATALOG}.raw.dim_customers
      "on": source.customer_id = customer.customer_id
    - name: sku
      source: {CATALOG}.raw.dim_skus
      "on": source.sku_id = sku.sku_id
  dimensions:
    - name: Customer Name
      expr: customer.customer_name
    - name: SKU Name
      expr: sku.sku_name_canonical
    - name: Product Category
      expr: sku.product_category
    - name: Region
      expr: customer.region
    - name: Channel
      expr: customer.channel
  measures:
    - name: Trailing 52W Revenue Gap
      expr: SUM(revenue_gap_usd) FILTER (WHERE revenue_gap_usd > 0)
      comment: "Cumulative missed demand revenue over trailing 52 weeks. Sort DESC for biggest opportunities."
    - name: Average Accuracy
      expr: AVG(accuracy_pct)
      comment: "Average forecast accuracy over trailing 52 weeks."
    - name: Weeks Consistently Under-Forecasted
      expr: COUNT(DISTINCT week_start_date) FILTER (WHERE unit_variance > 0)
      comment: "Weeks where actuals exceeded forecast -- distinguishes chronic under-forecast from one-time events."
    - name: Average Confidence Width
      expr: AVG(confidence_width)
      comment: "Average forecast confidence interval width -- high width indicates volatile, unreliable series."
$$
""")

print("Created gold.revenue_opportunity_metrics")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Metric View: gold.brand_manager_metrics
# MAGIC Brand manager performance. Customer dim join provides the round-robin BM assignment.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.brand_manager_metrics
WITH METRICS
LANGUAGE YAML
AS $$
  version: 1.1
  comment: "Brand manager forecast performance metrics. Enables coaching conversations and workload balancing across the team."
  source: {CATALOG}.gold.weekly_forecast_vs_actuals
  filter: week_start_date >= DATE_ADD(CURRENT_DATE(), -364)
  joins:
    - name: customer
      source: {CATALOG}.raw.dim_customers
      "on": source.customer_id = customer.customer_id
    - name: sku
      source: {CATALOG}.raw.dim_skus
      "on": source.sku_id = sku.sku_id
  dimensions:
    - name: Customer Name
      expr: customer.customer_name
    - name: Region
      expr: customer.region
    - name: Product Category
      expr: sku.product_category
    - name: Account Tier
      expr: customer.account_tier
  measures:
    - name: Accounts Managed
      expr: COUNT(DISTINCT customer.customer_name)
      comment: "Number of distinct customer accounts."
    - name: SKUs Covered
      expr: COUNT(DISTINCT sku.sku_name_canonical)
      comment: "Number of distinct SKUs across accounts."
    - name: Average Accuracy
      expr: AVG(accuracy_pct)
      comment: "Average forecast accuracy across all series."
    - name: Total Revenue Opportunity
      expr: SUM(revenue_gap_usd) FILTER (WHERE revenue_gap_usd > 0)
      comment: "Total missed demand revenue."
    - name: Weeks Below 70 Percent
      expr: COUNT(DISTINCT week_start_date) FILTER (WHERE accuracy_pct < 0.70)
      comment: "Number of weeks where any series fell below 70% accuracy."
    - name: Average Confidence Width
      expr: AVG(confidence_width)
      comment: "Average forecast confidence interval width."
$$
""")

print("Created gold.brand_manager_metrics")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Metric View: gold.seasonal_demand_metrics
# MAGIC Monthly seasonal pattern analysis by product category with SKU join.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.seasonal_demand_metrics
WITH METRICS
LANGUAGE YAML
AS $$
  version: 1.1
  comment: "Seasonal demand patterns by product category. Shows whether ai_forecast() correctly captured seasonality like Q4 holiday lift for Electronics/Sports_Outdoor and Q2 spring lift for Home_Kitchen."
  source: {CATALOG}.gold.weekly_forecast_vs_actuals
  joins:
    - name: sku
      source: {CATALOG}.raw.dim_skus
      "on": source.sku_id = sku.sku_id
  dimensions:
    - name: Month
      expr: DATE_TRUNC('MONTH', week_start_date)
      comment: "Calendar month"
    - name: Month Name
      expr: >
        CASE MONTH(week_start_date)
          WHEN 1 THEN 'January' WHEN 2 THEN 'February' WHEN 3 THEN 'March'
          WHEN 4 THEN 'April' WHEN 5 THEN 'May' WHEN 6 THEN 'June'
          WHEN 7 THEN 'July' WHEN 8 THEN 'August' WHEN 9 THEN 'September'
          WHEN 10 THEN 'October' WHEN 11 THEN 'November' WHEN 12 THEN 'December'
        END
      comment: "Month name for display"
    - name: Product Category
      expr: sku.product_category
      comment: "Product category: Electronics, Home_Kitchen, Health_Beauty, Sports_Outdoor, Office_Supplies"
    - name: Quarter
      expr: DATE_TRUNC('QUARTER', week_start_date)
  measures:
    - name: Total Forecasted Units
      expr: SUM(forecasted_units)
      comment: "Total ai_forecast() predicted units for the period."
    - name: Total Actual Units
      expr: SUM(actual_units)
      comment: "Total actual shipped units for the period."
    - name: Seasonal Accuracy
      expr: AVG(accuracy_pct)
      comment: "Average accuracy -- shows how well ai_forecast() captured seasonal patterns."
    - name: Seasonal Variance Units
      expr: SUM(actual_units) - SUM(forecasted_units)
      comment: "Difference between actual and forecasted units. Positive = under-forecasted."
    - name: Weeks in Period
      expr: COUNT(DISTINCT week_start_date)
      comment: "Number of distinct weeks with data in the period."
$$
""")

print("Created gold.seasonal_demand_metrics")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Metric View: gold.inventory_risk_metrics
# MAGIC Primary metric view for the Inventory & Channel Genie.
# MAGIC Uses YAML join directly from silver.inventory to dim_skus -- no base view needed.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.inventory_risk_metrics
WITH METRICS
LANGUAGE YAML
AS $$
  version: 1.1
  comment: "Inventory risk metrics for supply chain analysis. Measures stockout risk, days of supply, and reorder urgency across SKUs and warehouse regions."
  source: {CATALOG}.silver.inventory
  joins:
    - name: sku
      source: {CATALOG}.raw.dim_skus
      "on": source.sku_id = sku.sku_id
  dimensions:
    - name: Snapshot Date
      expr: snapshot_date
      comment: "Inventory snapshot date"
    - name: SKU Name
      expr: sku_name
      comment: "Canonical SKU product name"
    - name: Product Category
      expr: product_category
      comment: "Product category"
    - name: Warehouse Region
      expr: warehouse_region
      comment: "Distribution center region: Northeast, Southeast, Midwest, Southwest, West"
    - name: Risk Level
      expr: risk_level
      comment: "Inventory risk level: critical (DOS < 7), low (DOS < 14), adequate"
  measures:
    - name: Stockout Risk Score
      expr: AVG(CASE WHEN days_of_supply < 7 THEN 1.0 WHEN days_of_supply < 14 THEN 0.5 ELSE 0 END)
      comment: "Portfolio-level risk score 0-1. Higher = more SKUs at stockout risk."
    - name: Days of Supply
      expr: AVG(days_of_supply)
      comment: "Average days of supply across SKUs. Express as 'X days'."
    - name: Reorder Urgency
      expr: COUNT(DISTINCT sku_id) FILTER (WHERE days_of_supply < sku.lead_time_days)
      comment: "Count of SKUs already past their reorder point -- these need immediate action."
    - name: Total On Hand Units
      expr: SUM(on_hand_units)
      comment: "Total units currently on hand across selected SKUs/regions."
    - name: Total On Order Units
      expr: SUM(on_order_units)
      comment: "Total units currently on order (in transit or on PO)."
    - name: Stockout Count
      expr: COUNT(DISTINCT sku_id) FILTER (WHERE stockout_flag = TRUE)
      comment: "Number of SKUs currently at zero inventory."
    - name: Recommended Reorder Quantity
      expr: SUM(GREATEST(0, safety_stock_units - on_hand_units + sku.lead_time_days * (on_hand_units / NULLIF(days_of_supply, 0))))
      comment: "Total recommended reorder quantity based on safety stock and lead time."
$$
""")

print("Created gold.inventory_risk_metrics")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Base View: gold.inventory_forecast_alignment_base
# MAGIC Required because we need a CTE to aggregate 4-week forward forecast demand.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.inventory_forecast_alignment_base
COMMENT 'Inventory position vs 4-week AI-forecasted demand. CTE aggregation required before metric view layer.'
AS
WITH latest_forecasts AS (
    SELECT *
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY customer_id, sku_id, forecast_date
                ORDER BY generated_at DESC
            ) AS _rn
        FROM {CATALOG}.gold.ai_forecasts
    )
    WHERE _rn = 1
),
forecast_4w AS (
    SELECT
        sku_id,
        SUM(forecasted_units) AS forecasted_demand_4w
    FROM latest_forecasts
    WHERE forecast_date BETWEEN CURRENT_DATE() AND DATE_ADD(CURRENT_DATE(), 28)
    GROUP BY sku_id
)
SELECT
    i.snapshot_date,
    i.sku_id,
    i.sku_name,
    i.product_category,
    i.warehouse_region,
    i.on_hand_units,
    i.on_order_units,
    i.safety_stock_units,
    i.days_of_supply,
    i.risk_level,
    i.lead_time_days,
    ROUND(f4.forecasted_demand_4w, 0) AS forecasted_demand_4w,
    ROUND((i.on_hand_units + i.on_order_units) / NULLIF(f4.forecasted_demand_4w, 0), 2) AS forecast_coverage_ratio
FROM {CATALOG}.silver.inventory i
JOIN forecast_4w f4 ON i.sku_id = f4.sku_id
WHERE i.snapshot_date = (SELECT MAX(snapshot_date) FROM {CATALOG}.silver.inventory)
""")

print("Created gold.inventory_forecast_alignment_base")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Metric View: gold.inventory_forecast_metrics
# MAGIC Cross-Genie alignment: inventory vs AI forecast demand.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.gold.inventory_forecast_metrics
WITH METRICS
LANGUAGE YAML
AS $$
  version: 1.1
  comment: "Inventory vs AI forecast alignment metrics. Forecast Coverage below 1.0 means current inventory cannot satisfy ai_forecast() predicted demand for the next 4 weeks."
  source: {CATALOG}.gold.inventory_forecast_alignment_base
  dimensions:
    - name: SKU Name
      expr: sku_name
      comment: "Canonical SKU product name"
    - name: Product Category
      expr: product_category
    - name: Warehouse Region
      expr: warehouse_region
      comment: "Distribution center region"
    - name: Coverage Status
      expr: >
        CASE
          WHEN forecast_coverage_ratio < 0.5 THEN 'Critical Shortage'
          WHEN forecast_coverage_ratio < 0.8 THEN 'At Risk'
          WHEN forecast_coverage_ratio < 1.0 THEN 'Tight'
          ELSE 'Adequate'
        END
      comment: "Inventory coverage status relative to AI-forecasted 4-week demand"
  measures:
    - name: Forecast Coverage
      expr: SUM(on_hand_units) / NULLIF(SUM(forecasted_demand_4w), 0)
      comment: "Ratio of current on-hand inventory to 4-week AI forecast demand. Below 1.0 = insufficient inventory."
    - name: Total Forecasted Demand 4W
      expr: SUM(forecasted_demand_4w)
      comment: "Total AI-forecasted demand for the next 4 weeks."
    - name: Available Inventory
      expr: SUM(on_hand_units + on_order_units)
      comment: "Total available inventory (on-hand plus on-order)."
    - name: Shortage SKUs
      expr: COUNT(DISTINCT sku_id) FILTER (WHERE forecast_coverage_ratio < 1.0)
      comment: "Number of SKUs where current inventory is insufficient to meet 4-week AI forecast demand."
$$
""")

print("Created gold.inventory_forecast_metrics")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validation

# COMMAND ----------

print("=== Gold Layer Summary ===")

base_views = [
    "weekly_forecast_vs_actuals",
    "inventory_forecast_alignment_base",
]

metric_views = [
    "demand_forecast_metrics",
    "revenue_opportunity_metrics",
    "brand_manager_metrics",
    "seasonal_demand_metrics",
    "inventory_risk_metrics",
    "inventory_forecast_metrics",
]

print("\nBase Views:")
for v in base_views:
    try:
        count = spark.sql(f"SELECT COUNT(*) AS cnt FROM {CATALOG}.gold.{v}").first()["cnt"]
        print(f"  {v}: {count:,} rows")
    except Exception as e:
        print(f"  {v}: ERROR - {str(e)[:100]}")

print("\nMetric Views:")
for v in metric_views:
    try:
        desc = spark.sql(f"DESCRIBE TABLE EXTENDED {CATALOG}.gold.{v}").collect()
        print(f"  {v}: OK (metric view registered)")
    except Exception as e:
        print(f"  {v}: ERROR - {str(e)[:100]}")

# Test a metric view query
print("\n=== Sample Metric View Query: Revenue Opportunity by Region ===")
try:
    spark.sql(f"""
        SELECT
            Region,
            MEASURE(`Trailing 52W Revenue Gap`) AS revenue_gap,
            MEASURE(`Average Accuracy`) AS avg_accuracy,
            MEASURE(`Weeks Consistently Under-Forecasted`) AS weeks_under
        FROM {CATALOG}.gold.revenue_opportunity_metrics
        GROUP BY Region
        ORDER BY revenue_gap DESC
    """).show(truncate=False)
except Exception as e:
    print(f"  Query error (expected if ai_forecasts not yet populated): {str(e)[:150]}")
