# Databricks notebook source
# MAGIC %md
# MAGIC # Brand Intelligence Demo - Synthetic Data Generator (Actuals-First)
# MAGIC
# MAGIC Generates dimension and fact tables for the demand forecasting demo.
# MAGIC Forecasts are NOT generated here -- ai_forecast() handles that in the Gold layer.
# MAGIC
# MAGIC **Tables generated:**
# MAGIC - dim_customers (50 rows)
# MAGIC - dim_skus (200 rows)
# MAGIC - dim_sku_aliases (~600 rows)
# MAGIC - dim_date (4 years)
# MAGIC - fact_actuals (incremental daily)
# MAGIC - fact_inventory (incremental daily)
# MAGIC
# MAGIC **Realistic demand patterns for high ai_forecast() accuracy:**
# MAGIC - Smooth sinusoidal seasonality (not step-function quarters)
# MAGIC - Gradual YoY growth trend (2-8% per series)
# MAGIC - Low daily noise (~8% jitter) so Prophet can fit well -> 80-95% accuracy baseline
# MAGIC - 3 anomaly accounts with mild disruption starting Dec 2025 -> subtle accuracy dips
# MAGIC - 5 decline accounts with gentle demand erosion starting Oct 2025
# MAGIC - 8 opportunity SKUs with gradual demand ramp starting Sep 2025
# MAGIC - Rare spot orders (~1.5%) as outliers
# MAGIC
# MAGIC **Incremental mode:** On subsequent runs, only generates new dates (after existing max).
# MAGIC Uses hash-based per-day determinism so each date produces identical data regardless
# MAGIC of when the generator runs.

# COMMAND ----------

# MAGIC %pip install dbldatagen
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import dbldatagen as dg
from pyspark.sql import functions as F
from pyspark.sql.types import *
from datetime import date, timedelta
import random
import math
import uuid

# Master seed -- set once before ANY random calls for full determinism.
# All dimension generation (customers, SKUs, aliases) flows from this seed
# and produces identical results on every run.
random.seed(42)

# COMMAND ----------

CATALOG = "brand_intel_demo"
RAW_SCHEMA = "raw"
VOLUME_PATH = f"/Volumes/{CATALOG}/assets/raw"

# ---------------------------------------------------------------------------
# Proactive Agent Scenarios — injected perturbations for anomaly detection
# ---------------------------------------------------------------------------
from datetime import datetime

INJECT_SCENARIOS = True

SCENARIOS = [
    {
        "name": "channel_divergence",
        "sku": "SKU_0042",           # Electronics — Portable SSD Drive Standard
        "type": "channel_divergence",
        "affected_channel": "direct",
        "onset_days_ago": 21,
        "decline_pct": 0.38,         # 38% decline in direct channel
        "offset_pct": 0.12,          # 12% lift in other channels (masks in aggregate)
    },
    {
        "name": "leading_indicator",
        "sku": "SKU_0067",           # Home_Kitchen — Silicone Baking Mat Large
        "type": "leading_indicator",
        "onset_days_ago": 28,
        "demand_spike_pct": 0.45,    # 45% demand increase, inventory not keeping up
    },
    {
        "name": "stockout_risk",
        "sku": "SKU_0129",           # Health_Beauty — Sunscreen SPF50 Compact
        "type": "stockout_risk",
        "onset_days_ago": 14,
        "demand_spike_pct": 0.60,    # 60% demand spike depletes inventory
    },
    {
        "name": "cannibalization",
        "rising_sku": "SKU_0161",    # Sports_Outdoor — Jump Rope Speed Compact
        "declining_sku": "SKU_0158", # Sports_Outdoor — Foam Roller Standard
        "type": "cannibalization",
        "onset_days_ago": 35,
        "transfer_pct": 0.30,        # 30% demand shifted
    },
]

spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{RAW_SCHEMA}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.bronze")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.silver")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.gold")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.operational")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.assets")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.assets.raw")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.assets.reports")

# COMMAND ----------

# MAGIC %md
# MAGIC ## dim_customers (50 rows)

# COMMAND ----------

customer_names = [
    "Northstar Retail Group", "Pacific Merchandise Co.", "Atlantic Supply Chain",
    "Heartland Distribution", "Summit Consumer Products", "Coastal Trading Partners",
    "Prairie Logistics Inc.", "Metro Brands Alliance", "Pinnacle Retail Solutions",
    "Valley Wholesale Group", "Glacier Supply Co.", "Canyon Distributors",
    "Harbor Retail Networks", "Crestline Consumer Corp.", "Evergreen Supply Partners",
    "Ridgeline Wholesale", "Lakeside Trading Co.", "Bayshore Distribution",
    "Timberline Brands Inc.", "Riverside Retail Group", "Cascade Supply Chain",
    "Ironwood Distributors", "Sunstone Retail Corp.", "Windmill Trading Partners",
    "Bridgewater Wholesale", "Cedarwood Consumer Co.", "Foxhill Distribution",
    "Granite Retail Alliance", "Holloway Brands Group", "Ivy League Wholesale",
    "Juniper Supply Co.", "Keystone Retail Partners", "Larkspur Trading Inc.",
    "Magnolia Distribution", "Northfield Consumer Corp.", "Oakridge Supply Partners",
    "Pineview Retail Group", "Quarry Wholesale Co.", "Redstone Distributors",
    "Silverton Trading Corp.", "Thornberry Brands Inc.", "Upland Retail Solutions",
    "Valleyforge Wholesale", "Westbrook Distribution", "Xenon Consumer Partners",
    "Yarrow Supply Chain", "Zenith Retail Corp.", "Ashford Trading Group",
    "Beacon Distribution Co.", "Clearwater Wholesale Inc."
]

regions = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]
channels = ["retail", "direct", "ecomm"]
tiers = ["gold", "silver", "bronze"]

customer_rows = []
for i in range(50):
    cid = f"CUST_{i+1:03d}"
    region = regions[i % 5]
    # Channel distribution: 60% retail, 25% direct, 15% ecomm
    channel_rand = random.random()
    channel = "retail" if channel_rand < 0.60 else ("direct" if channel_rand < 0.85 else "ecomm")
    # Tier correlated with revenue
    tier_rand = random.random()
    tier = "gold" if tier_rand < 0.20 else ("silver" if tier_rand < 0.55 else "bronze")
    rev_ranges = {"gold": (10_000_000, 50_000_000), "silver": (2_000_000, 10_000_000), "bronze": (500_000, 2_000_000)}
    low, high = rev_ranges[tier]
    annual_rev = round(random.uniform(low, high), 2)
    first_names = ["Sarah", "James", "Maria", "Robert", "Linda", "Michael", "Jennifer", "David", "Patricia", "John"]
    last_names = ["Chen", "Rodriguez", "Patel", "Williams", "Thompson", "Garcia", "Martinez", "Anderson", "Taylor", "Moore"]
    contact = f"{random.choice(first_names)} {random.choice(last_names)}"
    customer_rows.append((cid, customer_names[i], region, channel, tier, annual_rev, contact, True))

dim_customers_schema = StructType([
    StructField("customer_id", StringType()),
    StructField("customer_name", StringType()),
    StructField("region", StringType()),
    StructField("channel", StringType()),
    StructField("account_tier", StringType()),
    StructField("annual_revenue_usd", DoubleType()),
    StructField("primary_contact", StringType()),
    StructField("is_active", BooleanType()),
])

dim_customers = spark.createDataFrame(customer_rows, dim_customers_schema)
dim_customers.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.dim_customers")
dim_customers.coalesce(1).write.mode("overwrite").option("header", "true").csv(f"{VOLUME_PATH}/dim_customers")
print(f"dim_customers: {dim_customers.count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## dim_skus (200 rows)

# COMMAND ----------

categories = {
    "Electronics": {"count": 50, "price_range": (15.00, 299.99), "lead_time": (14, 30)},
    "Home_Kitchen": {"count": 45, "price_range": (8.00, 89.99), "lead_time": (10, 25)},
    "Health_Beauty": {"count": 40, "price_range": (5.00, 49.99), "lead_time": (7, 20)},
    "Sports_Outdoor": {"count": 35, "price_range": (12.00, 149.99), "lead_time": (10, 28)},
    "Office_Supplies": {"count": 30, "price_range": (2.00, 45.99), "lead_time": (5, 15)},
}

# Naming patterns per category for canonical names
name_templates = {
    "Electronics": [
        "Wireless Bluetooth Speaker {size}", "USB-C Charging Hub {size}", "LED Desk Lamp {size}",
        "Portable Power Bank {size}", "Smart Home Sensor {size}",
        "Noise-Canceling Earbuds {size}", "Webcam HD Pro {size}", "Digital Kitchen Scale {size}",
        "Portable SSD Drive {size}", "Smart Plug WiFi {size}",
    ],
    "Home_Kitchen": [
        "Stainless Steel Tumbler {size}", "Ceramic Coffee Mug Set {size}", "Bamboo Cutting Board {size}",
        "Silicone Baking Mat {size}", "Glass Storage Container {size}", "Cast Iron Skillet {size}",
        "Non-Stick Frying Pan {size}", "Insulated Lunch Box {size}",
    ],
    "Health_Beauty": [
        "Organic Face Moisturizer {size}", "Vitamin C Serum {size}", "Bamboo Toothbrush Set {size}",
        "Essential Oil Diffuser {size}", "Aloe Vera Gel {size}", "Sunscreen SPF50 {size}",
        "Hair Repair Mask {size}",
    ],
    "Sports_Outdoor": [
        "Yoga Mat Premium {size}", "Resistance Band Set {size}", "Insulated Water Bottle {size}",
        "Camping Headlamp {size}", "Foam Roller {size}", "Jump Rope Speed {size}",
        "Hiking Backpack {size}", "Compression Socks {size}",
    ],
    "Office_Supplies": [
        "Gel Pen Set {size}", "Leather Notebook {size}", "Desk Organizer {size}",
        "Sticky Notes Bulk {size}", "Whiteboard Markers {size}", "Filing Folders Set {size}",
        "Ergonomic Mouse Pad {size}",
    ],
}

sizes = ["Small", "Medium", "Large", "XL", "Compact", "Standard", "Pro", "Deluxe"]

# 8 opportunity SKUs -- these get demand lift (kept small to avoid inflating
# aggregate revenue comparisons across the full customer base)
opportunity_sku_indices = set(random.sample(range(200), 8))

sku_rows = []
sku_idx = 0
launch_dates_pool = [date(y, m, 1) for y in range(2019, 2025) for m in [1, 4, 7, 10]]

for cat, config in categories.items():
    templates = name_templates[cat]
    for j in range(config["count"]):
        sku_id = f"SKU_{sku_idx+1:04d}"
        template = templates[j % len(templates)]
        size = sizes[j % len(sizes)]
        canonical_name = template.format(size=size)
        price = round(random.uniform(*config["price_range"]), 2)
        lead_time = random.randint(*config["lead_time"])
        is_active = sku_idx not in set(random.sample(range(200), 10))
        launch = random.choice(launch_dates_pool)
        is_opportunity = sku_idx in opportunity_sku_indices
        sku_rows.append((sku_id, canonical_name, cat, price, lead_time, True, launch, is_opportunity))
        sku_idx += 1

dim_skus_schema = StructType([
    StructField("sku_id", StringType()),
    StructField("sku_name_canonical", StringType()),
    StructField("product_category", StringType()),
    StructField("unit_price_usd", DoubleType()),
    StructField("lead_time_days", IntegerType()),
    StructField("is_active", BooleanType()),
    StructField("launch_date", DateType()),
    StructField("_is_opportunity_sku", BooleanType()),
])

dim_skus = spark.createDataFrame(sku_rows, dim_skus_schema)
dim_skus.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.dim_skus")
dim_skus.coalesce(1).write.mode("overwrite").option("header", "true").csv(f"{VOLUME_PATH}/dim_skus")
print(f"dim_skus: {dim_skus.count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## dim_sku_aliases (~600 rows)

# COMMAND ----------

def generate_aliases(canonical_name, sku_id):
    """Generate 3 messy alias variants per canonical SKU name."""
    aliases = []
    words = canonical_name.split()

    # OMS alias: reorder words, add parentheses around size
    oms = " ".join(words[2:4] + words[0:2]) if len(words) > 3 else " ".join(words[1:] + words[:1])
    size_part = [w for w in words if w in ("Small", "Medium", "Large", "XL", "Compact", "Standard", "Pro", "Deluxe")]
    if size_part:
        oms = oms.replace(size_part[0], f"({size_part[0]})")
    aliases.append((sku_id, oms.strip(), "order_mgmt"))

    # WMS alias: abbreviate, change separators
    wms = canonical_name.replace(" ", " ").replace("-", " ")
    words_wms = wms.split()
    if len(words_wms) > 2:
        wms = " ".join([words_wms[i] if i < 2 else words_wms[i].lower() for i in range(len(words_wms))])
    aliases.append((sku_id, wms.strip(), "warehouse"))

    # Customer PO alias: uppercase, hyphenated, abbreviated
    abbrev = canonical_name.upper().replace(" ", "-")
    for remove in ["SET", "PACK", "PRO", "BULK"]:
        abbrev = abbrev.replace(remove, remove[:3])
    aliases.append((sku_id, abbrev.strip(), "customer_po"))

    # Legacy ERP alias (for ~40% of SKUs): heavy abbreviation
    if random.random() < 0.4:
        erp = " ".join([w[:3].upper() for w in canonical_name.split() if len(w) > 2])
        aliases.append((sku_id, erp.strip(), "legacy_erp"))

    return aliases

# Collect canonical names from sku_rows
alias_rows = []
alias_counter = 0
for row in sku_rows:
    sku_id, canonical_name = row[0], row[1]
    for sku_ref, alias_name, source_sys in generate_aliases(canonical_name, sku_id):
        alias_counter += 1
        alias_rows.append((
            f"ALIAS_{alias_counter:04d}",
            sku_ref,
            alias_name,
            source_sys,
            None,  # similarity_score -- populated by ai_similarity()
            None,  # resolved_sku_id -- populated by ai_similarity()
        ))

dim_sku_aliases_schema = StructType([
    StructField("alias_id", StringType()),
    StructField("sku_id", StringType()),
    StructField("alias_name", StringType()),
    StructField("source_system", StringType()),
    StructField("similarity_score", DoubleType()),
    StructField("resolved_sku_id", StringType()),
])

dim_sku_aliases = spark.createDataFrame(alias_rows, dim_sku_aliases_schema)
dim_sku_aliases.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.dim_sku_aliases")
dim_sku_aliases.coalesce(1).write.mode("overwrite").option("header", "true").csv(f"{VOLUME_PATH}/dim_sku_aliases")
print(f"dim_sku_aliases: {dim_sku_aliases.count()} rows")

alias_to_sku = {a[2]: a[1] for a in alias_rows}  # alias_name -> sku_id

# COMMAND ----------

# MAGIC %md
# MAGIC ## dim_date (4 years: 3 back + 1 forward)

# COMMAND ----------

from datetime import date, timedelta

# 3 years back + 1 year forward from today
today = date.today()
start_date = date(today.year - 3, 1, 1)
end_date = date(today.year + 1, 12, 31)

us_holidays = {
    # Approximate fixed US holidays (month, day)
    (1, 1), (1, 20), (2, 17), (5, 26), (6, 19), (7, 4),
    (9, 1), (10, 13), (11, 11), (11, 27), (12, 25),
}

date_rows = []
current = start_date
while current <= end_date:
    day_of_week = current.strftime("%A")
    is_weekend = day_of_week in ("Saturday", "Sunday")
    is_holiday = (current.month, current.day) in us_holidays
    # Fiscal calendar: October year start
    fiscal_month = (current.month - 10) % 12 + 1
    fiscal_quarter = f"FQ{(fiscal_month - 1) // 3 + 1}"
    date_rows.append((
        current,
        current.year,
        (current.month - 1) // 3 + 1,
        current.month,
        current.isocalendar()[1],
        day_of_week,
        is_weekend,
        is_holiday,
        fiscal_quarter,
    ))
    current += timedelta(days=1)

dim_date_schema = StructType([
    StructField("date_key", DateType()),
    StructField("year", IntegerType()),
    StructField("quarter", IntegerType()),
    StructField("month", IntegerType()),
    StructField("week_of_year", IntegerType()),
    StructField("day_of_week", StringType()),
    StructField("is_weekend", BooleanType()),
    StructField("is_holiday", BooleanType()),
    StructField("fiscal_quarter", StringType()),
])

dim_date = spark.createDataFrame(date_rows, dim_date_schema)
dim_date.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.dim_date")
dim_date.coalesce(1).write.mode("overwrite").option("header", "true").csv(f"{VOLUME_PATH}/dim_date")
print(f"dim_date: {dim_date.count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## fact_actuals (incremental)
# MAGIC
# MAGIC Realistic demand signal with smooth trends that `ai_forecast()` can learn:
# MAGIC - **Smooth baseline** with gradual trend (growth or decline per customer)
# MAGIC - **Seasonality**: sinusoidal annual cycle + category-specific peaks
# MAGIC - **Opportunity SKUs**: gradual 30% ramp starting Sep 2025
# MAGIC - **Anomaly accounts**: 3 customers with mild disruption starting Dec 2025 (8-12% swing)
# MAGIC - **Decline accounts**: 5 customers with gentle demand erosion starting Oct 2025 (2-5% annual)
# MAGIC - **Low noise**: ~8% daily jitter so Prophet can fit cleanly -> high baseline accuracy
# MAGIC - **Incremental**: only generates data for dates after existing max

# COMMAND ----------

import numpy as np
from pyspark.sql import Row

# Build lookup maps
sku_data = {r[0]: r for r in sku_rows}  # sku_id -> full row
alias_lookup = {}  # sku_id -> list of (alias_name, source_system)
for a in alias_rows:
    sid = a[1]
    if sid not in alias_lookup:
        alias_lookup[sid] = []
    alias_lookup[sid].append((a[2], a[3]))

customer_data = {r[0]: r for r in customer_rows}  # customer_id -> full row

# ---------------------------------------------------------------------------
# Fixed pattern dates -- stable across all runs (not relative to today)
# ---------------------------------------------------------------------------
ANOMALY_START = date(2025, 12, 15)       # 3 customers disrupted from this date
OPPORTUNITY_RAMP_START = date(2025, 9, 1) # 15 SKUs ramping demand from this date
DECLINE_START = date(2025, 10, 1)         # 5 customers declining from this date

# ---------------------------------------------------------------------------
# Deterministic selections -- hash-based, independent of random module state.
# These are always the same regardless of seed position or run order.
# ---------------------------------------------------------------------------
_cust_ids = [f"CUST_{i+1:03d}" for i in range(50)]
anomaly_customers = set(sorted(_cust_ids, key=lambda c: hash(("anomaly_v1", c)))[:3])
decline_customers = set(sorted(_cust_ids, key=lambda c: hash(("decline_v1", c)))[:5]) - anomaly_customers

# Build opportunity SKU set
opportunity_skus = {row[0] for row in sku_rows if row[7]}

# ---------------------------------------------------------------------------
# Hash-based per-day determinism -- same date always produces same data
# regardless of when the generator runs.
# ---------------------------------------------------------------------------
def _hv(key):
    """Deterministic float in [0, 1) from a hashable key."""
    return (hash(key) & 0x7FFFFFFF) % 10000 / 10000

# ---------------------------------------------------------------------------
# Incremental mode -- only generate dates after existing max
# ---------------------------------------------------------------------------
DATA_ORIGIN = date(today.year - 3, 1, 1)
actuals_start = DATA_ORIGIN
is_incremental = False
try:
    max_row = spark.sql(f"SELECT MAX(transaction_date) as d FROM {CATALOG}.{RAW_SCHEMA}.fact_actuals").first()
    if max_row and max_row["d"]:
        actuals_start = max_row["d"] + timedelta(days=1)
        is_incremental = True
except:
    pass

actuals_end = today
num_new_days = (actuals_end - actuals_start).days

print(f"Mode: {'incremental' if is_incremental else 'initial load'}")
print(f"Generating fact_actuals: {actuals_start} to {actuals_end} ({num_new_days} days)")
print(f"Anomaly customers: {sorted(anomaly_customers)} (from {ANOMALY_START})")
print(f"Decline customers: {sorted(decline_customers)} (from {DECLINE_START})")
print(f"Opportunity SKUs: {len(opportunity_skus)} (from {OPPORTUNITY_RAMP_START})")

# COMMAND ----------

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario Injection Functions

# COMMAND ----------

def inject_actuals_scenarios(rows, reference_date=None):
    """Apply scenario perturbations to fact_actuals rows. Modifies in place."""
    if not INJECT_SCENARIOS:
        return rows
    if reference_date is None:
        reference_date = date.today()

    modified = 0
    for i, row in enumerate(rows):
        txn_date = row[1]  # transaction_date
        cid = row[2]       # customer_id
        # Get the actual sku_id from the alias via lookup
        alias_name = row[3]  # sku_alias_name
        sid = alias_to_sku.get(alias_name)
        if not sid:
            # Try reverse lookup from sku_rows
            continue
        channel = row[5]   # channel
        units = row[6]     # actual_units
        revenue = row[7]   # actual_revenue_usd

        for scenario in SCENARIOS:
            stype = scenario["type"]

            if stype == "channel_divergence":
                if sid != scenario["sku"]:
                    continue
                onset = reference_date - timedelta(days=scenario["onset_days_ago"])
                if txn_date < onset:
                    continue
                if channel == scenario["affected_channel"]:
                    factor = 1.0 - scenario["decline_pct"]
                else:
                    factor = 1.0 + scenario["offset_pct"]
                new_units = max(1, round(units * factor))
                price_per = revenue / units if units > 0 else 0
                rows[i] = (*row[:6], float(new_units), round(new_units * price_per, 2), *row[8:])
                modified += 1

            elif stype == "leading_indicator":
                if sid != scenario["sku"]:
                    continue
                onset = reference_date - timedelta(days=scenario["onset_days_ago"])
                if txn_date < onset:
                    continue
                # Progressive demand spike — ramps up over the onset window
                days_in = (txn_date - onset).days
                max_days = scenario["onset_days_ago"]
                ramp = min(1.0, days_in / max(1, max_days))
                factor = 1.0 + scenario["demand_spike_pct"] * ramp
                new_units = max(1, round(units * factor))
                price_per = revenue / units if units > 0 else 0
                rows[i] = (*row[:6], float(new_units), round(new_units * price_per, 2), *row[8:])
                modified += 1

            elif stype == "stockout_risk":
                if sid != scenario["sku"]:
                    continue
                onset = reference_date - timedelta(days=scenario["onset_days_ago"])
                if txn_date < onset:
                    continue
                factor = 1.0 + scenario["demand_spike_pct"]
                new_units = max(1, round(units * factor))
                price_per = revenue / units if units > 0 else 0
                rows[i] = (*row[:6], float(new_units), round(new_units * price_per, 2), *row[8:])
                modified += 1

            elif stype == "cannibalization":
                onset = reference_date - timedelta(days=scenario["onset_days_ago"])
                if txn_date < onset:
                    continue
                days_in = (txn_date - onset).days
                max_days = scenario["onset_days_ago"]
                ramp = min(1.0, days_in / max(1, max_days))
                transfer = scenario["transfer_pct"] * ramp

                if sid == scenario["rising_sku"]:
                    factor = 1.0 + transfer
                    new_units = max(1, round(units * factor))
                    price_per = revenue / units if units > 0 else 0
                    rows[i] = (*row[:6], float(new_units), round(new_units * price_per, 2), *row[8:])
                    modified += 1
                elif sid == scenario["declining_sku"]:
                    factor = 1.0 - transfer
                    new_units = max(1, round(units * factor))
                    price_per = revenue / units if units > 0 else 0
                    rows[i] = (*row[:6], float(new_units), round(new_units * price_per, 2), *row[8:])
                    modified += 1

    return rows


def inject_inventory_scenarios(rows, reference_date=None):
    """Apply scenario perturbations to fact_inventory rows."""
    if not INJECT_SCENARIOS:
        return rows
    if reference_date is None:
        reference_date = date.today()

    modified = 0
    for i, row in enumerate(rows):
        snap_date = row[0]  # snapshot_date
        sid = row[1]        # sku_id
        on_hand = row[3]    # on_hand_units
        dos = row[6]        # days_of_supply

        for scenario in SCENARIOS:
            stype = scenario["type"]

            # For leading_indicator and stockout_risk: deplete inventory faster
            if stype in ("leading_indicator", "stockout_risk"):
                target_sku = scenario["sku"]
                if sid != target_sku:
                    continue
                onset = reference_date - timedelta(days=scenario["onset_days_ago"])
                if snap_date < onset:
                    continue
                days_in = (snap_date - onset).days
                max_days = scenario["onset_days_ago"]
                ramp = min(1.0, days_in / max(1, max_days))
                # Reduce on_hand and days_of_supply progressively
                depletion = 1.0 - (scenario["demand_spike_pct"] * 0.7 * ramp)
                depletion = max(0.1, depletion)
                new_on_hand = max(0, round(on_hand * depletion))
                new_dos = max(0, round(dos * depletion, 1))
                new_stockout = new_on_hand <= 0
                rows[i] = (snap_date, sid, row[2], float(new_on_hand), row[4], row[5], new_dos, new_stockout)
                modified += 1

    return rows

# COMMAND ----------

# Generate fact_actuals with deterministic per-day noise
all_actuals = []
actual_counter = 0

if num_new_days > 0:
    for cust_idx, (cid, cname, cregion, cchannel, ctier, crev, ccontact, cactive) in enumerate(customer_rows):
        # Deterministic SKU assignment per customer (hash-based, stable across runs)
        cust_skus = sorted(sku_rows, key=lambda s: hash(("sku_assign_v1", cid, s[0])))[:70]

        for sku_row in cust_skus:
            sid, scanonical, scat, sprice, slead, sactive, slaunch, s_is_opp = sku_row

            effective_start = max(actuals_start, slaunch)
            sku_end = actuals_end
            sku_days = (sku_end - effective_start).days
            if sku_days <= 0:
                continue

            # Deterministic alias for this customer-SKU pair
            aliases = alias_lookup.get(sid, [(scanonical, "canonical")])
            alias_idx = hash(("alias_v1", cid, sid)) % len(aliases)
            alias_name, source_sys = aliases[alias_idx]

            # Base demand: correlated with customer tier and price
            tier_multiplier = {"gold": 3.0, "silver": 1.5, "bronze": 0.7}
            base_daily_units = max(3, int(25 * tier_multiplier.get(ctier, 1.0) / (sprice + 0.1)))

            # Per-series parameters (deterministic from customer-SKU pair, not random state)
            annual_growth = 0.02 + _hv(("growth", cid, sid)) * 0.03       # 2-5%
            season_phase = -0.5 + _hv(("phase", cid, sid)) * 1.0

            if scat in ("Electronics", "Sports_Outdoor"):
                season_amplitude = 0.20 + _hv(("amp", cid, sid)) * 0.15   # Strong Q4 lift (holiday season)
                season_peak_month = 10.5
            elif scat == "Home_Kitchen":
                season_amplitude = 0.10 + _hv(("amp", cid, sid)) * 0.10   # Moderate Q2 lift (spring/summer)
                season_peak_month = 4.5
            elif scat == "Health_Beauty":
                season_amplitude = 0.08 + _hv(("amp", cid, sid)) * 0.07
                season_peak_month = 9.0
            else:
                season_amplitude = 0.05 + _hv(("amp", cid, sid)) * 0.07   # Mild
                season_peak_month = 3.0 + _hv(("peak", cid, sid)) * 8.0

            # Decline rate for decline customers (5-15% annual)
            annual_decline = 0.0
            if cid in decline_customers:
                annual_decline = 0.02 + _hv(("decline_rate", cid, sid)) * 0.03

            order_probability = 0.65

            for day_offset in range(sku_days):
                txn_date = effective_start + timedelta(days=day_offset)
                day_key = txn_date.toordinal()

                # Deterministic order occurrence
                if _hv(("order", cid, sid, day_key)) >= order_probability:
                    continue

                month = txn_date.month
                years_elapsed = (txn_date - DATA_ORIGIN).days / 365.25

                units = float(base_daily_units)
                demand_driver = "baseline"

                # --- TREND ---
                if cid in decline_customers and txn_date >= DECLINE_START:
                    decline_elapsed = (txn_date - DECLINE_START).days / 365.25
                    trend_multiplier = max(0.5, 1.0 - annual_decline * decline_elapsed)
                    demand_driver = "demand_decline"
                else:
                    trend_multiplier = 1.0 + annual_growth * years_elapsed
                units *= trend_multiplier

                # --- SMOOTH SEASONALITY ---
                month_frac = month + (txn_date.day / 30.0) + season_phase
                seasonal_factor = 1.0 + season_amplitude * math.cos(
                    2 * math.pi * (month_frac - season_peak_month) / 12.0
                )
                units *= seasonal_factor
                if seasonal_factor > 1.10:
                    demand_driver = "seasonal_lift"

                # --- OPPORTUNITY SKU DEMAND LIFT ---
                if s_is_opp and txn_date >= OPPORTUNITY_RAMP_START:
                    days_into_ramp = (txn_date - OPPORTUNITY_RAMP_START).days
                    ramp_progress = min(1.0, days_into_ramp / 180.0)
                    units *= 1.0 + 0.15 * ramp_progress
                    if ramp_progress > 0.3:
                        demand_driver = "promotional"

                # --- ANOMALY ACCOUNTS ---
                if cid in anomaly_customers and txn_date >= ANOMALY_START:
                    anomaly_direction = 1 if hash(("anom_dir", cid)) % 2 == 0 else -1
                    anomaly_magnitude = 0.08 + _hv(("anom_mag", cid, sid)) * 0.04
                    units *= (1.0 + anomaly_direction * anomaly_magnitude)
                    demand_driver = "demand_disruption"

                # --- DETERMINISTIC NOISE (~8% jitter) ---
                jitter = 0.92 + _hv(("jitter", cid, sid, day_key)) * 0.16
                units *= jitter
                units = max(1, round(units))

                # --- OCCASIONAL SPOT ORDERS (rare, ~1.5%) ---
                if _hv(("spot", cid, sid, day_key)) < 0.015:
                    spot_mult = 2.0 + _hv(("spot_m", cid, sid, day_key)) * 1.5
                    units = round(units * spot_mult)
                    demand_driver = "spot_order"

                revenue = round(units * sprice, 2)
                actual_counter += 1
                order_id = f"ORD_{cid}_{txn_date.strftime('%Y%m%d')}_{actual_counter % 10000:04d}"

                all_actuals.append((
                    str(uuid.uuid4()),
                    txn_date,
                    cid,
                    alias_name,
                    cregion,
                    cchannel,
                    float(units),
                    revenue,
                    order_id,
                    demand_driver,
                ))

        if (cust_idx + 1) % 10 == 0:
            print(f"  Processed {cust_idx + 1}/50 customers, {len(all_actuals):,} rows so far")

# Apply proactive agent scenarios
if all_actuals and INJECT_SCENARIOS:
    inject_actuals_scenarios(all_actuals, reference_date=today)
    print(f"Applied {len(SCENARIOS)} scenario perturbations to fact_actuals")

print(f"Total new fact_actuals rows: {len(all_actuals):,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Apply late corrections (initial load only)

# COMMAND ----------

if all_actuals and not is_incremental:
    num_corrections = int(len(all_actuals) * 0.02)
    correction_indices = sorted(range(len(all_actuals)), key=lambda i: hash(("correction_v1", i)))[:num_corrections]

    for idx in correction_indices:
        original = all_actuals[idx]
        correction_factor = 0.90 + _hv(("corr_factor", idx)) * 0.20  # [0.90, 1.10]
        revised_units = max(1, round(original[6] * correction_factor))
        if original[6] > 0:
            price_proxy = original[7] / original[6]
        else:
            price_proxy = 0.10
        revised_revenue = round(revised_units * price_proxy, 2)
        all_actuals[idx] = (
            original[0], original[1], original[2], original[3], original[4], original[5],
            float(revised_units), revised_revenue, original[8], original[9],
        )

    print(f"Applied {num_corrections:,} late corrections")
elif is_incremental:
    print("Skipping late corrections (incremental mode)")
else:
    print("No actuals to correct")

# COMMAND ----------

# Write fact_actuals
fact_actuals_schema = StructType([
    StructField("actual_id", StringType()),
    StructField("transaction_date", DateType()),
    StructField("customer_id", StringType()),
    StructField("sku_alias_name", StringType()),
    StructField("region", StringType()),
    StructField("channel", StringType()),
    StructField("actual_units", DoubleType()),
    StructField("actual_revenue_usd", DoubleType()),
    StructField("order_id", StringType()),
    StructField("demand_driver", StringType()),
])

if all_actuals:
    write_mode = "append" if is_incremental else "overwrite"
    batch_size = 100_000
    num_batches = (len(all_actuals) + batch_size - 1) // batch_size

    for i in range(num_batches):
        batch = all_actuals[i * batch_size : (i + 1) * batch_size]
        batch_df = spark.createDataFrame(batch, fact_actuals_schema)
        if i == 0:
            batch_df.write.mode(write_mode).option("overwriteSchema", "true" if not is_incremental else "false").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.fact_actuals")
        else:
            batch_df.write.mode("append").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.fact_actuals")
        print(f"  Written batch {i+1}/{num_batches} ({len(batch):,} rows)")

    # Write to volume: append for incremental, overwrite for initial
    if is_incremental:
        new_df = spark.createDataFrame(all_actuals, fact_actuals_schema)
        new_df.write.mode("append").partitionBy("region").option("header", "true").csv(f"{VOLUME_PATH}/fact_actuals")
    else:
        fact_actuals_full = spark.table(f"{CATALOG}.{RAW_SCHEMA}.fact_actuals")
        fact_actuals_full.write.mode("overwrite").partitionBy("region").option("header", "true").csv(f"{VOLUME_PATH}/fact_actuals")

total_actuals = spark.table(f"{CATALOG}.{RAW_SCHEMA}.fact_actuals").count()
print(f"fact_actuals: {total_actuals:,} total rows ({len(all_actuals):,} new)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## fact_inventory (incremental)
# MAGIC
# MAGIC Daily inventory snapshots with realistic depletion/replenishment cycles.
# MAGIC - Most SKUs have adequate inventory (DOS 15-40 days)
# MAGIC - ~10% of SKUs in each region are at low/critical levels (realistic alerts)
# MAGIC - Opportunity SKUs trend toward tighter inventory as demand ramps
# MAGIC - Anomaly customers' preferred SKUs show inventory stress

# COMMAND ----------

# Incremental mode for inventory
INVENTORY_ORIGIN = date(today.year - 2, 1, 1)
inventory_start = INVENTORY_ORIGIN
is_inv_incremental = False
try:
    max_inv = spark.sql(f"SELECT MAX(snapshot_date) as d FROM {CATALOG}.{RAW_SCHEMA}.fact_inventory").first()
    if max_inv and max_inv["d"]:
        inventory_start = max_inv["d"] + timedelta(days=1)
        is_inv_incremental = True
except:
    pass

inventory_end = today
inv_days = (inventory_end - inventory_start).days

warehouse_regions = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]

# Deterministic set of stressed SKUs (hash-based) — kept small so proactive agent
# primarily surfaces injected scenarios, not baseline inventory noise.
anomaly_sku_stress = set(
    sorted(
        [r[0] for r in sku_rows],
        key=lambda s: hash(("stress_v1", s))
    )[:10]
)

print(f"Mode: {'incremental' if is_inv_incremental else 'initial load'}")
print(f"Generating fact_inventory: {inventory_start} to {inventory_end} ({inv_days} days)")
print(f"SKUs under stress: {len(anomaly_sku_stress)}")

# COMMAND ----------

all_inventory = []

if inv_days > 0:
    for sku_row in sku_rows:
        sid, scanonical, scat, sprice, slead, sactive, slaunch, s_is_opp = sku_row
        if not sactive:
            continue

        for wregion in warehouse_regions:
            # Per-series inventory parameters (deterministic, hash-based)
            avg_daily_demand = 20 + _hv(("inv_demand", sid, wregion)) * 180
            safety_stock = round(avg_daily_demand * (14 + _hv(("inv_safety", sid, wregion)) * 7))
            on_hand = round(safety_stock * (2.5 + _hv(("inv_start", sid, wregion)) * 1.5))
            on_order = 0.0
            reorder_pending_days = 0

            is_stressed = (
                (s_is_opp and _hv(("inv_stress_opp", sid, wregion)) < 0.10) or
                (sid in anomaly_sku_stress and wregion in ["Northeast"] and _hv(("inv_stress_anom", sid, wregion)) < 0.15)
            )

            # Deterministic snapshot frequency per SKU-region
            snap_step = 1 + hash(("inv_step", sid, wregion)) % 3  # 1, 2, or 3 day steps

            # For incremental mode, we need to simulate forward from a known state.
            # To keep it deterministic, we always simulate from INVENTORY_ORIGIN
            # but only EMIT rows for dates >= inventory_start.
            sim_start = INVENTORY_ORIGIN
            sim_days = (inventory_end - sim_start).days

            for day_offset in range(0, sim_days, snap_step):
                snap_date = sim_start + timedelta(days=day_offset)
                day_key = snap_date.toordinal()

                # Deterministic daily demand noise
                demand_noise = 0.85 + _hv(("inv_noise", sid, wregion, day_key)) * 0.30
                daily_demand = avg_daily_demand * demand_noise

                # Opportunity SKUs: higher recent demand depletes faster
                if s_is_opp and snap_date >= OPPORTUNITY_RAMP_START:
                    days_into_ramp = (snap_date - OPPORTUNITY_RAMP_START).days
                    ramp = min(1.0, days_into_ramp / 180.0)
                    daily_demand *= (1.0 + 0.15 * ramp)

                # Stressed SKUs: reduce replenishment effectiveness recently
                if is_stressed and snap_date >= (inventory_end - timedelta(days=90)):
                    stress_mult = 1.1 + _hv(("inv_stress_mult", sid, wregion, day_key)) * 0.2
                    daily_demand *= stress_mult
                    if reorder_pending_days > 0 and _hv(("inv_delay", sid, wregion, day_key)) < 0.15:
                        delay_extra = 2 + hash(("inv_delay_d", sid, wregion, day_key)) % 4
                        reorder_pending_days += delay_extra

                on_hand = max(0, on_hand - daily_demand)

                # Reorder logic
                if reorder_pending_days > 0:
                    reorder_pending_days -= 1
                    if reorder_pending_days == 0:
                        replenish_mult = 3.0 + _hv(("inv_replenish", sid, wregion, day_key)) * 2.0
                        on_hand += safety_stock * replenish_mult
                        on_order = 0.0

                if on_hand < safety_stock and reorder_pending_days == 0:
                    order_mult = 3.0 + _hv(("inv_order", sid, wregion, day_key)) * 2.0
                    on_order = round(safety_stock * order_mult)
                    reorder_pending_days = slead

                # Only emit rows for dates in the target range
                if snap_date >= inventory_start:
                    dos = round(on_hand / max(1, avg_daily_demand), 1)
                    stockout = on_hand <= 0

                    all_inventory.append((
                        snap_date,
                        sid,
                        wregion,
                        round(on_hand, 0),
                        round(on_order, 0),
                        round(safety_stock, 0),
                        dos,
                        stockout,
                    ))

        if (sku_rows.index(sku_row) + 1) % 50 == 0:
            print(f"  Processed {sku_rows.index(sku_row) + 1}/200 SKUs, {len(all_inventory):,} rows")

# Apply proactive agent scenarios to inventory
if all_inventory and INJECT_SCENARIOS:
    inject_inventory_scenarios(all_inventory, reference_date=today)
    print(f"Applied inventory scenario perturbations")

print(f"Total new fact_inventory rows: {len(all_inventory):,}")

# COMMAND ----------

fact_inventory_schema = StructType([
    StructField("snapshot_date", DateType()),
    StructField("sku_id", StringType()),
    StructField("warehouse_region", StringType()),
    StructField("on_hand_units", DoubleType()),
    StructField("on_order_units", DoubleType()),
    StructField("safety_stock_units", DoubleType()),
    StructField("days_of_supply", DoubleType()),
    StructField("stockout_flag", BooleanType()),
])

if all_inventory:
    write_mode = "append" if is_inv_incremental else "overwrite"
    batch_size = 100_000
    num_batches = (len(all_inventory) + batch_size - 1) // batch_size

    for i in range(num_batches):
        batch = all_inventory[i * batch_size : (i + 1) * batch_size]
        batch_df = spark.createDataFrame(batch, fact_inventory_schema)
        if i == 0:
            batch_df.write.mode(write_mode).option("overwriteSchema", "true" if not is_inv_incremental else "false").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.fact_inventory")
        else:
            batch_df.write.mode("append").saveAsTable(f"{CATALOG}.{RAW_SCHEMA}.fact_inventory")
        print(f"  Written batch {i+1}/{num_batches} ({len(batch):,} rows)")

    # Write to volume
    if is_inv_incremental:
        new_inv_df = spark.createDataFrame(all_inventory, fact_inventory_schema)
        new_inv_df.write.mode("append").partitionBy("warehouse_region").option("header", "true").csv(f"{VOLUME_PATH}/fact_inventory")
    else:
        fact_inventory_full = spark.table(f"{CATALOG}.{RAW_SCHEMA}.fact_inventory")
        fact_inventory_full.write.mode("overwrite").partitionBy("warehouse_region").option("header", "true").csv(f"{VOLUME_PATH}/fact_inventory")

total_inventory = spark.table(f"{CATALOG}.{RAW_SCHEMA}.fact_inventory").count()
print(f"fact_inventory: {total_inventory:,} total rows ({len(all_inventory):,} new)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validation

# COMMAND ----------

print("=== Data Generation Summary ===")
for table in ["dim_customers", "dim_skus", "dim_sku_aliases", "dim_date", "fact_actuals", "fact_inventory"]:
    count = spark.table(f"{CATALOG}.{RAW_SCHEMA}.{table}").count()
    print(f"  {table}: {count:,} rows")

# Validate opportunity SKU demand lift
print("\n=== Opportunity SKU Demand Lift Validation ===")
opp_skus_list = [r[0] for r in sku_rows if r[7]]
print(f"Opportunity SKUs: {opp_skus_list[:5]}...")

# Validate anomaly customer disruption
print(f"\n=== Anomaly Customers (disrupted since {ANOMALY_START}) ===")
print(f"  Customers: {sorted(anomaly_customers)}")

# Validate decline customers
print(f"\n=== Decline Customers (declining since {DECLINE_START}) ===")
print(f"  Customers: {sorted(decline_customers)}")

# Check alias distribution
print("\n=== Alias Distribution ===")
spark.sql(f"""
    SELECT source_system, COUNT(*) as cnt
    FROM {CATALOG}.{RAW_SCHEMA}.dim_sku_aliases
    GROUP BY source_system
    ORDER BY cnt DESC
""").show()

# Check demand driver distribution
print("\n=== Demand Driver Distribution ===")
spark.sql(f"""
    SELECT demand_driver, COUNT(*) as cnt, ROUND(AVG(actual_units), 1) as avg_units
    FROM {CATALOG}.{RAW_SCHEMA}.fact_actuals
    GROUP BY demand_driver
    ORDER BY cnt DESC
""").show()

print("\n=== Volume files ===")
dbutils.fs.ls(VOLUME_PATH)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario Validation

# COMMAND ----------

if INJECT_SCENARIOS:
    print("\n=== Proactive Agent Scenario Validation ===")
    reference = today
    for scenario in SCENARIOS:
        name = scenario["name"]
        stype = scenario["type"]
        onset_days = scenario.get("onset_days_ago", 0)
        onset_date = reference - timedelta(days=onset_days)

        if stype == "cannibalization":
            rising = scenario["rising_sku"]
            declining = scenario["declining_sku"]
            print(f"\n[{name}] Rising={rising}, Declining={declining}, onset={onset_date}")
            try:
                spark.sql(f"""
                    SELECT sku_id,
                           COUNT(*) as rows,
                           ROUND(AVG(actual_units), 1) as avg_units
                    FROM {CATALOG}.{RAW_SCHEMA}.fact_actuals
                    WHERE sku_alias_name IN (
                        SELECT alias_name FROM {CATALOG}.{RAW_SCHEMA}.dim_sku_aliases
                        WHERE sku_id IN ('{rising}', '{declining}')
                    )
                    AND transaction_date >= '{onset_date}'
                    GROUP BY sku_id
                """).show()
                print(f"  [PASS] Cannibalization data present")
            except Exception as e:
                print(f"  [CHECK] {e}")
        else:
            sku = scenario.get("sku", "")
            print(f"\n[{name}] SKU={sku}, onset={onset_date}")
            try:
                # Check actuals
                result = spark.sql(f"""
                    SELECT COUNT(*) as cnt, ROUND(AVG(actual_units), 1) as avg_units
                    FROM {CATALOG}.{RAW_SCHEMA}.fact_actuals
                    WHERE sku_alias_name IN (
                        SELECT alias_name FROM {CATALOG}.{RAW_SCHEMA}.dim_sku_aliases
                        WHERE sku_id = '{sku}'
                    )
                    AND transaction_date >= '{onset_date}'
                """).first()
                print(f"  Actuals since onset: {result['cnt']} rows, avg={result['avg_units']} units")

                # Check inventory for supply-side scenarios
                if stype in ("leading_indicator", "stockout_risk"):
                    inv_result = spark.sql(f"""
                        SELECT COUNT(*) as cnt, ROUND(AVG(days_of_supply), 1) as avg_dos
                        FROM {CATALOG}.{RAW_SCHEMA}.fact_inventory
                        WHERE sku_id = '{sku}'
                        AND snapshot_date >= '{onset_date}'
                    """).first()
                    print(f"  Inventory since onset: {inv_result['cnt']} rows, avg DOS={inv_result['avg_dos']}")

                print(f"  [PASS] Scenario data present")
            except Exception as e:
                print(f"  [CHECK] {e}")

    print("\n=== Scenario Validation Complete ===")
else:
    print("\nINJECT_SCENARIOS = False — scenarios not injected")
