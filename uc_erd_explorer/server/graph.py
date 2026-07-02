"""
Build the ERD graph (nodes + edges) for the configured catalog allow-list
(`config.get_catalogs()`, defaults to just `megacorp`) by querying
`system.information_schema` via a SQL warehouse.

system.information_schema aggregates PK/FK/table/column metadata across every catalog
in the metastore in one query (privilege-filtered per caller, same as the per-catalog
views -- verified empirically, no special enablement needed). We always filter every
query to `table_catalog IN (<configured catalogs>)` so a deployment only ever sees the
catalogs it was scoped to, regardless of what else the app's service principal can browse.

Nodes  = tables (with their columns; each column flagged is_pk / is_fk).
Edges  = FOREIGN KEY -> PRIMARY KEY relationships (direction: FK table -> PK table).
An edge only renders if BOTH endpoints are in-scope, so a FK pointing at a catalog
outside the allow-list is silently dropped rather than leaking that catalog's existence.

Results are cached in-memory for ~5 minutes (schema metadata changes rarely).
"""
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from databricks.sdk.service import sql

from .config import get_catalogs, get_warehouse_id, get_workspace_client

# In-memory cache: {(frozenset(catalogs), frozenset(schemas)): (timestamp, payload)}
_CACHE: Dict[Tuple[frozenset, frozenset], Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Internal schemas always excluded from the ERD graph, so neither UC's own metadata
# views nor the app's own Genie-plumbing views ever show up as fake table nodes:
#   - information_schema: UC's ~30 metadata views, present in every catalog.
#   - erd_meta: the hard-scoped views this app creates for the Genie Space
#     (setup/create_scoped_views.py) -- plumbing, not business tables.
# NOT hardcoded to any business schema names (e.g. "factory"/"erp") since ERD_CATALOGS
# can point at catalogs with arbitrary schema names -- see config.get_catalogs().
_EXCLUDED_SCHEMAS = ("information_schema", "erd_meta")
_EXCLUDED_SCHEMAS_SQL = "(" + ", ".join(f"'{s}'" for s in _EXCLUDED_SCHEMAS) + ")"


# --- SQL helpers ------------------------------------------------------------


def _execute(statement: str, timeout: str = "50s") -> sql.StatementResponse:
    client = get_workspace_client()
    warehouse_id = get_warehouse_id()
    if not warehouse_id:
        raise RuntimeError("No SQL warehouse available")
    return client.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        wait_timeout=timeout,
    )


def _rows(result: sql.StatementResponse) -> List[List[Any]]:
    if not result.result or not result.result.data_array:
        return []
    return result.result.data_array


def _in_clause(values: List[str]) -> str:
    """Build a safe SQL IN (...) clause, dropping anything that isn't a plain identifier."""
    safe = [v for v in values if _IDENTIFIER_RE.match(v)]
    quoted = ", ".join(f"'{v}'" for v in safe)
    return f"({quoted})" if quoted else "('')"


# --- queries ----------------------------------------------------------------


def _query_columns(catalogs: List[str], schemas: Optional[List[str]]) -> List[List[Any]]:
    """All columns for the in-scope catalogs (optionally filtered to given schemas)."""
    catalog_clause = _in_clause(catalogs)
    schema_filter = f"AND table_schema IN {_in_clause(schemas)}" if schemas else ""
    stmt = f"""
    SELECT table_catalog, table_schema, table_name, column_name, full_data_type, ordinal_position
    FROM system.information_schema.columns
    WHERE table_catalog IN {catalog_clause}
      AND table_schema NOT IN {_EXCLUDED_SCHEMAS_SQL}
      {schema_filter}
    ORDER BY table_catalog, table_schema, table_name, ordinal_position
    """
    return _rows(_execute(stmt))


def _query_tables(catalogs: List[str], schemas: Optional[List[str]]) -> List[List[Any]]:
    """Every table (incl. those with no FK) so isolated nodes still render."""
    catalog_clause = _in_clause(catalogs)
    schema_filter = f"AND table_schema IN {_in_clause(schemas)}" if schemas else ""
    stmt = f"""
    SELECT table_catalog, table_schema, table_name
    FROM system.information_schema.tables
    WHERE table_catalog IN {catalog_clause}
      AND table_schema NOT IN {_EXCLUDED_SCHEMAS_SQL}
      {schema_filter}
    ORDER BY table_catalog, table_schema, table_name
    """
    return _rows(_execute(stmt))


def _query_primary_keys(catalogs: List[str], schemas: Optional[List[str]]) -> List[List[Any]]:
    """(catalog, schema, table, column) tuples that participate in a PRIMARY KEY."""
    catalog_clause = _in_clause(catalogs)
    schema_filter = f"AND kcu.table_schema IN {_in_clause(schemas)}" if schemas else ""
    stmt = f"""
    SELECT kcu.table_catalog, kcu.table_schema, kcu.table_name, kcu.column_name
    FROM system.information_schema.table_constraints tc
    JOIN system.information_schema.key_column_usage kcu
      ON tc.constraint_catalog = kcu.constraint_catalog
     AND tc.constraint_schema  = kcu.constraint_schema
     AND tc.constraint_name    = kcu.constraint_name
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.constraint_catalog IN {catalog_clause}
      AND kcu.table_schema NOT IN {_EXCLUDED_SCHEMAS_SQL}
      {schema_filter}
    """
    return _rows(_execute(stmt))


def _query_foreign_keys(catalogs: List[str]) -> List[List[Any]]:
    """
    FK -> PK relationships sourced from system.information_schema (aggregates all
    catalogs in one query; privilege-filtered per caller). We scope to the configured
    catalog allow-list and then filter edge endpoints to the requested schemas/catalogs
    in Python (via the `present` node-id set in build_graph) so cross-schema AND
    cross-catalog edges are handled correctly, while anything pointing outside the
    allow-list is dropped rather than leaking that catalog's existence.
    """
    catalog_clause = _in_clause(catalogs)
    stmt = f"""
    SELECT fk.table_catalog fk_catalog, fk.table_schema fk_schema, fk.table_name fk_table,
           fkc.column_name fk_column, fkc.ordinal_position,
           pk.table_catalog pk_catalog, pk.table_schema pk_schema, pk.table_name pk_table,
           pkc.column_name pk_column, ref.constraint_name
    FROM system.information_schema.referential_constraints ref
    JOIN system.information_schema.table_constraints fk
      ON ref.constraint_catalog=fk.constraint_catalog AND ref.constraint_schema=fk.constraint_schema
     AND ref.constraint_name=fk.constraint_name AND fk.constraint_type='FOREIGN KEY'
    JOIN system.information_schema.key_column_usage fkc
      ON fk.constraint_catalog=fkc.constraint_catalog AND fk.constraint_schema=fkc.constraint_schema
     AND fk.constraint_name=fkc.constraint_name
    JOIN system.information_schema.table_constraints pk
      ON ref.unique_constraint_catalog=pk.constraint_catalog AND ref.unique_constraint_schema=pk.constraint_schema
     AND ref.unique_constraint_name=pk.constraint_name AND pk.constraint_type='PRIMARY KEY'
    JOIN system.information_schema.key_column_usage pkc
      ON pk.constraint_catalog=pkc.constraint_catalog AND pk.constraint_schema=pkc.constraint_schema
     AND pk.constraint_name=pkc.constraint_name AND fkc.position_in_unique_constraint=pkc.ordinal_position
    WHERE ref.constraint_catalog IN {catalog_clause}
    ORDER BY ref.constraint_name, fkc.ordinal_position
    """
    return _rows(_execute(stmt))


# --- graph assembly ---------------------------------------------------------


def _node_id(catalog: str, schema: str, table: str) -> str:
    return f"{catalog}.{schema}.{table}"


def build_graph(schemas: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build (or return cached) {nodes, edges} for the configured catalog allow-list,
    optionally further filtered to specific schemas."""
    catalogs = get_catalogs()
    schemas = [s for s in schemas if _IDENTIFIER_RE.match(s)] if schemas else None
    key = (frozenset(catalogs), frozenset(schemas or ()))

    cached = _CACHE.get(key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    tables = _query_tables(catalogs, schemas)
    columns = _query_columns(catalogs, schemas)
    pks = _query_primary_keys(catalogs, schemas)
    fks = _query_foreign_keys(catalogs)

    # Set of table node-ids present in this view (drives edge filtering).
    present: set = {_node_id(c, s, t) for (c, s, t) in tables}

    # PK column lookup: (catalog, schema, table) -> {column, ...}
    pk_cols: Dict[Tuple[str, str, str], set] = {}
    for (catalog, schema, table, column) in pks:
        pk_cols.setdefault((catalog, schema, table), set()).add(column)

    # FK column lookup: (catalog, schema, table) -> {column, ...} (from filtered edges below).
    fk_cols: Dict[Tuple[str, str, str], set] = {}

    # --- edges ---
    # Group multi-column FKs by constraint_name into a single edge.
    edge_acc: Dict[str, Dict[str, Any]] = {}
    for (fk_catalog, fk_schema, fk_table, fk_column, _ord,
         pk_catalog, pk_schema, pk_table, pk_column, constraint_name) in fks:
        source = _node_id(fk_catalog, fk_schema, fk_table)
        target = _node_id(pk_catalog, pk_schema, pk_table)
        # Only keep edges where BOTH endpoints are in the current (allow-listed) view --
        # this is what prevents a FK into an out-of-scope catalog from leaking anything.
        if source not in present or target not in present:
            continue
        fk_cols.setdefault((fk_catalog, fk_schema, fk_table), set()).add(fk_column)
        acc = edge_acc.setdefault(
            constraint_name,
            {
                "id": constraint_name,
                "source": source,
                "target": target,
                "fk_columns": [],
                "pk_columns": [],
                "constraint_name": constraint_name,
            },
        )
        acc["fk_columns"].append(fk_column)
        acc["pk_columns"].append(pk_column)
    edges = list(edge_acc.values())

    # --- nodes ---
    # Group columns by (catalog, schema, table) preserving ordinal order.
    cols_by_table: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for (catalog, schema, table, column_name, full_type, _ord) in columns:
        is_pk = column_name in pk_cols.get((catalog, schema, table), set())
        is_fk = column_name in fk_cols.get((catalog, schema, table), set())
        cols_by_table.setdefault((catalog, schema, table), []).append(
            {
                "name": column_name,
                "type": full_type,
                "is_pk": is_pk,
                "is_fk": is_fk,
            }
        )

    nodes = []
    for (catalog, schema, table) in tables:
        nodes.append(
            {
                "id": _node_id(catalog, schema, table),
                "catalog": catalog,
                "schema": schema,
                "table": table,
                "columns": cols_by_table.get((catalog, schema, table), []),
            }
        )
    # Stable ordering.
    nodes.sort(key=lambda n: (n["catalog"], n["schema"], n["table"]))

    payload = {
        "catalogs": catalogs,
        "schemas": schemas,
        "nodes": nodes,
        "edges": edges,
    }
    _CACHE[key] = (time.time(), payload)
    return payload
