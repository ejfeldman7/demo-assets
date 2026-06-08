from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from utils.db_connection import execute_query, execute_update


@dataclass(frozen=True)
class PropagationAction:
    """Represents a column tag propagation action."""

    table_fqn: str
    column_name: str
    tag_name: str
    tag_value: str
    sql: str


def _escape_sql_string(value: str) -> str:
    """Escape a string for SQL literals."""
    return value.replace("'", "''")


def _quote_identifier(value: str) -> str:
    """Quote an identifier using backticks."""
    return f"`{value.replace('`', '``')}`"


def _quote_fqn(parts: Iterable[str]) -> str:
    """Quote a fully qualified name from parts."""
    return ".".join(_quote_identifier(part) for part in parts)


def _split_rls_types(tag_value: str) -> List[str]:
    """Split comma-separated tag values into a list."""
    return [item.strip() for item in tag_value.split(",") if item.strip()]


def get_tagged_catalogs(tag_name: str, target_catalog: Optional[str]) -> List[Dict[str, object]]:
    """Return catalog tags for the given tag name."""
    query = """
        SELECT catalog_name, tag_value
        FROM system.information_schema.catalog_tags
        WHERE tag_name = :tag_name
    """
    params: Dict[str, object] = {"tag_name": tag_name}
    if target_catalog:
        query += " AND catalog_name = :catalog_name"
        params["catalog_name"] = target_catalog
    return execute_query(query, params)


def get_tagged_schemas(
    tag_name: str, target_catalog: Optional[str], target_schema: Optional[str]
) -> List[Dict[str, object]]:
    """Return schema tags for the given tag name."""
    query = """
        SELECT catalog_name, schema_name, tag_value
        FROM system.information_schema.schema_tags
        WHERE tag_name = :tag_name
    """
    params: Dict[str, object] = {"tag_name": tag_name}
    if target_catalog:
        query += " AND catalog_name = :catalog_name"
        params["catalog_name"] = target_catalog
    if target_schema:
        query += " AND schema_name = :schema_name"
        params["schema_name"] = target_schema
    return execute_query(query, params)


def get_tagged_tables(
    tag_name: str, target_catalog: Optional[str], target_schema: Optional[str]
) -> List[Dict[str, object]]:
    """Return table tags for the given tag name."""
    query = """
        SELECT catalog_name, schema_name, table_name, tag_value
        FROM system.information_schema.table_tags
        WHERE tag_name = :tag_name
    """
    params: Dict[str, object] = {"tag_name": tag_name}
    if target_catalog:
        query += " AND catalog_name = :catalog_name"
        params["catalog_name"] = target_catalog
    if target_schema:
        query += " AND schema_name = :schema_name"
        params["schema_name"] = target_schema
    return execute_query(query, params)


def get_tables_in_catalog(catalog: str) -> List[Dict[str, object]]:
    """Return all tables in a catalog."""
    query = """
        SELECT DISTINCT table_catalog, table_schema, table_name
        FROM system.information_schema.tables
        WHERE table_catalog = :catalog
    """
    return execute_query(query, {"catalog": catalog})


def get_tables_in_schema(catalog: str, schema: str) -> List[Dict[str, object]]:
    """Return all tables in a schema."""
    query = """
        SELECT DISTINCT table_catalog, table_schema, table_name
        FROM system.information_schema.tables
        WHERE table_catalog = :catalog
          AND table_schema = :schema
    """
    return execute_query(query, {"catalog": catalog, "schema": schema})


def get_table_columns(catalog: str, schema: str, table: str) -> List[str]:
    """Return column names for a table."""
    query = """
        SELECT column_name
        FROM system.information_schema.columns
        WHERE table_catalog = :catalog
          AND table_schema = :schema
          AND table_name = :table
        ORDER BY column_name
    """
    rows = execute_query(query, {"catalog": catalog, "schema": schema, "table": table})
    return [str(row["column_name"]) for row in rows if row.get("column_name")]


def build_propagation_plan(
    parent_tag_name: str,
    required_parent_tag: str,
    column_name: str,
    column_tag_name: str,
    column_tag_value: str,
    target_catalog: Optional[str],
    target_schema: Optional[str],
) -> List[PropagationAction]:
    """Build a list of column tag actions based on parent tags."""
    tables_to_process: Dict[str, set[str]] = {}
    for row in get_tagged_catalogs(parent_tag_name, target_catalog):
        catalog = str(row["catalog_name"])
        for table_row in get_tables_in_catalog(catalog):
            table_key = f"{table_row['table_catalog']}.{table_row['table_schema']}.{table_row['table_name']}"
            tables_to_process.setdefault(table_key, set()).update(
                _split_rls_types(str(row.get("tag_value", "")))
            )
    for row in get_tagged_schemas(parent_tag_name, target_catalog, target_schema):
        catalog = str(row["catalog_name"])
        schema = str(row["schema_name"])
        for table_row in get_tables_in_schema(catalog, schema):
            table_key = f"{table_row['table_catalog']}.{table_row['table_schema']}.{table_row['table_name']}"
            tables_to_process.setdefault(table_key, set()).update(
                _split_rls_types(str(row.get("tag_value", "")))
            )
    for row in get_tagged_tables(parent_tag_name, target_catalog, target_schema):
        table_key = f"{row['catalog_name']}.{row['schema_name']}.{row['table_name']}"
        tables_to_process.setdefault(table_key, set()).update(
            _split_rls_types(str(row.get("tag_value", "")))
        )

    actions: List[PropagationAction] = []
    for table_fqn, rls_types in tables_to_process.items():
        if required_parent_tag not in rls_types:
            continue
        catalog, schema, table = table_fqn.split(".", maxsplit=2)
        columns = get_table_columns(catalog, schema, table)
        if column_name not in columns:
            continue
        sql = (
            f"ALTER TABLE {_quote_fqn([catalog, schema, table])} "
            f"ALTER COLUMN {_quote_identifier(column_name)} "
            f"SET TAGS ('{_escape_sql_string(column_tag_name)}' = "
            f"'{_escape_sql_string(column_tag_value)}')"
        )
        actions.append(
            PropagationAction(
                table_fqn=table_fqn,
                column_name=column_name,
                tag_name=column_tag_name,
                tag_value=column_tag_value,
                sql=sql,
            )
        )
    return actions


def apply_propagation(actions: Iterable[PropagationAction]) -> List[str]:
    """Execute column tag propagation actions and return executed SQL."""
    executed: List[str] = []
    for action in actions:
        execute_update(action.sql)
        executed.append(action.sql)
    return executed
