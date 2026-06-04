# Databricks notebook source
# MAGIC %md
# MAGIC # Drop ai_forecasts for full rebuild
# MAGIC Called during full_refresh runs to ensure clean forecast data.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP TABLE IF EXISTS brand_intel_demo.gold.ai_forecasts
