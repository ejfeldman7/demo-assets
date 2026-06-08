from __future__ import annotations

from typing import Dict, List, Tuple

from utils.audit_logger import log_action
from utils.auth import get_current_user_email
from utils.db_connection import execute_query, execute_update
from utils.validators import validate_identifier


def _extract_name(row: Dict[str, object]) -> str:
    """Return a catalog/schema/table name from SHOW output."""
    for key in ("catalog", "databaseName", "schema_name", "tableName", "table"):
        if key in row and row[key]:
            return str(row[key])
    return ""


def get_catalogs() -> List[str]:
    """Return a list of catalog names."""
    rows = execute_query("SHOW CATALOGS")
    names = [_extract_name(row) for row in rows]
    return sorted(name for name in names if name)


def get_schemas(catalog: str) -> List[str]:
    """Return a list of schema names in a catalog."""
    rows = execute_query(f"SHOW SCHEMAS IN {catalog}")
    names = [_extract_name(row) for row in rows]
    return sorted(name for name in names if name)


def get_tables(catalog: str, schema: str) -> List[str]:
    """Return a list of table names in a schema."""
    rows = execute_query(f"SHOW TABLES IN {catalog}.{schema}")
    names = [_extract_name(row) for row in rows]
    return sorted(name for name in names if name)


def get_table_columns(catalog: str, schema: str, table: str) -> List[str]:
    """Return a list of column names for a table."""
    query = """
        SELECT column_name
        FROM system.information_schema.columns
        WHERE table_catalog = :catalog
          AND table_schema = :schema
          AND table_name = :table
        ORDER BY column_name
    """
    rows = execute_query(query, {"catalog": catalog, "schema": schema, "table": table})
    return sorted(str(row.get("column_name")) for row in rows if row.get("column_name"))


def get_tag_options(catalog: str, schema: str) -> List[Dict[str, str]]:
    """Return distinct tag name/value pairs for a schema."""
    query = """
        SELECT DISTINCT tag_name, tag_value
        FROM system.information_schema.table_tags
        WHERE catalog_name = :catalog
          AND schema_name = :schema
        UNION
        SELECT DISTINCT tag_name, tag_value
        FROM system.information_schema.column_tags
        WHERE catalog_name = :catalog
          AND schema_name = :schema
        ORDER BY tag_name, tag_value
    """
    rows = execute_query(query, {"catalog": catalog, "schema": schema})
    options: List[Dict[str, str]] = []
    for row in rows:
        tag_name = row.get("tag_name")
        tag_value = row.get("tag_value")
        if tag_name is None or tag_value is None:
            continue
        options.append({"tag_name": str(tag_name), "tag_value": str(tag_value)})
    return options


def get_table_tag_coverage(
    catalog: str, schema: str, tag_name: str, tag_value: str
) -> Dict[str, int]:
    """Return counts for table-level tag coverage."""
    total_query = """
        SELECT COUNT(DISTINCT table_name) AS total_tables
        FROM system.information_schema.tables
        WHERE table_catalog = :catalog
          AND table_schema = :schema
    """
    tagged_query = """
        SELECT COUNT(DISTINCT table_name) AS tagged_tables
        FROM system.information_schema.table_tags
        WHERE catalog_name = :catalog
          AND schema_name = :schema
          AND tag_name = :tag_name
          AND tag_value = :tag_value
    """
    params = {"catalog": catalog, "schema": schema, "tag_name": tag_name, "tag_value": tag_value}
    total_rows = execute_query(total_query, {"catalog": catalog, "schema": schema})
    tagged_rows = execute_query(tagged_query, params)
    total = int(total_rows[0]["total_tables"]) if total_rows else 0
    tagged = int(tagged_rows[0]["tagged_tables"]) if tagged_rows else 0
    return {"total": total, "tagged": tagged}


def get_column_tag_coverage(
    catalog: str, schema: str, tag_name: str, tag_value: str
) -> Dict[str, int]:
    """Return counts for column-level tag coverage."""
    total_query = """
        SELECT COUNT(*) AS total_columns
        FROM system.information_schema.columns
        WHERE table_catalog = :catalog
          AND table_schema = :schema
    """
    tagged_query = """
        SELECT COUNT(*) AS tagged_columns
        FROM system.information_schema.column_tags
        WHERE catalog_name = :catalog
          AND schema_name = :schema
          AND tag_name = :tag_name
          AND tag_value = :tag_value
    """
    params = {"catalog": catalog, "schema": schema, "tag_name": tag_name, "tag_value": tag_value}
    total_rows = execute_query(total_query, {"catalog": catalog, "schema": schema})
    tagged_rows = execute_query(tagged_query, params)
    total = int(total_rows[0]["total_columns"]) if total_rows else 0
    tagged = int(tagged_rows[0]["tagged_columns"]) if tagged_rows else 0
    return {"total": total, "tagged": tagged}


def get_table_tags(catalog: str, schema: str, table: str) -> List[Dict[str, object]]:
    """Return tags applied to a table."""
    query = """
        SELECT tag_name, tag_value
        FROM system.information_schema.table_tags
        WHERE catalog_name = :catalog
          AND schema_name = :schema
          AND table_name = :table
        ORDER BY tag_name
    """
    return execute_query(query, {"catalog": catalog, "schema": schema, "table": table})


def get_column_tags(catalog: str, schema: str, table: str) -> List[Dict[str, object]]:
    """Return tags applied to columns in a table."""
    query = """
        SELECT column_name, tag_name, tag_value
        FROM system.information_schema.column_tags
        WHERE catalog_name = :catalog
          AND schema_name = :schema
          AND table_name = :table
        ORDER BY column_name, tag_name
    """
    return execute_query(query, {"catalog": catalog, "schema": schema, "table": table})


def apply_table_tag(catalog: str, schema: str, table: str, tag_name: str, tag_value: str) -> Tuple[bool, str]:
    """Apply a governed tag to a table."""
    if not validate_identifier(tag_name):
        return False, "Tag name contains invalid characters."
    full_name = f"{catalog}.{schema}.{table}"
    query = f"ALTER TABLE {full_name} SET TAGS ('{tag_name}' = '{tag_value}')"
    try:
        execute_update(query)
        log_action(
            action_type="TAG_APPLY",
            object_type="TABLE_TAG",
            object_name=full_name,
            new_value=f"{tag_name}={tag_value}",
            notes=f"Applied by {get_current_user_email()}",
        )
        return True, "Tag applied successfully."
    except Exception as exc:
        return False, f"Error applying tag: {exc}"


def remove_table_tag(catalog: str, schema: str, table: str, tag_name: str) -> Tuple[bool, str]:
    """Remove a governed tag from a table."""
    if not validate_identifier(tag_name):
        return False, "Tag name contains invalid characters."
    full_name = f"{catalog}.{schema}.{table}"
    query = f"ALTER TABLE {full_name} UNSET TAGS ('{tag_name}')"
    try:
        execute_update(query)
        log_action(
            action_type="TAG_REMOVE",
            object_type="TABLE_TAG",
            object_name=full_name,
            new_value=tag_name,
            notes=f"Removed by {get_current_user_email()}",
        )
        return True, "Tag removed successfully."
    except Exception as exc:
        return False, f"Error removing tag: {exc}"


def apply_column_tag(
    catalog: str,
    schema: str,
    table: str,
    column: str,
    tag_name: str,
    tag_value: str,
) -> Tuple[bool, str]:
    """Apply a governed tag to a column."""
    if not validate_identifier(tag_name):
        return False, "Tag name contains invalid characters."
    full_name = f"{catalog}.{schema}.{table}.{column}"
    query = (
        f"ALTER TABLE {catalog}.{schema}.{table} "
        f"ALTER COLUMN {column} SET TAGS ('{tag_name}' = '{tag_value}')"
    )
    try:
        execute_update(query)
        log_action(
            action_type="TAG_APPLY",
            object_type="COLUMN_TAG",
            object_name=full_name,
            new_value=f"{tag_name}={tag_value}",
            notes=f"Applied by {get_current_user_email()}",
        )
        return True, "Tag applied successfully."
    except Exception as exc:
        return False, f"Error applying tag: {exc}"
