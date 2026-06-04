# Databricks notebook source
# MAGIC %md
# MAGIC # Brand Intelligence Demo - Gold Layer: ai_forecast()
# MAGIC
# MAGIC Runs ai_forecast() over silver.actuals grouped by customer_id + sku_id.
# MAGIC
# MAGIC **This notebook runs on a SQL warehouse** (set via `warehouse_id` in the job config)
# MAGIC so that ai_forecast() is available. All cells must be SQL.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE IF NOT EXISTS brand_intel_demo.gold.ai_forecasts (
# MAGIC     forecast_run_id STRING COMMENT 'UUID per pipeline run -- enables comparing forecast vintages',
# MAGIC     customer_id STRING COMMENT 'FK to dim_customers',
# MAGIC     sku_id STRING COMMENT 'FK to dim_skus (canonical, post-resolution)',
# MAGIC     forecast_date DATE COMMENT 'Week being forecasted (Monday)',
# MAGIC     forecasted_units DOUBLE COMMENT 'ai_forecast() point estimate (yhat)',
# MAGIC     forecast_lower_bound DOUBLE COMMENT '80 pct confidence interval lower bound',
# MAGIC     forecast_upper_bound DOUBLE COMMENT '80 pct confidence interval upper bound',
# MAGIC     confidence_width DOUBLE COMMENT 'Upper minus lower bound; wide = high uncertainty',
# MAGIC     model_used STRING COMMENT 'Model selected by AutoML (prophet, arima, etc.)',
# MAGIC     generated_at TIMESTAMP COMMENT 'When this forecast was produced',
# MAGIC     training_weeks INT COMMENT 'Number of weeks of actuals used for training'
# MAGIC )
# MAGIC USING DELTA
# MAGIC COMMENT 'AI-generated 52-week forward forecasts per customer-SKU series via ai_forecast()'

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS brand_intel_demo.gold._forecast_input_staging

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE brand_intel_demo.gold._forecast_input_staging AS
# MAGIC WITH weekly AS (
# MAGIC     SELECT
# MAGIC         customer_id,
# MAGIC         sku_id,
# MAGIC         week_start_date AS ds,
# MAGIC         SUM(actual_units) AS y
# MAGIC     FROM brand_intel_demo.silver.actuals
# MAGIC     WHERE sku_id != 'UNRESOLVED'
# MAGIC     GROUP BY customer_id, sku_id, week_start_date
# MAGIC ),
# MAGIC series_with_history AS (
# MAGIC     SELECT customer_id, sku_id, COUNT(DISTINCT ds) AS weeks
# MAGIC     FROM weekly
# MAGIC     GROUP BY customer_id, sku_id
# MAGIC     HAVING COUNT(DISTINCT ds) >= 12
# MAGIC )
# MAGIC SELECT w.customer_id, w.sku_id, w.ds, w.y
# MAGIC FROM weekly w
# MAGIC JOIN series_with_history s ON w.customer_id = s.customer_id AND w.sku_id = s.sku_id

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   COUNT(DISTINCT customer_id, sku_id) AS series_to_forecast,
# MAGIC   COUNT(*) AS total_weekly_observations
# MAGIC FROM brand_intel_demo.gold._forecast_input_staging

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2a: Backtest forecasts (train on data up to 26 weeks ago, predict recent 26 weeks)
# MAGIC This creates the overlap between forecast dates and actuals needed for accuracy metrics.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS brand_intel_demo.gold._forecast_backtest_staging

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE brand_intel_demo.gold._forecast_backtest_staging AS
# MAGIC SELECT customer_id, sku_id, ds, y
# MAGIC FROM brand_intel_demo.gold._forecast_input_staging
# MAGIC WHERE ds < DATE_ADD(CURRENT_DATE(), -182)

# COMMAND ----------

# MAGIC %sql
# MAGIC INSERT INTO brand_intel_demo.gold.ai_forecasts
# MAGIC SELECT
# MAGIC     UUID() AS forecast_run_id,
# MAGIC     customer_id,
# MAGIC     sku_id,
# MAGIC     ds AS forecast_date,
# MAGIC     y_forecast AS forecasted_units,
# MAGIC     y_lower AS forecast_lower_bound,
# MAGIC     y_upper AS forecast_upper_bound,
# MAGIC     (y_upper - y_lower) AS confidence_width,
# MAGIC     'prophet' AS model_used,
# MAGIC     CURRENT_TIMESTAMP() AS generated_at,
# MAGIC     NULL AS training_weeks
# MAGIC FROM ai_forecast(
# MAGIC     TABLE(brand_intel_demo.gold._forecast_backtest_staging),
# MAGIC     horizon => CURRENT_DATE(),
# MAGIC     time_col => 'ds',
# MAGIC     value_col => 'y',
# MAGIC     group_col => ARRAY('customer_id', 'sku_id'),
# MAGIC     frequency => 'W',
# MAGIC     prediction_interval_width => 0.8
# MAGIC )
# MAGIC WHERE ds >= DATE_ADD(CURRENT_DATE(), -182)

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS brand_intel_demo.gold._forecast_backtest_staging

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2b: Forward forecasts (train on all data, predict 52 weeks ahead)

# COMMAND ----------

# MAGIC %sql
# MAGIC INSERT INTO brand_intel_demo.gold.ai_forecasts
# MAGIC SELECT
# MAGIC     UUID() AS forecast_run_id,
# MAGIC     customer_id,
# MAGIC     sku_id,
# MAGIC     ds AS forecast_date,
# MAGIC     y_forecast AS forecasted_units,
# MAGIC     y_lower AS forecast_lower_bound,
# MAGIC     y_upper AS forecast_upper_bound,
# MAGIC     (y_upper - y_lower) AS confidence_width,
# MAGIC     'prophet' AS model_used,
# MAGIC     CURRENT_TIMESTAMP() AS generated_at,
# MAGIC     NULL AS training_weeks
# MAGIC FROM ai_forecast(
# MAGIC     TABLE(brand_intel_demo.gold._forecast_input_staging),
# MAGIC     horizon => DATE_ADD(CURRENT_DATE(), 364),
# MAGIC     time_col => 'ds',
# MAGIC     value_col => 'y',
# MAGIC     group_col => ARRAY('customer_id', 'sku_id'),
# MAGIC     frequency => 'W',
# MAGIC     prediction_interval_width => 0.8
# MAGIC )

# COMMAND ----------

# MAGIC %sql
# MAGIC MERGE INTO brand_intel_demo.gold.ai_forecasts f
# MAGIC USING (
# MAGIC     SELECT customer_id, sku_id, COUNT(DISTINCT ds) AS weeks
# MAGIC     FROM brand_intel_demo.gold._forecast_input_staging
# MAGIC     GROUP BY customer_id, sku_id
# MAGIC ) s
# MAGIC ON f.customer_id = s.customer_id AND f.sku_id = s.sku_id AND f.training_weeks IS NULL
# MAGIC WHEN MATCHED THEN UPDATE SET f.training_weeks = s.weeks

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS brand_intel_demo.gold._forecast_input_staging

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   COUNT(*) AS forecast_rows,
# MAGIC   COUNT(DISTINCT customer_id, sku_id) AS distinct_series,
# MAGIC   MIN(forecast_date) AS min_date,
# MAGIC   MAX(forecast_date) AS max_date
# MAGIC FROM brand_intel_demo.gold.ai_forecasts
# MAGIC WHERE generated_at >= DATE_ADD(CURRENT_TIMESTAMP(), -1)

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC     f.customer_id,
# MAGIC     f.sku_id,
# MAGIC     s._is_opportunity_sku,
# MAGIC     ROUND(AVG(f.forecasted_units), 1) AS avg_forecast,
# MAGIC     ROUND(AVG(f.confidence_width), 1) AS avg_confidence_width
# MAGIC FROM brand_intel_demo.gold.ai_forecasts f
# MAGIC JOIN brand_intel_demo.raw.dim_skus s ON f.sku_id = s.sku_id
# MAGIC WHERE s._is_opportunity_sku = TRUE
# MAGIC   AND f.generated_at >= DATE_ADD(CURRENT_TIMESTAMP(), -1)
# MAGIC GROUP BY f.customer_id, f.sku_id, s._is_opportunity_sku
# MAGIC ORDER BY avg_forecast DESC
# MAGIC LIMIT 15
