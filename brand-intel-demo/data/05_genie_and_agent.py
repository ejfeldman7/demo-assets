# Databricks notebook source
# MAGIC %md
# MAGIC # Brand Intelligence Demo - Genie Spaces & Supervisor Agent
# MAGIC
# MAGIC Creates two Genie Spaces and a Supervisor Agent that routes between them:
# MAGIC
# MAGIC 1. **Demand Forecast Intelligence** - forecast accuracy, revenue opportunities, seasonal patterns
# MAGIC 2. **Inventory & Supply Chain** - stockout risk, days of supply, reorder urgency
# MAGIC 3. **Brand Manager Agent** (Supervisor) - unified interface routing to both Genies
# MAGIC
# MAGIC **Note:** This notebook uses the Databricks REST API via `requests` because
# MAGIC Genie Space and Supervisor Agent management APIs are not yet in the Python SDK.

# COMMAND ----------

import requests
import json
import time

CATALOG = "brand_intel_demo"
WAREHOUSE_ID = "<YOUR_WAREHOUSE_ID>"  # set to your SQL Warehouse ID before running

# Get workspace URL and token from notebook context
host = spark.conf.get("spark.databricks.workspaceUrl")
token = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
BASE_URL = f"https://{host}"
HEADERS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def api_post(path, payload):
    resp = requests.post(f"{BASE_URL}{path}", headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()

def api_get(path):
    resp = requests.get(f"{BASE_URL}{path}", headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Genie Space 1: Demand Forecast Intelligence

# COMMAND ----------

demand_space = api_post("/api/2.0/data-rooms/", {
    "display_name": "Brand Intel - Demand Forecast Intelligence",
    "warehouse_id": WAREHOUSE_ID,
    "description": (
        "AI-powered demand forecast analytics for brand managers. "
        "Ask questions about forecast accuracy, revenue opportunities, seasonal patterns, "
        "and customer-SKU performance. Powered by ai_forecast() predictions joined to actuals.\n\n"
        "Key tables:\n"
        "- demand_forecast_metrics: Forecast accuracy, revenue gaps, model confidence by customer/SKU/region\n"
        "- revenue_opportunity_metrics: Trailing 52-week under-forecast analysis\n"
        "- brand_manager_metrics: Brand manager portfolio performance\n"
        "- seasonal_demand_metrics: Monthly/quarterly seasonal patterns by product category"
    ),
    "table_identifiers": [
        f"{CATALOG}.gold.demand_forecast_metrics",
        f"{CATALOG}.gold.revenue_opportunity_metrics",
        f"{CATALOG}.gold.brand_manager_metrics",
        f"{CATALOG}.gold.seasonal_demand_metrics",
    ],
    "run_as_type": "VIEWER",
})

demand_space_id = demand_space["space_id"]
print(f"Demand Forecast Genie: {demand_space_id}")

# COMMAND ----------

# Add sample questions
demand_questions = [
    "What is the overall forecast accuracy across all customers?",
    "Which customers have the largest revenue opportunity gap in the trailing 52 weeks?",
    "Show me forecast accuracy by product category",
    "Which SKUs have been consistently under-forecasted for more than 10 weeks?",
    "What is the average model confidence by region?",
    "Show me the seasonal demand pattern for Electronics by month",
    "Which account tier has the highest total revenue opportunity?",
    "What is the confidence interval hit rate by product category?",
    "Compare forecast accuracy between retail, direct, and ecomm channels",
    "Show me the top 10 customer-SKU pairs by trailing 52-week revenue gap",
]

for q in demand_questions:
    api_post(f"/api/2.0/data-rooms/{demand_space_id}/curated-questions", {
        "question_text": q,
        "question_type": "SAMPLE_QUESTION",
    })

print(f"Added {len(demand_questions)} sample questions")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Genie Space 2: Inventory & Supply Chain

# COMMAND ----------

inventory_space = api_post("/api/2.0/data-rooms/", {
    "display_name": "Brand Intel - Inventory & Supply Chain",
    "warehouse_id": WAREHOUSE_ID,
    "description": (
        "Inventory risk analysis and supply chain intelligence for operations teams. "
        "Ask about stockout risks, days of supply, reorder urgency, and forecast-inventory alignment.\n\n"
        "Key tables:\n"
        "- inventory_risk_metrics: Stockout risk scores, days of supply, reorder urgency by SKU and warehouse\n"
        "- inventory_forecast_metrics: Current inventory vs 4-week AI-forecasted demand alignment"
    ),
    "table_identifiers": [
        f"{CATALOG}.gold.inventory_risk_metrics",
        f"{CATALOG}.gold.inventory_forecast_metrics",
    ],
    "run_as_type": "VIEWER",
})

inventory_space_id = inventory_space["space_id"]
print(f"Inventory Genie: {inventory_space_id}")

# COMMAND ----------

# Add sample questions
inventory_questions = [
    "What is the overall stockout risk score across all SKUs?",
    "Which SKUs have critical inventory levels right now?",
    "Show me average days of supply by warehouse region",
    "How many SKUs need immediate reorder?",
    "What is the forecast coverage ratio by product category?",
    "Which SKUs have a forecast coverage below 0.5?",
    "Show me total on-hand vs on-order units by warehouse region",
    "What is the recommended reorder quantity for critical risk SKUs?",
    "How many SKUs are currently in stockout?",
    "Compare inventory position across Northeast, Southeast, and West regions",
]

for q in inventory_questions:
    api_post(f"/api/2.0/data-rooms/{inventory_space_id}/curated-questions", {
        "question_text": q,
        "question_type": "SAMPLE_QUESTION",
    })

print(f"Added {len(inventory_questions)} sample questions")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Supervisor Agent: Brand Manager Intelligence

# COMMAND ----------

supervisor = api_post("/api/2.0/multi-agent-supervisors", {
    "name": "Brand_Manager_Intelligence_Agent",
    "description": (
        "Unified AI assistant for brand managers. "
        "Routes questions to specialized agents for demand forecasting, "
        "revenue opportunity analysis, and inventory/supply chain intelligence."
    ),
    "instructions": (
        "You are the Brand Manager Intelligence Agent. "
        "Route user queries to the appropriate specialist:\n\n"
        "1. **Demand & Revenue questions** -> demand_forecast_analyst\n"
        "   - Forecast accuracy, revenue gaps, model confidence\n"
        "   - Customer/SKU performance, seasonal patterns\n"
        "   - Under-forecasted products, revenue opportunities\n"
        "   - Brand manager portfolio metrics\n\n"
        "2. **Inventory & Supply Chain questions** -> inventory_supply_chain\n"
        "   - Stockout risk, days of supply, reorder urgency\n"
        "   - Warehouse inventory levels, on-hand/on-order units\n"
        "   - Forecast coverage ratio (inventory vs predicted demand)\n"
        "   - Safety stock, lead times, critical SKUs\n\n"
        "If a question spans both domains, gather information from both agents "
        "and synthesize the answer.\n\n"
        "Always provide specific numbers and actionable recommendations."
    ),
    "agents": [
        {
            "name": "demand_forecast_analyst",
            "description": (
                "Handles all demand forecasting, revenue opportunity, and brand manager "
                "performance questions. Use for any question about predictions, accuracy, "
                "trends, revenue opportunities, seasonal patterns, or brand manager workload."
            ),
            "agent_type": "genie",
            "genie_space": {"id": demand_space_id},
        },
        {
            "name": "inventory_supply_chain",
            "description": (
                "Handles all inventory management, supply chain, and stockout risk questions. "
                "Use for any question about stock levels, reorder needs, supply chain health, "
                "days of supply, warehouse regions, or inventory coverage."
            ),
            "agent_type": "genie",
            "genie_space": {"id": inventory_space_id},
        },
    ],
})

tile = supervisor["multi_agent_supervisor"]["tile"]
tile_id = tile["tile_id"]
print(f"Supervisor Agent: {tile_id}")
print(f"Name: {tile['name']}")

# COMMAND ----------

# Add routing examples
examples = [
    ("What is our overall forecast accuracy this quarter?", "demand_forecast_analyst"),
    ("Which customers have the biggest revenue opportunity gap?", "demand_forecast_analyst"),
    ("How many SKUs are at critical stockout risk?", "inventory_supply_chain"),
    ("What is the forecast coverage ratio for Electronics?", "inventory_supply_chain"),
    ("Show me seasonal demand patterns for Sports & Outdoor products", "demand_forecast_analyst"),
    ("Which warehouse regions need immediate reorders?", "inventory_supply_chain"),
]

for question, agent in examples:
    api_post(f"/api/2.0/multi-agent-supervisors/{tile_id}/examples", {
        "question": question,
        "guidelines": [f"Should be routed to {agent}"],
    })

print(f"Added {len(examples)} routing examples")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validation

# COMMAND ----------

# Wait for provisioning and check status
print("=== Brand Intelligence AI Agent Summary ===\n")

print(f"Demand Forecast Genie:  {demand_space_id}")
print(f"  URL: {BASE_URL}/genie/rooms/{demand_space_id}")
print(f"  Tables: 4 metric views\n")

print(f"Inventory Genie:        {inventory_space_id}")
print(f"  URL: {BASE_URL}/genie/rooms/{inventory_space_id}")
print(f"  Tables: 2 metric views\n")

mas_status = api_get(f"/api/2.0/multi-agent-supervisors/{tile_id}")
endpoint_status = mas_status["multi_agent_supervisor"].get("status", {}).get("endpoint_status", "UNKNOWN")
print(f"Supervisor Agent:       {tile_id}")
print(f"  Endpoint Status: {endpoint_status}")
print(f"  Agents: 2 (demand_forecast_analyst, inventory_supply_chain)")

if endpoint_status == "NOT_READY":
    print("\n  Note: Supervisor endpoint is still provisioning (2-5 min). Check back shortly.")
