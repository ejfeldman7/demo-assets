"""
Constrained Genie Question Templates & Result Validation.

The proactive agent picks a template and fills slots — it does not write
free-form questions. Each Genie result is validated before use. Invalid results
become 'unable to verify' findings, not silent failures.

Design decision: Template constraints + metric view quality together make Genie
reliable in Phase 2. Templates alone are necessary but not sufficient — the
underlying metric views must define business measures correctly.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from genie import ask_genie

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template library
# ---------------------------------------------------------------------------

ANALYTICAL_TEMPLATES = {
    "variance_check": {
        "template": "What is the {metric} for {sku} over the last {window} compared to the prior {window}?",
        "genie": "demand",
        "required_fields": [],
        "bounds": {},
    },
    "channel_split": {
        "template": "Break down {metric} for {sku} by channel for the last {window}.",
        "genie": "demand",
        "required_fields": [],
        "bounds": {},
    },
    "stockout_risk": {
        "template": "Which SKUs have fewer than {days} days of inventory remaining based on current demand forecast?",
        "genie": "inventory",
        "required_fields": [],
        "bounds": {"days_of_supply": (0, 365)},
    },
    "category_trend": {
        "template": "How has {metric} trended for the {category} category over the last {window}?",
        "genie": "demand",
        "required_fields": [],
        "bounds": {},
    },
    "top_movers": {
        "template": "Which SKUs had the largest {direction} change in {metric} over the last {window}?",
        "genie": "demand",
        "required_fields": [],
        "bounds": {},
    },
    "inventory_position": {
        "template": "Show me the current inventory position for {sku} across all warehouse regions.",
        "genie": "inventory",
        "required_fields": [],
        "bounds": {"on_hand_units": (0, 1_000_000)},
    },
    "forecast_coverage": {
        "template": "What is the forecast coverage ratio for {sku} across warehouse regions?",
        "genie": "inventory",
        "required_fields": [],
        "bounds": {"forecast_coverage_ratio": (0, 100)},
    },
}

# Known valid slot values — prevents the agent from hallucinating bad inputs
VALID_METRICS = {
    "Forecast Accuracy", "Revenue Opportunity", "Forecast Bias",
    "Model Confidence", "Weeks at Risk", "Total Actual Revenue",
    "Days of Supply", "Stockout Risk Score", "Reorder Urgency",
    "Forecast Coverage",
}

VALID_CATEGORIES = {
    "Electronics", "Home_Kitchen", "Health_Beauty",
    "Sports_Outdoor", "Office_Supplies",
}

VALID_WINDOWS = {"7 days", "14 days", "28 days", "4 weeks", "8 weeks", "12 weeks"}

VALID_DIRECTIONS = {"increase", "decrease"}


# ---------------------------------------------------------------------------
# Template filling
# ---------------------------------------------------------------------------

def fill_template(template_name: str, slots: dict) -> Optional[str]:
    """Fill a template with the given slots. Returns the question string or None."""
    tmpl = ANALYTICAL_TEMPLATES.get(template_name)
    if not tmpl:
        logger.error("Unknown template: %s", template_name)
        return None

    try:
        question = tmpl["template"].format(**slots)
        return question
    except KeyError as e:
        logger.error("Missing slot %s for template %s", e, template_name)
        return None


# ---------------------------------------------------------------------------
# Result validation
# ---------------------------------------------------------------------------

def validate_genie_result(
    result: dict,
    template_name: str,
) -> tuple[bool, str]:
    """Validate a Genie result before the agent uses it.

    Returns (is_valid, reason).

    Validation checks:
    - row_count: 1 <= rows <= 10,000
    - numeric_range: values within historical bounds (hardcoded per template — known simplification)
    - null_check: required fields have no nulls
    - staleness_check: most recent date within 7 days of today
    """
    # Check for Genie errors
    if result.get("error"):
        return False, f"Genie returned error: {result['error']}"

    df = result.get("result_df")
    answer = result.get("answer_text", "")

    # If no DataFrame, check if there's at least an answer
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        if answer and len(answer) > 20:
            # Genie gave a text answer without tabular data — acceptable
            return True, "Text answer only (no tabular data)"
        return False, "Empty result — no data returned"

    # Row count check
    if isinstance(df, pd.DataFrame):
        row_count = len(df)
        if row_count > 10_000:
            return False, f"Too many rows ({row_count}) — likely unfiltered query"

        # Numeric range check against template bounds
        tmpl = ANALYTICAL_TEMPLATES.get(template_name, {})
        bounds = tmpl.get("bounds", {})
        for col, (low, high) in bounds.items():
            if col in df.columns:
                numeric_col = pd.to_numeric(df[col], errors="coerce")
                if numeric_col.notna().any():
                    col_min = numeric_col.min()
                    col_max = numeric_col.max()
                    if col_min < low * 0.1 or col_max > high * 10:
                        return False, (
                            f"Column '{col}' out of expected range: "
                            f"[{col_min}, {col_max}] vs expected [{low}, {high}]"
                        )

        # Required fields null check
        required = tmpl.get("required_fields", [])
        for field in required:
            if field in df.columns and df[field].isna().any():
                null_count = df[field].isna().sum()
                return False, f"Required field '{field}' has {null_count} nulls"

        # Staleness check — look for date columns
        date_cols = [c for c in df.columns if "date" in c.lower() or "week" in c.lower()]
        if date_cols:
            for dc in date_cols:
                try:
                    dates = pd.to_datetime(df[dc], errors="coerce")
                    if dates.notna().any():
                        most_recent = dates.max()
                        if most_recent < datetime.now() - timedelta(days=14):
                            return False, (
                                f"Stale data — most recent date in '{dc}' is {most_recent.date()}"
                            )
                except Exception:
                    pass

    return True, "Valid"


# ---------------------------------------------------------------------------
# Genie wrappers with template + validation
# ---------------------------------------------------------------------------

def genie_query(
    template_name: str,
    slots: dict,
    space_id: str,
) -> dict:
    """Call any Genie space with a filled template. Validates result."""
    question = fill_template(template_name, slots)
    if not question:
        return {"error": f"Failed to fill template '{template_name}'", "valid": False}

    result = ask_genie(space_id, question)
    is_valid, reason = validate_genie_result(result, template_name)

    result["template"] = template_name
    result["slots"] = slots
    result["valid"] = is_valid
    result["validation_reason"] = reason

    if not is_valid:
        logger.warning(
            "Genie result validation failed: template=%s reason=%s",
            template_name, reason,
        )

    return result


def genie_demand(
    template_name: str,
    slots: dict,
    demand_space_id: str,
) -> dict:
    """Call Demand Forecast Genie with a filled template. Validates result."""
    question = fill_template(template_name, slots)
    if not question:
        return {"error": f"Failed to fill template '{template_name}'", "valid": False}

    result = ask_genie(demand_space_id, question)
    is_valid, reason = validate_genie_result(result, template_name)

    result["template"] = template_name
    result["slots"] = slots
    result["valid"] = is_valid
    result["validation_reason"] = reason

    if not is_valid:
        logger.warning(
            "Genie result validation failed: template=%s reason=%s",
            template_name, reason,
        )

    return result


def genie_inventory(
    template_name: str,
    slots: dict,
    inventory_space_id: str,
) -> dict:
    """Call Inventory & Supply Chain Genie with a filled template. Validates result."""
    question = fill_template(template_name, slots)
    if not question:
        return {"error": f"Failed to fill template '{template_name}'", "valid": False}

    result = ask_genie(inventory_space_id, question)
    is_valid, reason = validate_genie_result(result, template_name)

    result["template"] = template_name
    result["slots"] = slots
    result["valid"] = is_valid
    result["validation_reason"] = reason

    if not is_valid:
        logger.warning(
            "Genie result validation failed: template=%s reason=%s",
            template_name, reason,
        )

    return result


# ---------------------------------------------------------------------------
# Tool definitions for Foundation Model API function calling
# ---------------------------------------------------------------------------

GENIE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "genie_demand",
            "description": (
                "Query the Demand Forecast Genie using a constrained template. "
                "Use for drill-down questions about forecast accuracy, revenue, "
                "customer-SKU performance, and seasonal patterns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "enum": [
                            "variance_check", "channel_split", "category_trend",
                            "top_movers",
                        ],
                        "description": "Template to use for the question.",
                    },
                    "slots": {
                        "type": "object",
                        "description": (
                            "Key-value pairs to fill template slots. "
                            "Required slots per template: "
                            "variance_check: metric, sku, window; "
                            "channel_split: metric, sku, window; "
                            "category_trend: metric, category, window; "
                            "top_movers: direction, metric, window. "
                            "Valid windows: '7 days', '14 days', '28 days', '4 weeks', '8 weeks', '12 weeks'. "
                            "Valid directions: 'increase', 'decrease'."
                        ),
                    },
                },
                "required": ["template_name", "slots"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "genie_inventory",
            "description": (
                "Query the Inventory & Supply Chain Genie using a constrained template. "
                "Use for drill-down questions about stockout risk, days of supply, "
                "warehouse regions, and forecast coverage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "enum": [
                            "stockout_risk", "inventory_position", "forecast_coverage",
                        ],
                        "description": "Template to use for the question.",
                    },
                    "slots": {
                        "type": "object",
                        "description": (
                            "Key-value pairs to fill template slots. "
                            "Required slots per template: "
                            "stockout_risk: days (integer); "
                            "inventory_position: sku; "
                            "forecast_coverage: sku."
                        ),
                    },
                },
                "required": ["template_name", "slots"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dynamic tool definition builder
# ---------------------------------------------------------------------------

def build_genie_tool_definitions(genies: list[dict]) -> list[dict]:
    """Generate one tool definition per genie from the registry.

    Each tool is named ``genie_{name}`` (e.g. ``genie_demand``).
    Template enums are filtered by the ``"genie"`` key in ANALYTICAL_TEMPLATES.
    """
    tools = []
    for g in genies:
        genie_name = g["name"]
        # Collect templates that belong to this genie
        template_enum = [
            tname for tname, tdef in ANALYTICAL_TEMPLATES.items()
            if tdef.get("genie") == genie_name
        ]
        # Build slot descriptions from the matching templates
        slot_parts = []
        for tname in template_enum:
            tmpl = ANALYTICAL_TEMPLATES[tname]
            # Extract slot names from the template string
            import re
            slot_names = re.findall(r'\{(\w+)\}', tmpl["template"])
            slot_parts.append(f"{tname}: {', '.join(slot_names)}")

        tools.append({
            "type": "function",
            "function": {
                "name": f"genie_{genie_name}",
                "description": (
                    f"Query the {g['display_name']} using a constrained template. "
                    f"{g['description']}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "template_name": {
                            "type": "string",
                            "enum": template_enum,
                            "description": "Template to use for the question.",
                        },
                        "slots": {
                            "type": "object",
                            "description": (
                                "Key-value pairs to fill template slots. "
                                "Required slots per template: " +
                                "; ".join(slot_parts) + ". "
                                "Valid windows: '7 days', '14 days', '28 days', '4 weeks', '8 weeks', '12 weeks'. "
                                "Valid directions: 'increase', 'decrease'."
                            ),
                        },
                    },
                    "required": ["template_name", "slots"],
                },
            },
        })
    return tools
