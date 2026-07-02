"""
Build the ERD graph (nodes + edges) for the configured catalog allow-list
(`config.get_catalogs()`) by querying `system.information_schema` via a SQL warehouse.

system.information_schema aggregates PK/FK/table/column metadata across every catalog
in the metastore in one query (privilege-filtered per caller, same as the per-catalog
views -- verified empirically, no special enablement needed). When ERD_CATALOGS is set,
we filter every query to `table_catalog IN (<configured catalogs>)` so a deployment only
ever sees the catalogs it was scoped to. When ERD_CATALOGS is unset (get_catalogs()
returns None), this is deliberate "unscoped" mode: no catalog filter is applied at all,
and the graph shows every catalog the app's own credentials can browse (still bounded by
Unity Catalog's own privilege filtering -- "unscoped" means "whatever this deployment's
grants allow," not literally every catalog that exists). Per user decision, Genie Space
setup mirrors this: unscoped ERD_CATALOGS means an unscoped Genie Space too (see
setup/create_scoped_views.py) -- this is a deliberate, documented choice, not a gap.

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

from .config import get_catalogs, get_metadata_location, get_warehouse_id, get_workspace_client

# In-memory cache: {(frozenset(catalogs), frozenset(pairs)): (timestamp, payload)}
_CACHE: Dict[Tuple[frozenset, frozenset], Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def validate_pairs(pairs: List[Tuple[str, str]], allowed_catalogs: Optional[List[str]]) -> List[Tuple[str, str]]:
    """Validate requested (catalog, schema) pairs -- from the frontend's catalog/schema
    tree picker -- raising ValueError naming the bad one(s) rather than silently dropping
    them (a caller who mistypes/requests something invalid should get a clear 400, not a
    graph silently widened back to everything in scope). Also enforces that every
    requested catalog is within the configured ERD_CATALOGS allow-list when one is set
    (allowed_catalogs=None means unscoped -- any catalog name is fine, still subject to
    the identifier check and to whatever UC privileges actually apply)."""
    bad_format = [f"{c}.{s}" for c, s in pairs if not (_IDENTIFIER_RE.match(c) and _IDENTIFIER_RE.match(s))]
    if bad_format:
        raise ValueError(f"Invalid catalog.schema pair(s): {bad_format}")
    if allowed_catalogs is not None:
        allowed = set(allowed_catalogs)
        out_of_scope = sorted({c for c, _ in pairs if c not in allowed})
        if out_of_scope:
            raise ValueError(f"Catalog(s) not in this deployment's allow-list: {out_of_scope}")
    return pairs


def _pair_in_clause(catalog_col: str, schema_col: str, pairs: List[Tuple[str, str]]) -> str:
    """Build a safe SQL tuple-IN clause matching exact (catalog, schema) combinations --
    the correct model for a catalog/schema tree picker, where the same schema name can be
    selected under one catalog and not another (a flat schema-name filter can't express
    that)."""
    safe = [(c, s) for c, s in pairs if _IDENTIFIER_RE.match(c) and _IDENTIFIER_RE.match(s)]
    tuples = ", ".join(f"('{c}', '{s}')" for c, s in safe)
    return f"({catalog_col}, {schema_col}) IN ({tuples})" if tuples else "1=0"


def _internal_schema_exclusion_sql(catalog_col: str, schema_col: str) -> str:
    """SQL condition excluding UC's own metadata schema plus THIS deployment's actual
    configured Genie metadata catalog+schema (via get_metadata_location(), which reads
    ERD_METADATA_LOCATION) -- never a hardcoded schema name. Scoped by catalog+schema
    together (not schema name alone), so an unrelated catalog that happens to also have a
    schema literally named "erd_meta" isn't wrongly excluded.
    """
    meta_catalog, meta_schema = get_metadata_location()
    if not (_IDENTIFIER_RE.match(meta_catalog) and _IDENTIFIER_RE.match(meta_schema)):
        # Defensive: an invalid configured name can't be excluded via string interpolation
        # into SQL; information_schema exclusion below still applies either way.
        meta_catalog, meta_schema = "", ""
    return (
        f"{schema_col} != 'information_schema' "
        f"AND NOT ({catalog_col} = '{meta_catalog}' AND {schema_col} = '{meta_schema}') "
        # Databricks-internal plumbing catalogs (e.g. __databricks_internal_catalog_...)
        # -- only surfaces in unscoped mode, since a scoped ERD_CATALOGS would never
        # deliberately name one of these, but worth excluding unconditionally either way.
        f"AND substring({catalog_col}, 1, 2) != '__'"
    )


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


def _query_columns(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]]) -> List[List[Any]]:
    """All columns for the in-scope catalogs (optionally narrowed to exact catalog.schema
    pairs). catalogs=None means unscoped -- no catalog filter at all."""
    catalog_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('table_catalog', 'table_schema', pairs)}" if pairs else ""
    stmt = f"""
    SELECT table_catalog, table_schema, table_name, column_name, full_data_type, ordinal_position
    FROM system.information_schema.columns
    WHERE {_internal_schema_exclusion_sql("table_catalog", "table_schema")}
      {catalog_filter}
      {pair_filter}
    ORDER BY table_catalog, table_schema, table_name, ordinal_position
    """
    return _rows(_execute(stmt))


def _query_tables(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]]) -> List[List[Any]]:
    """Every table (incl. those with no FK) so isolated nodes still render.
    catalogs=None means unscoped -- no catalog filter at all."""
    catalog_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('table_catalog', 'table_schema', pairs)}" if pairs else ""
    stmt = f"""
    SELECT table_catalog, table_schema, table_name
    FROM system.information_schema.tables
    WHERE {_internal_schema_exclusion_sql("table_catalog", "table_schema")}
      {catalog_filter}
      {pair_filter}
    ORDER BY table_catalog, table_schema, table_name
    """
    return _rows(_execute(stmt))


def _query_primary_keys(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]]) -> List[List[Any]]:
    """(catalog, schema, table, column) tuples that participate in a PRIMARY KEY.
    catalogs=None means unscoped -- no catalog filter at all."""
    catalog_filter = f"AND tc.constraint_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('kcu.table_catalog', 'kcu.table_schema', pairs)}" if pairs else ""
    stmt = f"""
    SELECT kcu.table_catalog, kcu.table_schema, kcu.table_name, kcu.column_name
    FROM system.information_schema.table_constraints tc
    JOIN system.information_schema.key_column_usage kcu
      ON tc.constraint_catalog = kcu.constraint_catalog
     AND tc.constraint_schema  = kcu.constraint_schema
     AND tc.constraint_name    = kcu.constraint_name
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND {_internal_schema_exclusion_sql("kcu.table_catalog", "kcu.table_schema")}
      {catalog_filter}
      {pair_filter}
    """
    return _rows(_execute(stmt))


def _query_foreign_keys(catalogs: Optional[List[str]]) -> List[List[Any]]:
    """
    FK -> PK relationships sourced from system.information_schema (aggregates all
    catalogs in one query; privilege-filtered per caller). We scope to the configured
    catalog allow-list (or apply no filter at all when catalogs=None, i.e. unscoped mode)
    and then filter edge endpoints to the requested pairs/catalogs in Python (via the
    `present` node-id set in build_graph) so cross-schema AND cross-catalog edges are
    handled correctly, while anything pointing outside the allow-list is dropped rather
    than leaking that catalog's existence.
    """
    catalog_filter = f"AND ref.constraint_catalog IN {_in_clause(catalogs)}" if catalogs else ""
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
    WHERE 1=1
      {catalog_filter}
    ORDER BY ref.constraint_name, fkc.ordinal_position
    """
    return _rows(_execute(stmt))


# --- catalog/schema tree (lightweight, for the frontend picker) -------------


def list_catalog_schemas() -> List[Dict[str, Any]]:
    """Enumerate catalog -> [schema, ...] for the frontend's catalog/schema tree picker,
    without fetching the full graph. Respects the same scope as build_graph (the
    ERD_CATALOGS allow-list, or every catalog visible if unset)."""
    catalogs = get_catalogs()
    catalog_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    stmt = f"""
    SELECT DISTINCT table_catalog, table_schema
    FROM system.information_schema.tables
    WHERE {_internal_schema_exclusion_sql("table_catalog", "table_schema")}
      {catalog_filter}
    ORDER BY table_catalog, table_schema
    """
    rows = _rows(_execute(stmt))
    tree: Dict[str, List[str]] = {}
    for catalog, schema in rows:
        tree.setdefault(catalog, []).append(schema)
    return [{"catalog": c, "schemas": sorted(s)} for c, s in sorted(tree.items())]


# --- graph assembly ---------------------------------------------------------


def _node_id(catalog: str, schema: str, table: str) -> str:
    return f"{catalog}.{schema}.{table}"


def build_graph(pairs: Optional[List[Tuple[str, str]]] = None) -> Dict[str, Any]:
    """Build (or return cached) {nodes, edges} for the configured catalog allow-list
    (or every catalog visible to this deployment, if ERD_CATALOGS is unset -- see module
    docstring), optionally further narrowed to exact (catalog, schema) pairs -- the model
    the frontend's catalog/schema tree picker uses (a flat schema-name filter can't
    express "schema X under catalog A but not under catalog B")."""
    catalogs = get_catalogs()  # None means unscoped
    if pairs:
        validate_pairs(pairs, catalogs)  # raises ValueError naming the bad entry, rather
        # than silently dropping it and widening the result back to everything in scope.
    key = (frozenset(catalogs or ()), frozenset(pairs or ()))

    cached = _CACHE.get(key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    tables = _query_tables(catalogs, pairs)
    columns = _query_columns(catalogs, pairs)
    pks = _query_primary_keys(catalogs, pairs)
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
        # Actual catalogs present in this result, not just the configured allow-list --
        # correct in both scoped mode (subset of ERD_CATALOGS) and unscoped mode
        # (catalogs=None), and always what the frontend needs to render its picker.
        "catalogs": sorted({n["catalog"] for n in nodes}),
        "unscoped": catalogs is None,
        "pairs": [f"{c}.{s}" for c, s in pairs] if pairs else None,
        "nodes": nodes,
        "edges": edges,
    }
    _CACHE[key] = (time.time(), payload)
    return payload
