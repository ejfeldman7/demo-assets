"""
cost.py — LIVE COST PROXY.

    est_cost_usd = elapsed_hours * dbu_per_hr * price_per_dbu

These rates are deliberately approximate. The whole point of Watchtower is that a
*live* number is available immediately, where settled cost lags ~24h. The estimate is
always labeled as such; ground truth comes later from the system.billing.usage
reconciliation (see src/db/uc_ddl.sql). Rates are overridable via env for tuning.
"""

from __future__ import annotations

import os

# Rough blended list price ($/DBU). Overridable; reconciliation supplies the truth.
PRICE_PER_DBU = float(os.environ.get("WT_PRICE_PER_DBU", "0.55"))
DEFAULT_DBU_PER_HR = float(os.environ.get("WT_DEFAULT_DBU_HR", "10"))

# Approximate serverless SQL warehouse DBU/hr by t-shirt size.
SQL_WAREHOUSE_DBU_PER_HR = {
    "2X-Small": 4, "X-Small": 6, "Small": 12, "Medium": 24, "Large": 40,
    "X-Large": 80, "2X-Large": 144, "3X-Large": 272, "4X-Large": 528,
}


def dbu_per_hr(workload_type: str, meta: dict | None) -> float:
    """Best-effort DBU/hr for a workload, from whatever compute metadata we have."""
    meta = meta or {}
    if workload_type == "query":
        return float(SQL_WAREHOUSE_DBU_PER_HR.get(meta.get("warehouse_size"), DEFAULT_DBU_PER_HR))
    if workload_type == "cluster":
        # ~ cores as a crude DBU proxy for all-purpose compute.
        cores = meta.get("cluster_cores")
        return float(cores) if cores else DEFAULT_DBU_PER_HR
    # jobs / pipelines / serving: fall back to the default rate.
    return DEFAULT_DBU_PER_HR


def estimate(elapsed_sec: float | None, rate: float, price: float | None = None) -> float:
    """Return the proxy cost in USD (rounded). None/0 elapsed -> 0.0.

    `price` is $/DBU; pass the live value from system.billing.list_prices when available,
    else the PRICE_PER_DBU default is used.
    """
    hours = (elapsed_sec or 0.0) / 3600.0
    return round(hours * rate * (price if price is not None else PRICE_PER_DBU), 4)
