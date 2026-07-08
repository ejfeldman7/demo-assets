"""
agent.py — LLM-powered dashboard builder agent.

Build flow:
  Phase 1: Design + validate SQL datasets (each query executed against warehouse).
  Phase 2: Generate full Lakeview dashboard JSON from validated datasets.
  Phase 3: Review + refine spec in a loop against the ai-dev-kit skill documentation.
  Deploy:  Publish the approved spec via w.lakeview.create().

Returns (agent_message, dashboard_url, dashboard_json_dict, dashboard_id).
"""

import json
import os
import re
import threading
import time
from pathlib import Path

from openai import OpenAI
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.dashboards import Dashboard

# ---------------------------------------------------------------------------
# Load ai-dev-kit skill files for the reviewer agent (Phase 3).
# These are the authoritative Lakeview format docs — much more detailed than
# our hand-written LAKEVIEW_FORMAT prompt. Bundled under skills/ alongside
# this file so the app has no runtime dependency on any other workspace path.
# ---------------------------------------------------------------------------
_SKILLS_DIR = Path(__file__).resolve().parent / "skills" / "databricks-aibi-dashboards"


def _load_skill(name: str) -> str:
    """Read a bundled skill file from disk."""
    try:
        return (_SKILLS_DIR / name).read_text()
    except Exception as exc:
        print(f"[WARN] Could not load bundled skill file {name}: {exc}")
        return ""


_SKILL_MAIN = _load_skill("SKILL.md")
_SKILL_WIDGET_SPECS = _load_skill("1-widget-specifications.md")
_SKILL_ADV_WIDGETS = _load_skill("2-advanced-widget-specifications.md")
_SKILL_FILTERS = _load_skill("3-filters.md")
_SKILL_TROUBLESHOOTING = _load_skill("5-troubleshooting.md")

# ---------------------------------------------------------------------------
# Gold catalog scope. Discovery below is ALWAYS locked to this catalog/schema —
# never derived from user input or LLM output — so moving to live discovery
# does not widen what the agent can see beyond this one catalog.schema.
#
# Set GOLD_CATALOG / GOLD_SCHEMA (env vars, e.g. in app.yaml) to the Unity
# Catalog catalog/schema you want this deployment scoped to.
# ---------------------------------------------------------------------------
SCOPE_CATALOG = os.environ.get("GOLD_CATALOG", "your_catalog")
SCOPE_SCHEMA = os.environ.get("GOLD_SCHEMA", "your_schema")

# ---------------------------------------------------------------------------
# Static fallback catalog — used ONLY if live schema discovery (below) fails.
# Deliberately generic: a fallback tailored to one deployment's actual tables
# would be actively misleading in anyone else's. If you want a real fallback
# for your own deployment, put the same "table: col(type), ..." shape here.
# ---------------------------------------------------------------------------
_GOLD_CATALOG_FALLBACK = f"""
Live schema discovery for {SCOPE_CATALOG}.{SCOPE_SCHEMA} is temporarily unavailable and
no static fallback schema has been configured for this deployment.
Do not guess table or column names — call out that dataset design cannot proceed
until the warehouse/catalog is reachable again.
"""

# ---------------------------------------------------------------------------
# Live gold catalog discovery.
#
# Replaces the hand-maintained column list above with a live lookup, scoped
# to SCOPE_CATALOG.SCOPE_SCHEMA only (see constants above). Uses the same
# databricks_tools_core helper the (currently unused) manage_dashboard tool
# scaffolding already depends on, called directly rather than through an
# LLM tool-calling loop since we control exactly when discovery should run.
#
# Cached in-process with a TTL so normal build/chat traffic doesn't add a
# schema round-trip to every request — the gold layer's schema changes rarely.
# Falls back to _GOLD_CATALOG_FALLBACK (logged loudly) if discovery fails.
# ---------------------------------------------------------------------------
_GOLD_CATALOG_CACHE_TTL = 900  # 15 minutes
_gold_catalog_lock = threading.Lock()
_gold_catalog_cache: str | None = None
_gold_catalog_cached_at: float = 0.0


def _format_gold_catalog(result) -> str:
    """Render a databricks_tools_core TableSchemaResult into the same prompt shape as the static fallback."""
    lines = [
        f"All tables live in {SCOPE_CATALOG}.{SCOPE_SCHEMA}. "
        f"Always qualify: {SCOPE_CATALOG}.{SCOPE_SCHEMA}.<table_name>.",
        "Use Spark SQL. DATE_TRUNC('month', col) for monthly grouping. "
        "No SAFE_DIVIDE (use col/NULLIF(denom,0)).",
        "",
        "SCHEMAS (exact column names and types — use ONLY these):",
        "",
    ]
    for table in sorted(result.tables, key=lambda t: t.name):
        if table.error or not table.column_details:
            print(f"[WARN] Skipping {table.name} in live schema discovery: {table.error or 'no columns returned'}")
            continue
        cols = ", ".join(f"{c.name}({c.data_type})" for c in table.column_details.values())
        lines.append(f"{table.name}: {cols}")
    return "\n".join(lines)


def _get_gold_catalog(warehouse_id: str) -> str:
    """Return the (cached) live gold catalog prompt block, discovering it if the cache is stale/empty."""
    global _gold_catalog_cache, _gold_catalog_cached_at

    now = time.time()
    if _gold_catalog_cache is not None and (now - _gold_catalog_cached_at) < _GOLD_CATALOG_CACHE_TTL:
        return _gold_catalog_cache

    with _gold_catalog_lock:
        # Re-check: another thread may have refreshed the cache while we waited on the lock.
        now = time.time()
        if _gold_catalog_cache is not None and (now - _gold_catalog_cached_at) < _GOLD_CATALOG_CACHE_TTL:
            return _gold_catalog_cache

        try:
            from databricks_tools_core.sql import TableStatLevel, get_table_stats_and_schema

            result = get_table_stats_and_schema(
                catalog=SCOPE_CATALOG,
                schema=SCOPE_SCHEMA,
                table_stat_level=TableStatLevel.NONE,
                warehouse_id=warehouse_id,
            )
            if not result.tables:
                raise RuntimeError(f"No tables found in {SCOPE_CATALOG}.{SCOPE_SCHEMA}.")

            formatted = _format_gold_catalog(result)
            _gold_catalog_cache = formatted
            _gold_catalog_cached_at = now
            print(f"[INFO] Refreshed live schema for {SCOPE_CATALOG}.{SCOPE_SCHEMA} ({len(result.tables)} tables).")
            return formatted
        except Exception as exc:
            print(
                f"[WARN] Live schema discovery for {SCOPE_CATALOG}.{SCOPE_SCHEMA} failed ({exc}); "
                "falling back to the static gold catalog."
            )
            return _GOLD_CATALOG_FALLBACK


# ---------------------------------------------------------------------------
# Lakeview JSON spec guide for Phase 2 (generation).
# The skill files loaded above are used for Phase 3 (review).
# ---------------------------------------------------------------------------
LAKEVIEW_FORMAT = """
Produce a valid Lakeview dashboard JSON. CRITICAL structural rules:

DATASET FORMAT — use queryLines array, NOT "query" string:
{
  "datasets": [
    {
      "name": "ds_snake_case",
      "displayName": "Human Readable Label",
      "queryLines": ["SELECT col1, col2 FROM your_catalog.your_schema.table_name GROUP BY col1 ORDER BY col2 DESC LIMIT 20"]
    }
  ],
  "pages": [
    {
      "name": "page_1",
      "displayName": "Page Title",
      "pageType": "PAGE_TYPE_CANVAS",
      "layoutVersion": "GRID_V1",
      "layout": [
        {
          "widget": {
            "name": "title",
            "multilineTextboxSpec": {"lines": ["## Page Title"]}
          },
          "position": {"x": 0, "y": 0, "width": 12, "height": 1}
        },
        {
          "widget": {
            "name": "subtitle",
            "multilineTextboxSpec": {"lines": ["Description text"]}
          },
          "position": {"x": 0, "y": 1, "width": 12, "height": 1}
        },
        {
          "widget": {
            "name": "kpi-total-revenue",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_snake_case",
              "fields": [{"name": "revenue", "expression": "`revenue`"}], "disaggregated": true}}],
            "spec": {
              "version": 2, "widgetType": "counter",
              "frame": {"title": "Total Revenue", "showTitle": true},
              "encodings": {"value": {"fieldName": "revenue", "displayName": "Revenue ($)",
                "format": {"type": "number-currency", "currencyCode": "USD", "abbreviation": "compact"}}}
            }
          },
          "position": {"x": 0, "y": 2, "width": 4, "height": 3}
        },
        {
          "widget": {
            "name": "revenue-by-carrier",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_snake_case",
              "fields": [{"name": "carrier_name", "expression": "`carrier_name`"},
                         {"name": "sum(revenue)", "expression": "SUM(`revenue`)"}]}}],
            "spec": {
              "version": 3, "widgetType": "bar",
              "frame": {"title": "Revenue by Carrier", "showTitle": true},
              "encodings": {
                "x": {"fieldName": "carrier_name", "scale": {"type": "categorical"}, "displayName": "Carrier"},
                "y": {"fieldName": "sum(revenue)", "scale": {"type": "quantitative"}, "displayName": "Revenue ($)"}
              }
            }
          },
          "position": {"x": 0, "y": 5, "width": 6, "height": 6}
        },
        {
          "widget": {
            "name": "ontime-by-carrier",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_snake_case",
              "fields": [{"name": "carrier_name", "expression": "`carrier_name`"},
                         {"name": "avg(on_time_pct)", "expression": "AVG(`on_time_pct`)"}]}}],
            "spec": {
              "version": 3, "widgetType": "bar",
              "frame": {"title": "On-Time % by Carrier", "showTitle": true},
              "encodings": {
                "x": {"fieldName": "carrier_name", "scale": {"type": "categorical"}, "displayName": "Carrier"},
                "y": {"fieldName": "avg(on_time_pct)", "scale": {"type": "quantitative"}, "displayName": "On-Time %"}
              }
            }
          },
          "position": {"x": 6, "y": 5, "width": 6, "height": 6}
        },
        {
          "widget": {
            "name": "detail-table",
            "queries": [{"name": "main_query", "query": {"datasetName": "ds_snake_case",
              "fields": [{"name": "carrier_name", "expression": "`carrier_name`"},
                         {"name": "revenue", "expression": "`revenue`"}],
              "disaggregated": true}}],
            "spec": {
              "version": 2, "widgetType": "table",
              "frame": {"title": "Detail View", "showTitle": true},
              "encodings": {"columns": [
                {"fieldName": "carrier_name", "displayName": "Carrier"},
                {"fieldName": "revenue", "displayName": "Revenue"}
              ]}
            }
          },
          "position": {"x": 0, "y": 11, "width": 12, "height": 6}
        }
      ]
    }
  ]
}

=== FIELD NAME RULE (CRITICAL) ===
query.fields[].name MUST exactly match encodings.fieldName.
  - Raw column:    name="carrier_name"   expression="`carrier_name`"   fieldName="carrier_name"
  - Aggregated:    name="sum(revenue)"   expression="SUM(`revenue`)"   fieldName="sum(revenue)"
  - Aggregated:    name="avg(on_time)"   expression="AVG(`on_time_pct`)" fieldName="avg(on_time)"
If they don't match, the widget shows "no selected fields to visualize".

=== TEXT WIDGET RULES ===
- Use multilineTextboxSpec: {"lines": ["text"]}  (NOT "content": "text")
- NEVER add a spec block to a text widget
- Title and subtitle MUST be SEPARATE widgets at y=0 (h=1) and y=1 (h=1)

=== LAYOUT RULES (12-column grid) ===
- Canvas is 12 columns wide. Every row MUST sum to exactly width=12 (no gaps).
- Title text widget:  x=0 y=0  width=12 height=1
- Subtitle text:      x=0 y=1  width=12 height=1
- KPI row (3 KPIs):   width=4 each at x=0,4,8 — height=3 (NEVER height=2)
- KPI row (4 KPIs):   width=3 each at x=0,3,6,9 — height=3
- Two side-by-side charts: x=0 width=6 and x=6 width=6 — height=5 or 6
- Full-width table:   x=0 width=12 height=6

=== VERSIONS ===
counter=2, table=2, bar=3, line=3, area=3, pie=3, scatter=3

=== SQL RULES ===
- Spark SQL. Always qualify tables with the catalog.schema given in the dataset definitions.
- Bar/pie: always GROUP BY the x/color dimension. LIMIT 15.
- Line charts by carrier/client: pre-filter to TOP 6 by volume.
- Date: DATE_TRUNC('month', col) for monthly.
"""


def _repack_layout(layout: list, cols: int = 12) -> list:
    """
    Enforce minimum widths then repack widgets left-to-right, top-to-bottom.
    Prevents charts from being narrow or overlapping after LLM generation.
    """
    min_w = {
        "bar": 6,
        "line": 6,
        "area": 6,
        "scatter": 6,
        "pie": 4,
        "table": 12,
        "counter": 3,
    }
    for item in layout:
        widget = item.get("widget", {})
        if "multilineTextboxSpec" in widget:
            item.setdefault("position", {})["width"] = cols
            item["position"]["x"] = 0
            continue
        sp = widget.get("spec") or {}
        wtype = sp.get("widgetType", "")
        pos = item.setdefault("position", {})
        required_width = min_w.get(wtype, 3)
        if wtype == "table":
            pos["width"] = cols
            pos["x"] = 0
        elif pos.get("width", 0) < required_width:
            pos["width"] = required_width

    items = sorted(
        layout,
        key=lambda i: (i.get("position", {}).get("y", 0), i.get("position", {}).get("x", 0)),
    )

    cur_y, cur_x, row_h = 0, 0, 0
    result = []
    for item in items:
        pos = item.get("position", {})
        w = min(pos.get("width", 6), cols)
        h = pos.get("height", 6)
        if w == cols:
            if cur_x > 0:
                cur_y += row_h
                cur_x, row_h = 0, 0
            pos["x"], pos["y"], pos["width"] = 0, cur_y, cols
            cur_y += h
            cur_x, row_h = 0, 0
        else:
            if cur_x + w > cols:
                cur_y += row_h
                cur_x, row_h = 0, 0
            pos["x"], pos["y"], pos["width"] = cur_x, cur_y, w
            cur_x += w
            row_h = max(row_h, h)
        result.append(item)
    return result


def _fix_spec_minimal(spec: dict) -> dict:
    """Lightweight structural fixes applied just before deployment."""
    for ds in spec.get("datasets", []):
        if "query" in ds and "queryLines" not in ds:
            ds["queryLines"] = [ds.pop("query")]

    version_map = {
        "counter": 2,
        "table": 2,
        "bar": 3,
        "line": 3,
        "area": 3,
        "pie": 3,
        "scatter": 3,
        "filter-multi-select": 2,
        "filter-single-select": 2,
        "filter-date-range-picker": 2,
        "combo": 1,
    }
    for page in spec.get("pages", []):
        page.setdefault("pageType", "PAGE_TYPE_CANVAS")
        page.setdefault("layoutVersion", "GRID_V1")
        for item in page.get("layout", []):
            widget = item.get("widget", {})
            if "multilineTextboxSpec" in widget:
                widget.pop("spec", None)
                continue
            sp = widget.get("spec")
            if not sp:
                continue
            wtype = sp.get("widgetType", "")
            if wtype in version_map:
                sp["version"] = version_map[wtype]
            raw_name = widget.get("name", wtype)
            clean_name = re.sub(r"^w[_\s]+", "", raw_name, flags=re.IGNORECASE)
            display_title = clean_name.replace("-", " ").replace("_", " ").title()
            sp.setdefault(
                "frame",
                {
                    "title": display_title,
                    "showTitle": True,
                },
            )
            encodings = sp.get("encodings", {})
            needed, seen = [], set()

            def _walk(v):
                if isinstance(v, dict):
                    fn = v.get("fieldName")
                    if fn and fn not in seen:
                        seen.add(fn)
                        needed.append(fn)
                    for child in v.values():
                        _walk(child)
                elif isinstance(v, list):
                    for child in v:
                        _walk(child)

            _walk(encodings)
            for q in widget.get("queries", []):
                qb = q.get("query", {})
                if not qb.get("fields") and needed:
                    qb["fields"] = [{"name": fn, "expression": f"`{fn}`"} for fn in needed]
                    if wtype == "counter":
                        qb["disaggregated"] = True
        page["layout"] = _repack_layout(page.get("layout", []))
    return spec


# ---------------------------------------------------------------------------
# Phase 3: Review + refine loop using ai-dev-kit skill documentation
# ---------------------------------------------------------------------------
def _review_and_refine(
    spec: dict,
    validated_datasets: list[dict],
    client: OpenAI,
    max_rounds: int = 3,
) -> dict:
    """
    Critic loop: passes the dashboard spec to an LLM reviewer armed with the
    authoritative ai-dev-kit skill documentation. The reviewer either returns
    APPROVED (done) or a corrected spec with a list of issues found.

    Iterates up to `max_rounds` times. The validated SQL from Phase 1 is
    re-injected after each round so the reviewer cannot corrupt it.
    """
    if not (_SKILL_WIDGET_SPECS or _SKILL_TROUBLESHOOTING):
        print("[WARN] Skill files not loaded — skipping review phase.")
        return spec

    col_ctx = "\n".join(
        f"  {ds['name']}: [{ds.get('_columns', 'unknown')}]" for ds in validated_datasets
    )

    reviewer_system = (
        "You are an expert Databricks AI/BI Lakeview dashboard reviewer. "
        "Your sole job is to find and fix structural, formatting, and field-name issues "
        "in a dashboard JSON spec before it is deployed.\n\n"
        "=== OFFICIAL WIDGET SPECIFICATIONS ===\n"
        f"{_SKILL_WIDGET_SPECS}\n\n"
        "=== ADVANCED WIDGET SPECIFICATIONS ===\n"
        f"{_SKILL_ADV_WIDGETS}\n\n"
        "=== FILTER SPECIFICATIONS ===\n"
        f"{_SKILL_FILTERS}\n\n"
        "=== TROUBLESHOOTING GUIDE ===\n"
        f"{_SKILL_TROUBLESHOOTING}\n\n"
        "=== OFFICIAL GUIDELINES (quality checklist + layout) ===\n"
        f"{_SKILL_MAIN}\n\n"
        "HOW TO RESPOND (STRICT — this output is parsed by code, not read by a human):\n"
        "- Output ONLY one of the two forms below. NEVER include analysis, a checklist walkthrough,\n"
        "  markdown headers, or any prose before/after — even to explain that the spec looks correct.\n"
        "- If the spec has NO issues: output ONLY the literal word APPROVED and nothing else.\n"
        "- If the spec has issues: output ONLY a JSON object with two keys, no fences, no commentary:\n"
        '    {"issues": ["brief description of each fix"], "spec": {... complete corrected spec ...}}\n'
        "  The spec value MUST be the full corrected dashboard JSON, not a diff.\n\n"
        "TOP ISSUES TO CATCH (check every one):\n"
        "1. FIELD NAME MISMATCH: query.fields[].name must EXACTLY match encodings.fieldName.\n"
        "   Raw column: name='carrier_name' expression='`carrier_name`' -> fieldName='carrier_name'.\n"
        "   Aggregated: name='sum(revenue)' expression='SUM(`revenue`)' -> fieldName='sum(revenue)'.\n"
        "   This is the #1 cause of 'no selected fields to visualize' errors.\n"
        "2. TEXT WIDGET FORMAT: use multilineTextboxSpec: {\"lines\": [\"text\"]} - NOT content: \"text\".\n"
        "   Text widgets must NOT have a spec block. widgetType: 'text' is INVALID.\n"
        "   Title (y=0, h=1) and subtitle (y=1, h=1) must be SEPARATE widgets.\n"
        "3. VERSIONS: counter=2, table=2, bar/line/pie/area/scatter=3.\n"
        "4. PAGE METADATA: every page needs pageType AND layoutVersion: 'GRID_V1'.\n"
        "5. LAYOUT: every row must sum to exactly width=12. No gaps, no overflow.\n"
        "   KPI height must be 3 or 4 (NEVER 2). Charts: height 5 or 6.\n"
        "6. DATASETS: queryLines (array), NOT query (string).\n"
        "7. COUNTER disaggregated: true when dataset is pre-aggregated (1 row).\n"
        "8. TABLE encodings: columns array with fieldName + displayName only.\n"
        "9. COLOR CARDINALITY: no chart color dimension with >8 categories.\n"
        "10. AXIS LABELS: every chart encoding should have a displayName.\n\n"
        "DO NOT modify queryLines content — SQL is already validated."
    )

    sql_map = {ds["name"]: ds["sql"] for ds in validated_datasets if "sql" in ds}

    current_spec = spec
    retry_hint = ""
    for round_num in range(1, max_rounds + 1):
        review_user = (
            f"Review this dashboard spec. Available dataset columns:\n{col_ctx}\n\n"
            f"Spec:\n```json\n{json.dumps(current_spec, indent=2)}\n```"
            f"{retry_hint}"
        )
        response, model = _chat_with_fallback(
            client,
            [
                {"role": "system", "content": reviewer_system},
                {"role": "user", "content": review_user},
            ],
            max_tokens=10000,
            temperature=0.0,
        )
        print(f"[INFO] Review round {round_num}/{max_rounds} by {model}")

        stripped = response.strip()
        # Accept "APPROVED" as the whole response, allowing for stray
        # surrounding punctuation/markdown the model might still add.
        if stripped.strip("`*_. \n").upper() == "APPROVED":
            print(f"[INFO] Spec APPROVED after {round_num} review round(s).")
            break

        try:
            result = _extract_json(stripped)
            issues = result.get("issues", [])
            print(f"[INFO] Reviewer found {len(issues)} issue(s):")
            for issue in issues[:15]:
                print(f"[INFO]   • {issue}")
            new_spec = result.get("spec")
            if isinstance(new_spec, dict) and new_spec:
                current_spec = new_spec
                retry_hint = ""
            else:
                print("[WARN] Reviewer returned issues but no corrected spec — stopping review.")
                break
        except Exception as exc:
            print(f"[WARN] Review round {round_num} response unparseable ({exc}); retrying with a stricter reminder.")
            retry_hint = (
                "\n\nIMPORTANT: your previous response could not be parsed. "
                "Respond with ONLY the literal word APPROVED, or ONLY a single raw JSON object "
                "as instructed above — no analysis, no markdown, no commentary of any kind."
            )
            continue

        for ds in current_spec.get("datasets", []):
            name = ds.get("name", "")
            if name in sql_map:
                ds.pop("query", None)
                ds["queryLines"] = [sql_map[name]]

    return current_spec


# ---------------------------------------------------------------------------
# Model config and auth helpers
# ---------------------------------------------------------------------------
PRIMARY_MODEL = "databricks-claude-sonnet-4-6"
FALLBACK_MODEL = "databricks-gpt-5-4"


def _make_openai_client(w: WorkspaceClient) -> OpenAI:
    """Return an OpenAI-compatible client against Databricks FMAPI."""
    headers = w.config.authenticate()
    token = headers.get("Authorization", "").split("Bearer ", 1)[-1]
    if not token:
        raise RuntimeError("Could not obtain a bearer token from the Databricks SDK config.")
    return OpenAI(
        api_key=token,
        base_url=f"{w.config.host.rstrip('/')}/serving-endpoints",
    )


def _chat_with_fallback(client: OpenAI, messages: list, **kwargs) -> tuple[str, str]:
    """Try PRIMARY_MODEL first; on any error retry with FALLBACK_MODEL."""
    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
            return resp.choices[0].message.content, model
        except Exception as exc:
            print(f"[WARN] Model {model} failed ({exc}). Trying fallback.")
    raise RuntimeError(f"Both {PRIMARY_MODEL} and {FALLBACK_MODEL} failed.")


# ---------------------------------------------------------------------------
# SQL validation helper
#
# Dataset SQL is LLM-generated from free-text intake fields (business_question,
# description). Prompt instructions alone ("design SELECT queries") are not a
# security boundary — this guard rejects anything that isn't a plain read-only
# SELECT/WITH query BEFORE it reaches the warehouse, regardless of how it got
# there (bad generation or a prompt-injection attempt in the intake text).
# ---------------------------------------------------------------------------
_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_WRITE_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "COPY", "USE", "SET", "REFRESH",
    "VACUUM", "OPTIMIZE", "CALL", "RESTORE", "UNDROP", "COMMENT",
)
_SQL_WRITE_KEYWORD_RE = re.compile(r"\b(" + "|".join(_SQL_WRITE_KEYWORDS) + r")\b", re.IGNORECASE)


def _reject_if_not_read_only(sql: str) -> str | None:
    """Return an error string if sql isn't a plain read-only SELECT/WITH query, else None."""
    stripped = _SQL_COMMENT_RE.sub(" ", sql).strip()
    if ";" in stripped.rstrip(";"):
        return "Multiple statements are not allowed — write exactly one SELECT per dataset."
    first_word_match = re.match(r"[A-Za-z]+", stripped)
    first_word = first_word_match.group(0).upper() if first_word_match else ""
    if first_word not in ("SELECT", "WITH"):
        return (
            f"Only read-only SELECT/WITH queries are allowed for dataset SQL "
            f"(query started with '{first_word or stripped[:20]}')."
        )
    write_match = _SQL_WRITE_KEYWORD_RE.search(stripped)
    if write_match:
        return f"Query contains a disallowed write keyword: {write_match.group(0).upper()}."
    return None


def _test_sql(sql: str, warehouse_id: str, w: WorkspaceClient) -> tuple[bool, str]:
    """Execute sql against warehouse; return (success, column_names_or_error)."""
    from databricks.sdk.service.sql import Disposition, StatementState

    rejection = _reject_if_not_read_only(sql)
    if rejection:
        return False, rejection

    try:
        stmt = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=sql,
            wait_timeout="30s",
            disposition=Disposition.INLINE,
        )
        if stmt.status.state == StatementState.SUCCEEDED:
            cols = ""
            if stmt.manifest and stmt.manifest.schema and stmt.manifest.schema.columns:
                cols = ", ".join(c.name for c in stmt.manifest.schema.columns)
            return True, cols
        err = stmt.status.error.message if stmt.status and stmt.status.error else "unknown error"
        return False, err
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Warehouse helper
# ---------------------------------------------------------------------------
def _get_warehouse_id(w: WorkspaceClient) -> str:
    warehouses = list(w.warehouses.list())
    for wh in warehouses:
        if wh.state and str(wh.state.value) in ("RUNNING", "STOPPED"):
            return wh.id
    if warehouses:
        return warehouses[0].id
    raise RuntimeError("No SQL warehouse found.")


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict:
    """Robustly extract and parse the first JSON object from model output."""
    raw = text
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start == -1:
        print(f"[ERROR] No JSON found. Raw (first 500):\n{raw[:500]}")
        raise ValueError("No JSON object found in model response.")
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Found JSON block but failed to parse: {exc}") from exc
    raise ValueError("Malformed JSON in model response.")


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------
def build_dashboard(intake: dict, session_id: str, history: list[dict]) -> tuple[str, str, dict, str]:
    """
    Generate and publish a Lakeview dashboard.

    Phase 1: Design + validate SQL datasets.
    Phase 2: Generate Lakeview JSON spec.
    Phase 3: Review + refine spec against skill documentation (loop until APPROVED).
    Deploy:  Publish approved spec.

    Returns (agent_message, dashboard_url, dashboard_spec, dashboard_id).
    """
    final_result = None
    for event, payload in build_dashboard_stream(intake, session_id, history):
        if event == "result":
            final_result = payload
    if final_result is None:
        raise RuntimeError("Dashboard build did not produce a final result.")
    return final_result


def build_dashboard_stream(intake: dict, session_id: str, history: list[dict]):
    """Yield progress updates through the dashboard build lifecycle."""
    yield "progress", "Phase 1/5: Initializing workspace context"
    w = WorkspaceClient()
    client = _make_openai_client(w)
    warehouse_id = _get_warehouse_id(w)

    intake_summary = (
        f"Report Name: {intake.get('report_name', 'Dashboard')}\n"
        f"Business Question: {intake.get('business_question', '')}\n"
        f"Description: {intake.get('description', '')}\n"
        f"Key Metrics: {intake.get('key_metrics', '')}\n"
        f"Dimensions / Filters: {intake.get('dimensions', '')}\n"
        f"Time Period: {intake.get('time_period', 'Last 30 days')}"
    )

    yield "progress", "Phase 1/5: Designing dataset queries"
    phase1_system = (
        "You are a Databricks SQL expert designing dataset queries for a Lakeview dashboard.\n\n"
        + _get_gold_catalog(warehouse_id)
        + "\nRules:\n"
        f"- Use Spark SQL. Always qualify tables: {SCOPE_CATALOG}.{SCOPE_SCHEMA}.<table>.\n"
        "- Design 2-6 focused datasets. Each dataset serves 1-3 widgets.\n"
        "- Use DATE_TRUNC('month', col) for monthly grouping.\n"
        "- Column aliases must be snake_case (no spaces or special chars).\n"
        "- Pre-aggregate where appropriate; avoid SELECT *.\n"
        "- For line/bar charts broken out by a high-cardinality dimension (e.g. entity, region): "
        "limit to the TOP 6 by an appropriate volume/value metric (use a subquery with ORDER BY ... DESC LIMIT 6).\n"
        "- For bar/pie charts: LIMIT 15 rows max.\n"
        "- Avoid returning more than 100 rows per dataset.\n\n"
        "Output ONLY a JSON array, no prose or fences:\n"
        '[{"name": "ds_snake", "displayName": "Label", "sql": "SELECT ..."}]'
    )
    p1_msgs = [
        {"role": "system", "content": phase1_system},
        {"role": "user", "content": f"Design SQL datasets for this dashboard:\n{intake_summary}"},
    ]
    datasets_raw, m1 = _chat_with_fallback(client, p1_msgs, max_tokens=2048, temperature=0.1)
    print(f"[INFO] Phase 1 (query design) by {m1}")
    datasets = _extract_json(datasets_raw)
    if not isinstance(datasets, list):
        datasets = [datasets]

    yield "progress", f"Phase 1/5: Validating {len(datasets)} dataset query(s)"
    remaining_errors = []
    for attempt in range(3):
        remaining_errors = []
        for ds in datasets:
            ok, info = _test_sql(ds["sql"], warehouse_id, w)
            if ok:
                ds["_columns"] = info
                print(f"[INFO] Query OK: {ds['name']} -> {info}")
            else:
                remaining_errors.append(f"Dataset '{ds['name']}' failed: {info}\nSQL: {ds['sql']}")
                print(f"[WARN] Query failed ({ds['name']}): {info}")
        if not remaining_errors:
            break
        if attempt < 2:
            yield "progress", (
                f"Phase 1/5: Fixing {len(remaining_errors)} query issue(s) "
                f"and retrying (attempt {attempt + 2}/3)"
            )
            fix_msgs = [
                {"role": "system", "content": phase1_system},
                {
                    "role": "user",
                    "content": (
                        f"Fix these failing SQL queries:\n{intake_summary}\n\nErrors:\n"
                        + "\n".join(remaining_errors)
                        + "\n\nReturn the corrected full dataset array as JSON."
                    ),
                },
            ]
            fixed_raw, mfix = _chat_with_fallback(client, fix_msgs, max_tokens=2048, temperature=0.1)
            print(f"[INFO] Query fix attempt {attempt + 1} by {mfix}")
            datasets = _extract_json(fixed_raw)
            if not isinstance(datasets, list):
                datasets = [datasets]

    if remaining_errors:
        raise RuntimeError("Failed to validate dashboard dataset queries after 3 attempts.")

    validated_ctx = ""
    for ds in datasets:
        cols = ds.get("_columns", "unknown")
        validated_ctx += f"- {ds['name']} ({ds['displayName']}): columns=[{cols}]\n  SQL: {ds['sql']}\n"

    yield "progress", "Phase 2/5: Generating dashboard definition"
    phase2_system = (
        "You are an expert Databricks Lakeview dashboard builder.\n\n"
        + LAKEVIEW_FORMAT
        + "\nOutput ONLY the raw JSON object for the full dashboard. No prose, no fences."
    )
    phase2_user = (
        f"Build a complete Lakeview dashboard JSON for:\n{intake_summary}\n\n"
        f"Use EXACTLY these pre-validated datasets (reference by name, do not change the SQL):\n"
        f"{validated_ctx}\n"
        "STRUCTURE REQUIREMENTS:\n"
        "- Start every page with a title text widget (y=0, h=1) and subtitle widget (y=1, h=1).\n"
        "- Include 2-4 KPI counter widgets (width=4 each, height=3, y=2).\n"
        "- Place charts in pairs side by side: x=0 w=6 and x=6 w=6, height=5 or 6.\n"
        "- End each page with a full-width detail table (x=0 w=12).\n"
        "- Every row must sum to exactly width=12.\n\n"
        "TITLE REQUIREMENTS:\n"
        "- Every widget's frame.title must be a clean, business-friendly phrase.\n"
        "- Good: 'Revenue by Carrier', 'Monthly On-Time Trend'. Bad: 'w_revenue', 'Chart 1'.\n\n"
        "FIELD NAME RULE:\n"
        "- query.fields[].name MUST exactly match the fieldName used in encodings.\n\n"
        "All widget fieldNames must exactly match the column names shown in the dataset definitions above."
    )
    p2_msgs = [
        {"role": "system", "content": phase2_system},
        *history[-10:],
        {"role": "user", "content": phase2_user},
    ]
    raw, model_used = _chat_with_fallback(client, p2_msgs, max_tokens=6000, temperature=0.1)
    print(f"[INFO] Phase 2 (dashboard JSON) by {model_used}")
    dashboard_spec = _extract_json(raw)

    spec_ds_map = {ds["name"]: ds for ds in datasets}
    for spec_dataset in dashboard_spec.get("datasets", []):
        ds_name = spec_dataset.get("name", "")
        if ds_name in spec_ds_map:
            spec_dataset.pop("query", None)
            spec_dataset["queryLines"] = [spec_ds_map[ds_name]["sql"]]

    yield "progress", "Phase 3/5: Applying structural fixes"
    dashboard_spec = _fix_spec_minimal(dashboard_spec)

    yield "progress", "Phase 4/5: Reviewing definition against ai-dev-kit dashboard skills"
    dashboard_spec = _review_and_refine(dashboard_spec, datasets, client, max_rounds=3)

    for spec_dataset in dashboard_spec.get("datasets", []):
        ds_name = spec_dataset.get("name", "")
        if ds_name in spec_ds_map:
            spec_dataset.pop("query", None)
            spec_dataset["queryLines"] = [spec_ds_map[ds_name]["sql"]]

    dashboard_spec = _fix_spec_minimal(dashboard_spec)

    yield "progress", "Phase 5/5: Creating dashboard in Databricks"
    current_user = w.current_user.me()
    parent_path = f"/Workspace/Users/{current_user.user_name}/dashboards"
    try:
        w.workspace.mkdirs(path=parent_path)
    except Exception:
        pass

    report_name = intake.get("report_name") or f"Dashboard {session_id[:8]}"
    created = w.lakeview.create(
        Dashboard(
            display_name=report_name,
            serialized_dashboard=json.dumps(dashboard_spec),
            warehouse_id=warehouse_id,
            parent_path=parent_path,
        )
    )
    dashboard_url = f"{w.config.host.rstrip('/')}/dashboards/{created.dashboard_id}"

    n_ds = len(dashboard_spec.get("datasets", []))
    n_wg = sum(len(p.get("layout", [])) for p in dashboard_spec.get("pages", []))
    n_pg = len(dashboard_spec.get("pages", []))
    agent_message = (
        f"Dashboard **{report_name}** created successfully!\n\n"
        f"[Open Dashboard]({dashboard_url})\n\n"
        f"_{n_ds} dataset(s), {n_wg} widget(s) across {n_pg} page(s)._\n\n"
        "You can ask me to revise it or submit a new version below."
    )
    yield "result", (agent_message, dashboard_url, dashboard_spec, created.dashboard_id)


# ---------------------------------------------------------------------------
# Conversational follow-up (no dashboard creation)
# ---------------------------------------------------------------------------
def chat_with_agent(message: str, history: list[dict]) -> str:
    """Respond to a follow-up question or revision request."""
    w = WorkspaceClient()
    client = _make_openai_client(w)
    warehouse_id = _get_warehouse_id(w)
    system = (
        "You are a helpful Databricks AI/BI dashboard assistant. "
        "You help business users refine and improve their Lakeview dashboards.\n\n"
        "## WHAT THE REBUILD PIPELINE CAN DO\n"
        "When the user clicks \'Rebuild\', the full generation pipeline runs again from scratch "
        "and can change ANYTHING about the dashboard. This includes:\n"
        "- Widget types: swap bar ↔ line ↔ pie ↔ area ↔ scatter, add/remove KPI counters\n"
        "- Layout and sizing: number of columns, widget heights, side-by-side vs stacked\n"
        "- Titles and axis labels: make them cleaner, shorter, more descriptive\n"
        "- Number formatting: currency ($1.2M), percentages (45.2%), compact notation\n"
        "- KPI tiles: which metrics to highlight, how many, which dataset to pull from\n"
        "- Charts: horizontal vs vertical bar, stacked vs grouped, color grouping dimension\n"
        "- Data shown: different aggregations, different time granularities, top-N filtering\n"
        "- New sections: add a page, add a trends section, add a detail table\n"
        "- Better data: use different gold tables, join more context, surface different KPIs\n\n"
        "## WHAT CANNOT BE CHANGED\n"
        "The Lakeview format does not support custom CSS, font changes, or color theme overrides "
        "(those are set at the workspace level). Everything else is fair game.\n\n"
        "## HOW TO RESPOND\n"
        "1. Confirm you understood the request.\n"
        "2. Describe SPECIFICALLY what you will change in the next build "
        "(e.g., \'I\'ll switch the carrier chart to a horizontal bar, add a % change KPI, "
        "and make all titles sentence-case\').\n"
        "3. Tell them to click **Rebuild Dashboard** to apply it.\n"
        "Be concise — 2-4 sentences max. Never say you can\'t do something that is in the "
        "\'CAN DO\' list above.\n\n"
        + _get_gold_catalog(warehouse_id)
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": message})
    reply, model_used = _chat_with_fallback(client, messages, max_tokens=600, temperature=0.3)
    print(f"[INFO] Chat response from {model_used}")
    return reply
