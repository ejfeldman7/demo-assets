"""
Analytical Tools for Proactive Analysis Agent.

Five SQL-based tools that run parameterized queries directly against Gold views
via the Databricks SQL Warehouse. These are the Phase 1 sweep tools — deterministic,
bounded, and fast. They return structured dicts for the Foundation Model API.

Design decision: These tools use direct SQL, not Genie. Genie is reserved for
Phase 2 drill-downs where flexible natural language is valuable. Phase 1 needs
performance and determinism — Genie adds latency and non-determinism to structured
analytical operations that run unattended.
"""

import os
import logging
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

logger = logging.getLogger(__name__)

CATALOG = os.environ.get("CATALOG", "brand_intel_demo")
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "")

# ---------------------------------------------------------------------------
# SQL execution helper
# ---------------------------------------------------------------------------

def _execute_sql(w: WorkspaceClient, sql: str) -> list[dict]:
    """Execute SQL via the Statement API and return rows as list of dicts."""
    try:
        response = w.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID,
            statement=sql,
            wait_timeout="50s",
        )
        # If statement is still running, poll until complete
        if response.status and response.status.state in (
            StatementState.PENDING, StatementState.RUNNING
        ):
            import time
            statement_id = response.statement_id
            for _ in range(24):  # up to ~2 more minutes
                time.sleep(5)
                response = w.statement_execution.get_statement(statement_id)
                if response.status and response.status.state not in (
                    StatementState.PENDING, StatementState.RUNNING
                ):
                    break
        if response.status and response.status.state == StatementState.SUCCEEDED:
            if response.manifest and response.result and response.result.data_array:
                columns = [col.name for col in response.manifest.schema.columns]
                rows = []
                for row_data in response.result.data_array:
                    row = {}
                    for j, col in enumerate(columns):
                        val = row_data[j]
                        # Try numeric conversion
                        if val is not None:
                            try:
                                val = float(val)
                                if val == int(val):
                                    val = int(val)
                            except (ValueError, TypeError):
                                pass
                        row[col] = val
                    rows.append(row)
                return rows
            return []
        else:
            error = response.status.error if response.status else "Unknown error"
            logger.error("SQL execution failed: %s", error)
            return []
    except Exception as e:
        logger.error("SQL execution error: %s | SQL: %s", e, sql[:200])
        return []


# ---------------------------------------------------------------------------
# Tool 1: variance_baseline
# ---------------------------------------------------------------------------

def variance_baseline(
    w: WorkspaceClient,
    metric: str = "accuracy_pct",
    lookback_weeks: int = 8,
    sigma_threshold: float = 1.5,
    sku: Optional[str] = None,
) -> list[dict]:
    """Compute trailing mean/stddev per SKU and flag anomalies beyond sigma_threshold.

    Scans the demand forecast base view for the specified metric.
    Returns list of {sku, metric, current_value, mean, stddev, sigma, direction}
    for any SKU beyond the threshold.

    Valid metrics: accuracy_pct, unit_variance, revenue_gap_usd, actual_units
    """
    # Whitelist metrics to prevent SQL injection
    allowed_metrics = {
        "accuracy_pct", "unit_variance", "revenue_gap_usd",
        "actual_units", "forecasted_units", "confidence_width",
    }
    if metric not in allowed_metrics:
        logger.error("Invalid metric: %s", metric)
        return []

    sku_filter = f"AND sku_id = '{sku}'" if sku else ""

    sql = f"""
    WITH weekly AS (
        SELECT
            sku_id,
            week_start_date,
            AVG({metric}) AS metric_value
        FROM {CATALOG}.gold.weekly_forecast_vs_actuals
        WHERE week_start_date >= DATE_ADD(CURRENT_DATE(), -{lookback_weeks * 7 + 7})
        {sku_filter}
        GROUP BY sku_id, week_start_date
    ),
    stats AS (
        SELECT
            sku_id,
            week_start_date,
            metric_value,
            AVG(metric_value) OVER (
                PARTITION BY sku_id
                ORDER BY week_start_date
                ROWS BETWEEN {lookback_weeks} PRECEDING AND 1 PRECEDING
            ) AS trailing_mean,
            STDDEV(metric_value) OVER (
                PARTITION BY sku_id
                ORDER BY week_start_date
                ROWS BETWEEN {lookback_weeks} PRECEDING AND 1 PRECEDING
            ) AS trailing_stddev,
            ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY week_start_date DESC) AS rn
        FROM weekly
    )
    SELECT
        sku_id AS sku,
        '{metric}' AS metric,
        ROUND(metric_value, 4) AS current_value,
        ROUND(trailing_mean, 4) AS mean,
        ROUND(trailing_stddev, 4) AS stddev,
        ROUND((metric_value - trailing_mean) / NULLIF(trailing_stddev, 0), 2) AS sigma,
        CASE
            WHEN metric_value > trailing_mean THEN 'above'
            ELSE 'below'
        END AS direction
    FROM stats
    WHERE rn = 1
      AND trailing_stddev > 0
      AND ABS((metric_value - trailing_mean) / NULLIF(trailing_stddev, 0)) >= {sigma_threshold}
    ORDER BY ABS((metric_value - trailing_mean) / NULLIF(trailing_stddev, 0)) DESC
    LIMIT 50
    """
    return _execute_sql(w, sql)


# ---------------------------------------------------------------------------
# Tool 2: forecast_vs_inventory
# ---------------------------------------------------------------------------

def forecast_vs_inventory(
    w: WorkspaceClient,
    horizon_days: int = 60,
    sku: Optional[str] = None,
) -> list[dict]:
    """Compare current inventory position against forecasted demand.

    Joins latest inventory snapshot with 4-week forward forecast demand.
    Returns list of {sku, product_category, warehouse_region, days_of_supply,
    on_hand_units, forecasted_demand, coverage_ratio, risk_type, severity}.
    """
    sku_filter = f"AND i.sku_id = '{sku}'" if sku else ""

    sql = f"""
    WITH latest_inventory AS (
        SELECT *
        FROM {CATALOG}.silver.inventory
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {CATALOG}.silver.inventory)
    ),
    forecast_demand AS (
        SELECT
            sku_id,
            SUM(forecasted_units) AS forecasted_demand
        FROM {CATALOG}.gold.ai_forecasts
        WHERE forecast_date BETWEEN CURRENT_DATE() AND DATE_ADD(CURRENT_DATE(), {horizon_days})
        GROUP BY sku_id
    )
    SELECT
        i.sku_id AS sku,
        i.product_category,
        i.warehouse_region,
        ROUND(i.days_of_supply, 1) AS days_of_supply,
        ROUND(i.on_hand_units, 0) AS on_hand_units,
        ROUND(i.on_order_units, 0) AS on_order_units,
        ROUND(COALESCE(f.forecasted_demand, 0), 0) AS forecasted_demand,
        ROUND((i.on_hand_units + i.on_order_units) / NULLIF(f.forecasted_demand, 0), 2) AS coverage_ratio,
        CASE
            WHEN i.days_of_supply < 7 THEN 'stockout'
            WHEN (i.on_hand_units + i.on_order_units) / NULLIF(f.forecasted_demand, 0) > 3.0 THEN 'overstock'
            ELSE NULL
        END AS risk_type,
        CASE
            WHEN i.days_of_supply < 7 THEN 'critical'
            WHEN i.days_of_supply < 14 THEN 'urgent'
            WHEN i.days_of_supply < 30 THEN 'notable'
            ELSE NULL
        END AS severity
    FROM latest_inventory i
    LEFT JOIN forecast_demand f ON i.sku_id = f.sku_id
    WHERE 1=1
    {sku_filter}
    AND (
        i.days_of_supply < 30
        OR (i.on_hand_units + i.on_order_units) / NULLIF(f.forecasted_demand, 0) > 3.0
        OR i.stockout_flag = TRUE
    )
    ORDER BY i.days_of_supply ASC
    LIMIT 50
    """
    return _execute_sql(w, sql)


# ---------------------------------------------------------------------------
# Tool 3: compare_periods
# ---------------------------------------------------------------------------

def compare_periods(
    w: WorkspaceClient,
    metric: str = "accuracy_pct",
    window_days: int = 7,
    sku: Optional[str] = None,
) -> list[dict]:
    """Period-over-period comparison for any metric.

    Compares the last N days vs the prior N days for each SKU.
    Returns list of {sku, metric, period_a_value, period_b_value,
    delta, delta_pct, direction}.
    """
    allowed_metrics = {
        "accuracy_pct", "unit_variance", "revenue_gap_usd",
        "actual_units", "forecasted_units",
    }
    if metric not in allowed_metrics:
        logger.error("Invalid metric: %s", metric)
        return []

    sku_filter = f"AND sku_id = '{sku}'" if sku else ""

    sql = f"""
    WITH period_a AS (
        SELECT
            sku_id,
            AVG({metric}) AS value
        FROM {CATALOG}.gold.weekly_forecast_vs_actuals
        WHERE week_start_date >= DATE_ADD(CURRENT_DATE(), -{window_days})
        {sku_filter}
        GROUP BY sku_id
    ),
    period_b AS (
        SELECT
            sku_id,
            AVG({metric}) AS value
        FROM {CATALOG}.gold.weekly_forecast_vs_actuals
        WHERE week_start_date >= DATE_ADD(CURRENT_DATE(), -{window_days * 2})
          AND week_start_date < DATE_ADD(CURRENT_DATE(), -{window_days})
        {sku_filter}
        GROUP BY sku_id
    )
    SELECT
        a.sku_id AS sku,
        '{metric}' AS metric,
        ROUND(a.value, 4) AS period_a_value,
        ROUND(b.value, 4) AS period_b_value,
        ROUND(a.value - b.value, 4) AS delta,
        ROUND((a.value - b.value) / NULLIF(b.value, 0) * 100, 2) AS delta_pct,
        CASE
            WHEN a.value > b.value THEN 'increasing'
            WHEN a.value < b.value THEN 'decreasing'
            ELSE 'stable'
        END AS direction
    FROM period_a a
    JOIN period_b b ON a.sku_id = b.sku_id
    WHERE ABS(a.value - b.value) / NULLIF(b.value, 0) > 0.05
    ORDER BY ABS(a.value - b.value) / NULLIF(b.value, 0) DESC
    LIMIT 50
    """
    return _execute_sql(w, sql)


# ---------------------------------------------------------------------------
# Tool 4: channel_decomposition
# ---------------------------------------------------------------------------

def channel_decomposition(
    w: WorkspaceClient,
    sku: str,
    metric: str = "actual_units",
    window_days: int = 28,
) -> list[dict]:
    """Break an aggregate metric down by channel for a specific SKU.

    Used when variance_baseline flags a SKU whose aggregate looks stable
    but a channel shift is the underlying cause.
    Returns list of {sku, channel, metric, value, share_of_total, wow_change_pct}.
    """
    allowed_metrics = {
        "actual_units", "accuracy_pct", "revenue_gap_usd",
        "actual_revenue_usd", "unit_variance",
    }
    if metric not in allowed_metrics:
        logger.error("Invalid metric: %s", metric)
        return []

    agg = "SUM" if metric in ("actual_units", "actual_revenue_usd", "revenue_gap_usd") else "AVG"

    sql = f"""
    WITH current_period AS (
        SELECT
            f.sku_id,
            c.channel,
            {agg}(f.{metric}) AS value
        FROM {CATALOG}.gold.weekly_forecast_vs_actuals f
        JOIN {CATALOG}.raw.dim_customers c ON f.customer_id = c.customer_id
        WHERE f.week_start_date >= DATE_ADD(CURRENT_DATE(), -{window_days})
          AND f.sku_id = '{sku}'
        GROUP BY f.sku_id, c.channel
    ),
    prior_period AS (
        SELECT
            f.sku_id,
            c.channel,
            {agg}(f.{metric}) AS value
        FROM {CATALOG}.gold.weekly_forecast_vs_actuals f
        JOIN {CATALOG}.raw.dim_customers c ON f.customer_id = c.customer_id
        WHERE f.week_start_date >= DATE_ADD(CURRENT_DATE(), -{window_days * 2})
          AND f.week_start_date < DATE_ADD(CURRENT_DATE(), -{window_days})
          AND f.sku_id = '{sku}'
        GROUP BY f.sku_id, c.channel
    ),
    totals AS (
        SELECT SUM(value) AS total FROM current_period
    )
    SELECT
        cp.sku_id AS sku,
        cp.channel,
        '{metric}' AS metric,
        ROUND(cp.value, 2) AS value,
        ROUND(cp.value / NULLIF(t.total, 0) * 100, 1) AS share_of_total,
        ROUND((cp.value - pp.value) / NULLIF(pp.value, 0) * 100, 1) AS wow_change_pct
    FROM current_period cp
    LEFT JOIN prior_period pp ON cp.sku_id = pp.sku_id AND cp.channel = pp.channel
    CROSS JOIN totals t
    ORDER BY cp.value DESC
    """
    return _execute_sql(w, sql)


# ---------------------------------------------------------------------------
# Tool 5: correlate
# ---------------------------------------------------------------------------

def correlate(
    w: WorkspaceClient,
    metric_a: str = "actual_units",
    metric_b: str = "accuracy_pct",
    sku: Optional[str] = None,
    lookback_weeks: int = 8,
) -> list[dict]:
    """Test whether two metrics are moving together or diverging.

    Computes Pearson correlation over the lookback window and compares
    to the prior equal window. Diverging = correlation dropped significantly.
    Returns list of {sku, metric_a, metric_b, recent_corr, prior_corr, diverging}.
    """
    allowed_metrics = {
        "accuracy_pct", "unit_variance", "revenue_gap_usd",
        "actual_units", "forecasted_units", "confidence_width",
    }
    if metric_a not in allowed_metrics or metric_b not in allowed_metrics:
        logger.error("Invalid metrics: %s, %s", metric_a, metric_b)
        return []

    sku_filter = f"AND sku_id = '{sku}'" if sku else ""
    half_window = lookback_weeks * 7

    sql = f"""
    WITH weekly AS (
        SELECT
            sku_id,
            week_start_date,
            AVG({metric_a}) AS val_a,
            AVG({metric_b}) AS val_b
        FROM {CATALOG}.gold.weekly_forecast_vs_actuals
        WHERE week_start_date >= DATE_ADD(CURRENT_DATE(), -{half_window * 2})
        {sku_filter}
        GROUP BY sku_id, week_start_date
    ),
    recent AS (
        SELECT
            sku_id,
            CORR(val_a, val_b) AS correlation
        FROM weekly
        WHERE week_start_date >= DATE_ADD(CURRENT_DATE(), -{half_window})
        GROUP BY sku_id
        HAVING COUNT(*) >= 4
    ),
    prior AS (
        SELECT
            sku_id,
            CORR(val_a, val_b) AS correlation
        FROM weekly
        WHERE week_start_date < DATE_ADD(CURRENT_DATE(), -{half_window})
        GROUP BY sku_id
        HAVING COUNT(*) >= 4
    )
    SELECT
        r.sku_id AS sku,
        '{metric_a}' AS metric_a,
        '{metric_b}' AS metric_b,
        ROUND(r.correlation, 3) AS recent_corr,
        ROUND(p.correlation, 3) AS prior_corr,
        CASE
            WHEN ABS(r.correlation - p.correlation) > 0.3 THEN TRUE
            ELSE FALSE
        END AS diverging
    FROM recent r
    JOIN prior p ON r.sku_id = p.sku_id
    WHERE ABS(r.correlation - p.correlation) > 0.2
    ORDER BY ABS(r.correlation - p.correlation) DESC
    LIMIT 30
    """
    return _execute_sql(w, sql)


# ---------------------------------------------------------------------------
# Tool definitions for Foundation Model API function calling
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "variance_baseline",
            "description": (
                "Scans all SKUs for anomalies in a demand metric by computing trailing "
                "mean/stddev and flagging values beyond the sigma threshold. "
                "Use this as the primary sweep tool to detect unusual changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["accuracy_pct", "unit_variance", "revenue_gap_usd", "actual_units"],
                        "description": "The metric to scan for anomalies.",
                    },
                    "lookback_weeks": {
                        "type": "integer",
                        "description": "Number of weeks for the trailing baseline. Default 8.",
                    },
                    "sigma_threshold": {
                        "type": "number",
                        "description": "Standard deviations from mean to flag. Default 1.5.",
                    },
                    "sku": {
                        "type": "string",
                        "description": "Optional SKU ID to filter to a single SKU.",
                    },
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forecast_vs_inventory",
            "description": (
                "Compares current inventory position against AI-forecasted demand. "
                "Returns SKUs at stockout or overstock risk within the specified horizon."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "horizon_days": {
                        "type": "integer",
                        "description": "Forward-looking horizon in days. Default 60.",
                    },
                    "sku": {
                        "type": "string",
                        "description": "Optional SKU ID to filter to a single SKU.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_periods",
            "description": (
                "Period-over-period comparison for a metric. Compares the last N days "
                "against the prior N days to identify recent changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["accuracy_pct", "unit_variance", "revenue_gap_usd", "actual_units"],
                        "description": "The metric to compare across periods.",
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "Size of each comparison window in days. Default 7.",
                    },
                    "sku": {
                        "type": "string",
                        "description": "Optional SKU ID to filter.",
                    },
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "channel_decomposition",
            "description": (
                "Breaks a metric down by sales channel (retail, direct, ecomm) for a specific SKU. "
                "Use when an aggregate anomaly might be caused by a single channel shifting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {
                        "type": "string",
                        "description": "The SKU ID to decompose.",
                    },
                    "metric": {
                        "type": "string",
                        "enum": ["actual_units", "accuracy_pct", "revenue_gap_usd", "actual_revenue_usd"],
                        "description": "The metric to decompose by channel. Default actual_units.",
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "Lookback window in days. Default 28.",
                    },
                },
                "required": ["sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correlate",
            "description": (
                "Tests whether two metrics are moving together or diverging for SKUs. "
                "Use to detect leading indicators (e.g., demand rising while accuracy drops)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_a": {
                        "type": "string",
                        "enum": ["actual_units", "accuracy_pct", "unit_variance", "revenue_gap_usd"],
                        "description": "First metric.",
                    },
                    "metric_b": {
                        "type": "string",
                        "enum": ["actual_units", "accuracy_pct", "unit_variance", "revenue_gap_usd"],
                        "description": "Second metric.",
                    },
                    "sku": {
                        "type": "string",
                        "description": "Optional SKU ID to filter.",
                    },
                    "lookback_weeks": {
                        "type": "integer",
                        "description": "Lookback window in weeks. Default 8.",
                    },
                },
                "required": ["metric_a", "metric_b"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatcher — maps function name to callable
# ---------------------------------------------------------------------------

def dispatch_tool(w: WorkspaceClient, tool_name: str, arguments: dict) -> list[dict]:
    """Dispatch a tool call from the Foundation Model API to the correct function."""
    tools = {
        "variance_baseline": variance_baseline,
        "forecast_vs_inventory": forecast_vs_inventory,
        "compare_periods": compare_periods,
        "channel_decomposition": channel_decomposition,
        "correlate": correlate,
    }
    fn = tools.get(tool_name)
    if not fn:
        logger.error("Unknown tool: %s", tool_name)
        return [{"error": f"Unknown tool: {tool_name}"}]
    return fn(w=w, **arguments)
