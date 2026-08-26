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

Nodes  = tables (with their columns; each column flagged is_pk / is_fk). Tables and
         columns also carry their UC `comment` (nullable) and `tags` (from
         table_tags/column_tags, e.g. PII classification) -- both are surfaced as-is,
         empty/None when a deployment's catalog has none set.
Edges  = FOREIGN KEY -> PRIMARY KEY relationships (direction: FK table -> PK table).
An edge only renders if BOTH endpoints are in-scope, so a FK pointing at a catalog
outside the allow-list is silently dropped rather than leaking that catalog's existence.

Metadata can be read two ways (ERD_METADATA_SOURCE, see config.get_metadata_source):
live from system.information_schema (default), or from the pre-materialized erd_snapshot_*
Delta tables (built weekly by setup/build_erd_snapshot.py) so the expensive joins never run
on the request path -- _resolve_source() picks snapshot when enabled and in prod, and
build_graph falls back to live if the first snapshot read errors (tables not built yet).
Either way, results are cached in-memory (TTL
configurable via ERD_CACHE_TTL_SECONDS, default 3600s -- schema metadata changes rarely).
Catalogs above ERD_SCHEMA_COLLAPSE_THRESHOLD tables
(default 80) default to a collapsed, one-node-per-schema view instead of full
table-level detail -- see build_schema_summary() -- since a flat table-per-node layout
gets slow to query and unreadable well before a few hundred nodes. Selecting a specific
schema (via `pairs`) always returns full detail for it regardless of the threshold --
this is also how the frontend "expands" a collapsed schema node on click, reusing the
same pairs-based mechanism as the catalog/schema tree picker rather than a second one.
"""
import contextvars
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from databricks.sdk.service import sql

from .config import (
    get_cache_ttl_seconds,
    get_catalogs,
    get_metadata_location,
    get_metadata_source,
    get_query_client,
    get_schema_collapse_threshold,
    get_test_catalog_suffix,
    get_user_cache_key,
    get_warehouse_id,
)

logger = logging.getLogger("erd")

# Metadata read source (see config.get_metadata_source): "information_schema" queries the
# system tables live; "snapshot" reads the pre-materialized erd_snapshot_* Delta tables.
_SNAPSHOT = "snapshot"

# In-memory cache: {(user_key, frozenset(catalogs), frozenset(pairs)): (timestamp, payload)}
# user_key is "" in service-principal mode (shared cache) and the per-user identity in
# on-behalf-of-user mode (so privilege-filtered results are never shared across users).
_CACHE: Dict[Tuple[str, frozenset, frozenset], Tuple[float, Dict[str, Any]]] = {}

# Shared, BOUNDED threadpool for the per-load metadata queries. build_graph fans out ~5
# independent information_schema queries per load; running them concurrently instead of
# sequentially is the single biggest win on the live-query path. Bounding it (a fixed,
# module-level pool, not an unbounded per-request one) also caps how many warehouse
# statements all concurrent app users can have in flight at once -- natural backpressure.
#
# OBO note: get_query_client() reads the logged-in user's token from a ContextVar (see
# config.py), which does NOT auto-propagate into pool threads. build_graph therefore
# submits each query wrapped in contextvars.copy_context().run(...) so the per-user
# identity is carried into the worker thread -- see _submit_query().
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="erd-query")


def _submit_query(fn, *args):
    """Submit a metadata query to the shared pool, carrying the CURRENT context (the
    per-request OBO user token/identity captured in ContextVars) into the worker thread.
    A bare _EXECUTOR.submit would run in a fresh context where get_query_client() sees no
    token and, in on-behalf-of-user mode, raises -- copying the context is what threads
    the identity through explicitly."""
    ctx = contextvars.copy_context()
    return _EXECUTOR.submit(ctx.run, fn, *args)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _resolve_catalogs(catalogs: Optional[List[str]], env: str) -> Optional[List[str]]:
    """Translate the configured (prod) catalog allow-list to its test-environment
    equivalent when env == "test", by appending get_test_catalog_suffix() to each entry
    (e.g. edp_customer -> edp_customer_ts) -- these are two distinct real Unity Catalog
    catalogs, not an alias, so every downstream query needs the suffixed name to hit the
    right one. Unscoped deployments (catalogs=None) have no defined catalog list to
    suffix, so this is a no-op there -- routes expose config.get_catalogs() is not None
    as `test_available` so the frontend can disable the toggle rather than relying on
    this function to reject it silently."""
    if env != "test" or not catalogs:
        return catalogs
    suffix = get_test_catalog_suffix()
    return [f"{c}{suffix}" for c in catalogs]


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


def _execute(statement: str, label: str = "query", timeout: str = "50s") -> sql.StatementResponse:
    # get_query_client() is the SP in service-principal mode and the logged-in user's
    # client in on-behalf-of-user mode -- so information_schema privilege filtering
    # follows whichever identity this deployment is configured to query as.
    client = get_query_client()
    warehouse_id = get_warehouse_id()
    if not warehouse_id:
        raise RuntimeError("No SQL warehouse available")
    started = time.perf_counter()
    result = client.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        wait_timeout=timeout,
        # CANCEL, not the SDK default CONTINUE: if the statement doesn't finish within
        # `timeout`, cancel it so the state check below raises. With CONTINUE the SDK
        # returns a still-pending response carrying no data, which _rows() would read as
        # an empty result -- silently rendering a partial/empty graph instead of failing.
        on_wait_timeout=sql.ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    state = result.status.state if result.status else None
    row_count = len(result.result.data_array) if result.result and result.result.data_array else 0
    logger.info("query %-13s %6.0fms state=%s rows=%d", label, elapsed_ms, state, row_count)
    if state != sql.StatementState.SUCCEEDED:
        err = result.status.error.message if (result.status and result.status.error) else None
        raise RuntimeError(
            f"Query '{label}' did not succeed (state={state}): {err or 'no result returned'}"
        )
    return result


def _rows(result: sql.StatementResponse) -> List[List[Any]]:
    if not result.result or not result.result.data_array:
        return []
    return result.result.data_array


def _in_clause(values: List[str]) -> str:
    """Build a safe SQL IN (...) clause, dropping anything that isn't a plain identifier."""
    safe = [v for v in values if _IDENTIFIER_RE.match(v)]
    quoted = ", ".join(f"'{v}'" for v in safe)
    return f"({quoted})" if quoted else "('')"


def _snapshot_loc() -> str:
    """`catalog.schema` where the erd_snapshot_* tables live (the ERD metadata location),
    validated for safe interpolation into the snapshot read queries."""
    meta_catalog, meta_schema = get_metadata_location()
    if not (_IDENTIFIER_RE.match(meta_catalog) and _IDENTIFIER_RE.match(meta_schema)):
        raise RuntimeError(f"Invalid ERD metadata location for snapshot reads: {meta_catalog}.{meta_schema}")
    return f"{meta_catalog}.{meta_schema}"


def get_snapshot_freshness() -> Optional[Dict[str, Any]]:
    """Read the snapshot's freshness marker (erd_snapshot_meta) -> {refreshed_at,
    catalogs}, or None if the snapshot isn't present/readable. Used by the admin panel to
    show when the materialized metadata was last rebuilt. Tolerant: never raises."""
    try:
        loc = _snapshot_loc()
        rows = _rows(_execute(
            f"SELECT CAST(refreshed_at AS STRING), catalogs FROM {loc}.erd_snapshot_meta LIMIT 1",
            "snapshot_meta", "30s",
        ))
        if not rows:
            return None
        return {"refreshed_at": rows[0][0], "catalogs": rows[0][1]}
    except Exception:  # noqa: BLE001
        return None


def _resolve_source(env: str = "prod") -> str:
    """The CONFIGURED metadata source for this build (intent only -- no warehouse call):
    "snapshot" when ERD_METADATA_SOURCE asks for it AND env is prod, else live.

    Test always goes live: the snapshot only materializes the configured (prod) catalogs,
    while the Prod/Test toggle queries the _ts-suffixed catalogs, which aren't in it.

    This deliberately does NOT probe whether the snapshot exists -- that cost a warehouse
    round-trip on every cold build. Instead build_graph attempts the first snapshot read
    and falls back to live if the tables aren't there yet (fresh deploy before the first
    refresh job), so the graceful fallback is preserved without the extra query."""
    if env != "prod" or get_metadata_source() != _SNAPSHOT:
        return "information_schema"
    return _SNAPSHOT


# --- queries ----------------------------------------------------------------


def _query_columns(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]],
                   source: str = "information_schema") -> List[List[Any]]:
    """All columns for the in-scope catalogs (optionally narrowed to exact catalog.schema
    pairs). catalogs=None means unscoped -- no catalog filter at all. In snapshot mode,
    reads the pre-materialized erd_snapshot_columns table (same columns, already scoped/
    excluded at snapshot time), so no live information_schema hit."""
    catalog_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('table_catalog', 'table_schema', pairs)}" if pairs else ""
    cols = "table_catalog, table_schema, table_name, column_name, full_data_type, ordinal_position, comment"
    order = "ORDER BY table_catalog, table_schema, table_name, ordinal_position"
    if source == _SNAPSHOT:
        stmt = f"SELECT {cols} FROM {_snapshot_loc()}.erd_snapshot_columns WHERE 1=1 {catalog_filter} {pair_filter} {order}"
    else:
        stmt = f"""
        SELECT {cols}
        FROM system.information_schema.columns
        WHERE {_internal_schema_exclusion_sql("table_catalog", "table_schema")}
          {catalog_filter} {pair_filter} {order}"""
    return _rows(_execute(stmt, "columns"))


def _query_tables(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]],
                  source: str = "information_schema") -> List[List[Any]]:
    """Every table (incl. those with no FK) so isolated nodes still render.
    catalogs=None means unscoped -- no catalog filter at all. Snapshot mode reads
    erd_snapshot_tables."""
    catalog_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('table_catalog', 'table_schema', pairs)}" if pairs else ""
    cols = "table_catalog, table_schema, table_name, comment"
    order = "ORDER BY table_catalog, table_schema, table_name"
    if source == _SNAPSHOT:
        stmt = f"SELECT {cols} FROM {_snapshot_loc()}.erd_snapshot_tables WHERE 1=1 {catalog_filter} {pair_filter} {order}"
    else:
        stmt = f"""
        SELECT {cols}
        FROM system.information_schema.tables
        WHERE {_internal_schema_exclusion_sql("table_catalog", "table_schema")}
          {catalog_filter} {pair_filter} {order}"""
    return _rows(_execute(stmt, "tables"))


def _query_table_tags(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]],
                      source: str = "information_schema") -> List[List[Any]]:
    """(catalog, schema, table, tag_name, tag_value) rows from Unity Catalog's tag
    governance feature (e.g. PII classification). Tolerant of the system table (or the
    snapshot table) being unavailable -- degrades to "no tags" rather than breaking the
    whole graph. Snapshot mode reads erd_snapshot_table_tags."""
    catalog_filter = f"AND catalog_name IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('catalog_name', 'schema_name', pairs)}" if pairs else ""
    cols = "catalog_name, schema_name, table_name, tag_name, tag_value"
    if source == _SNAPSHOT:
        stmt = f"SELECT {cols} FROM {_snapshot_loc()}.erd_snapshot_table_tags WHERE 1=1 {catalog_filter} {pair_filter}"
    else:
        stmt = f"""
        SELECT {cols}
        FROM system.information_schema.table_tags
        WHERE {_internal_schema_exclusion_sql("catalog_name", "schema_name")}
          {catalog_filter} {pair_filter}"""
    try:
        return _rows(_execute(stmt, "table_tags"))
    except Exception:  # noqa: BLE001
        return []


def _query_column_tags(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]],
                       source: str = "information_schema") -> List[List[Any]]:
    """(catalog, schema, table, column, tag_name, tag_value) rows -- see _query_table_tags.
    Snapshot mode reads erd_snapshot_column_tags."""
    catalog_filter = f"AND catalog_name IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('catalog_name', 'schema_name', pairs)}" if pairs else ""
    cols = "catalog_name, schema_name, table_name, column_name, tag_name, tag_value"
    if source == _SNAPSHOT:
        stmt = f"SELECT {cols} FROM {_snapshot_loc()}.erd_snapshot_column_tags WHERE 1=1 {catalog_filter} {pair_filter}"
    else:
        stmt = f"""
        SELECT {cols}
        FROM system.information_schema.column_tags
        WHERE {_internal_schema_exclusion_sql("catalog_name", "schema_name")}
          {catalog_filter} {pair_filter}"""
    try:
        return _rows(_execute(stmt, "column_tags"))
    except Exception:  # noqa: BLE001
        return []


def _query_primary_keys(catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]],
                        source: str = "information_schema") -> List[List[Any]]:
    """(catalog, schema, table, column) tuples that participate in a PRIMARY KEY.
    catalogs=None means unscoped -- no catalog filter at all. Snapshot mode reads the
    pre-materialized erd_snapshot_primary_keys (the tc/kcu join done at snapshot time)."""
    if source == _SNAPSHOT:
        catalog_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
        pair_filter = f"AND {_pair_in_clause('table_catalog', 'table_schema', pairs)}" if pairs else ""
        stmt = (
            f"SELECT table_catalog, table_schema, table_name, column_name "
            f"FROM {_snapshot_loc()}.erd_snapshot_primary_keys WHERE 1=1 {catalog_filter} {pair_filter}"
        )
        return _rows(_execute(stmt, "primary_keys"))
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
    return _rows(_execute(stmt, "primary_keys"))


def _query_foreign_keys(
    catalogs: Optional[List[str]], pairs: Optional[List[Tuple[str, str]]] = None,
    source: str = "information_schema",
) -> List[List[Any]]:
    """
    FK -> PK relationships sourced from system.information_schema (aggregates all
    catalogs in one query; privilege-filtered per caller). We scope to the configured
    catalog allow-list (or apply no filter at all when catalogs=None, i.e. unscoped mode).
    When `pairs` is given we ALSO push a filter on the FK (source) table's
    (catalog, schema) into the query, rather than fetching every FK for the whole catalog
    and discarding most of them in Python: an edge only survives when BOTH endpoints are
    in `present` (the tables in the selected pairs), so restricting the FK side to those
    same pairs drops nothing we would have kept while cutting the scanned join
    dramatically. Endpoints are still re-checked against `present` in build_graph, so
    cross-schema/cross-catalog edges stay correct and anything pointing outside the
    allow-list is dropped rather than leaking that catalog's existence.
    """
    if source == _SNAPSHOT:
        # The materialized edge list already has the join done; filter its own fk_* columns.
        catalog_filter = f"AND fk_catalog IN {_in_clause(catalogs)}" if catalogs else ""
        pair_filter = f"AND {_pair_in_clause('fk_catalog', 'fk_schema', pairs)}" if pairs else ""
        stmt = (
            "SELECT fk_catalog, fk_schema, fk_table, fk_column, ordinal_position, "
            "pk_catalog, pk_schema, pk_table, pk_column, constraint_name "
            f"FROM {_snapshot_loc()}.erd_snapshot_foreign_keys WHERE 1=1 {catalog_filter} {pair_filter} "
            "ORDER BY constraint_name, ordinal_position"
        )
        return _rows(_execute(stmt, "foreign_keys"))
    catalog_filter = f"AND ref.constraint_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    pair_filter = f"AND {_pair_in_clause('fk.table_catalog', 'fk.table_schema', pairs)}" if pairs else ""
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
      {pair_filter}
    ORDER BY ref.constraint_name, fkc.ordinal_position
    """
    return _rows(_execute(stmt, "foreign_keys"))


# --- catalog/schema tree (lightweight, for the frontend picker) -------------


def list_catalog_schemas(env: str = "prod") -> List[Dict[str, Any]]:
    """Enumerate catalog -> [schema, ...] for the frontend's catalog/schema tree picker,
    without fetching the full graph. Respects the same scope as build_graph (the
    ERD_CATALOGS allow-list, or every catalog visible if unset), resolved to the test
    catalogs (see _resolve_catalogs) when env == "test"."""
    catalogs = _resolve_catalogs(get_catalogs(), env)
    catalog_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
    source = _resolve_source(env)
    if source == _SNAPSHOT:
        stmt = (
            "SELECT DISTINCT table_catalog, table_schema "
            f"FROM {_snapshot_loc()}.erd_snapshot_tables WHERE 1=1 {catalog_filter} "
            "ORDER BY table_catalog, table_schema"
        )
    else:
        stmt = f"""
        SELECT DISTINCT table_catalog, table_schema
        FROM system.information_schema.tables
        WHERE {_internal_schema_exclusion_sql("table_catalog", "table_schema")}
          {catalog_filter}
        ORDER BY table_catalog, table_schema
        """
    rows = _rows(_execute(stmt, "schema_tree"))
    tree: Dict[str, List[str]] = {}
    for catalog, schema in rows:
        tree.setdefault(catalog, []).append(schema)
    return [{"catalog": c, "schemas": sorted(s)} for c, s in sorted(tree.items())]


# --- inferred (undeclared) relationship heuristic ---------------------------
#
# Deliberately isolated from the declared-FK query/assembly below: this is a guess, not
# a guarantee, and keeping it in one self-contained function makes it trivial to disable
# (skip calling it) or retune (edit only this function) without touching the real
# constraint-based logic. Never treat its output as equivalent to a declared FK.


def infer_relationships(
    columns: List[List[Any]],
    pks: List[List[Any]],
    fk_cols: Dict[Tuple[str, str, str], set],
) -> List[Dict[str, Any]]:
    """Heuristic: a column named exactly like another table's primary key column, with
    the same data type, is treated as a LIKELY undeclared foreign key (e.g. an orders
    table's `customer_id` column matching customers' `customer_id` primary key). Only
    fires on an unambiguous single match -- if two or more tables declare a primary key
    column with that same (name, type), the guess is too uncertain to make and is
    skipped entirely, rather than picking one arbitrarily.

    `columns` / `pks` are the same rows `build_graph` already queried (catalog, schema,
    table, column_name, full_type, ordinal, comment) and (catalog, schema, table,
    column) respectively -- no extra queries needed. `fk_cols` (table -> set of column
    names already covered by a declared FK) excludes anything already a real,
    constraint-backed relationship.
    """
    col_type: Dict[Tuple[str, str, str, str], str] = {}
    pk_col_names: Dict[Tuple[str, str, str], set] = {}
    for (catalog, schema, table, column_name, full_type, _ord, _comment) in columns:
        col_type[(catalog, schema, table, column_name)] = full_type
    for (catalog, schema, table, column) in pks:
        pk_col_names.setdefault((catalog, schema, table), set()).add(column)

    # Index PK columns by (lowercased name, type) -> candidate (catalog, schema, table, column).
    # Only single-column primary keys are eligible targets: a composite PK's individual
    # columns (e.g. a many-to-many junction table's own FK-shaped PK members) aren't
    # meaningful standalone reference targets, and routinely reuse another table's PK
    # column name+type by design -- including them would make ordinary junction tables
    # look like false ambiguity for every real match.
    pk_index: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = {}
    for (catalog, schema, table, column) in pks:
        if len(pk_col_names.get((catalog, schema, table), set())) != 1:
            continue
        full_type = col_type.get((catalog, schema, table, column))
        if not full_type:
            continue
        pk_index.setdefault((column.lower(), full_type), []).append((catalog, schema, table, column))

    inferred: List[Dict[str, Any]] = []
    for (catalog, schema, table, column_name, full_type, _ord, _comment) in columns:
        if column_name in pk_col_names.get((catalog, schema, table), set()):
            continue  # this table's own PK column -- a naming coincidence, not a reference
        if column_name in fk_cols.get((catalog, schema, table), set()):
            continue  # already a declared FK -- don't also mark it "inferred"
        candidates = [
            c for c in pk_index.get((column_name.lower(), full_type), [])
            if (c[0], c[1], c[2]) != (catalog, schema, table)
        ]
        if len(candidates) != 1:
            continue  # no match, or too ambiguous (multiple same-named PKs) to guess
        pk_catalog, pk_schema, pk_table, pk_column = candidates[0]
        inferred.append(
            {
                "id": f"inferred:{catalog}.{schema}.{table}.{column_name}",
                "source": _node_id(catalog, schema, table),
                "target": _node_id(pk_catalog, pk_schema, pk_table),
                "fk_columns": [column_name],
                "pk_columns": [pk_column],
                "constraint_name": None,
                "inferred": True,
            }
        )
    return inferred


# --- graph assembly ---------------------------------------------------------


def _node_id(catalog: str, schema: str, table: str) -> str:
    return f"{catalog}.{schema}.{table}"


def build_graph(pairs: Optional[List[Tuple[str, str]]] = None, env: str = "prod") -> Dict[str, Any]:
    """Build (or return cached) {nodes, edges} for the configured catalog allow-list
    (or every catalog visible to this deployment, if ERD_CATALOGS is unset -- see module
    docstring), optionally further narrowed to exact (catalog, schema) pairs -- the model
    the frontend's catalog/schema tree picker uses (a flat schema-name filter can't
    express "schema X under catalog A but not under catalog B"). When env == "test", the
    allow-list is resolved to its test-catalog equivalent first (see _resolve_catalogs)
    -- `pairs` is expected to already name the resolved (suffixed) catalogs too, since
    the frontend's picker is populated from list_catalog_schemas() with the same env."""
    catalogs = _resolve_catalogs(get_catalogs(), env)  # None means unscoped
    if pairs:
        validate_pairs(pairs, catalogs)  # raises ValueError naming the bad entry, rather
        # than silently dropping it and widening the result back to everything in scope.
    # The leading user key segments the cache per logged-in user in on-behalf-of-user
    # mode (results are privilege-filtered per user, so a shared cache would leak one
    # user's visible set to another); it is "" in service-principal mode, leaving the
    # cache shared exactly as before.
    key = (get_user_cache_key(), frozenset(catalogs or ()), frozenset(pairs or ()))

    cached = _CACHE.get(key)
    if cached and (time.time() - cached[0]) < get_cache_ttl_seconds():
        return cached[1]

    build_started = time.perf_counter()
    # Read from the materialized snapshot when enabled (env-gated), else live. The first
    # snapshot read doubles as the existence check: if the snapshot tables aren't there yet
    # (fresh deploy before the first refresh job), fall back to live and re-read -- no
    # separate probe round-trip on every cold build.
    source = _resolve_source(env)
    if source == _SNAPSHOT:
        try:
            tables = _query_tables(catalogs, pairs, _SNAPSHOT)
        except Exception as e:  # noqa: BLE001
            logger.warning("snapshot read failed (%s); falling back to live information_schema", e)
            source = "information_schema"
            tables = _query_tables(catalogs, pairs, source)
    else:
        tables = _query_tables(catalogs, pairs, source)

    # Above the collapse threshold, render one node per schema instead of one per table
    # -- but only for the unfiltered "All" view. A specific schema selection (pairs) is
    # exactly how a collapsed schema node "expands": clicking it sets that schema as the
    # picker selection, which re-requests /api/graph?pairs=... and always gets full
    # detail regardless of how many tables are in scope overall.
    collapse_threshold = get_schema_collapse_threshold()
    if not pairs and collapse_threshold and len(tables) > collapse_threshold:
        payload = build_schema_summary(catalogs, tables, source)
        _CACHE[key] = (time.time(), payload)
        logger.info(
            "build_graph view=schema_summary source=%s nodes=%d edges=%d %.0fms",
            source, len(payload["nodes"]), len(payload["edges"]),
            (time.perf_counter() - build_started) * 1000,
        )
        return payload

    # Fan the 5 remaining metadata queries out concurrently (they're independent) instead
    # of issuing them serially -- each is its own warehouse round-trip, so this cuts the
    # detail-view build to roughly the slowest single query rather than their sum. Each is
    # submitted through _submit_query so the per-request OBO identity rides along into the
    # worker thread. .result() re-raises any query error here, surfacing it as a 500 (the
    # tag queries swallow their own errors and return [], so they never raise).
    fut_columns = _submit_query(_query_columns, catalogs, pairs, source)
    fut_pks = _submit_query(_query_primary_keys, catalogs, pairs, source)
    fut_fks = _submit_query(_query_foreign_keys, catalogs, pairs, source)
    fut_table_tags = _submit_query(_query_table_tags, catalogs, pairs, source)
    fut_column_tags = _submit_query(_query_column_tags, catalogs, pairs, source)
    columns = fut_columns.result()
    pks = fut_pks.result()
    fks = fut_fks.result()
    table_tags = fut_table_tags.result()
    column_tags = fut_column_tags.result()

    # Set of table node-ids present in this view (drives edge filtering).
    present: set = {_node_id(c, s, t) for (c, s, t, _comment) in tables}

    # Tag lookups: node-id -> [{name, value}, ...], (catalog, schema, table, column) -> [...].
    table_tags_by_id: Dict[str, List[Dict[str, str]]] = {}
    for (catalog, schema, table, tag_name, tag_value) in table_tags:
        table_tags_by_id.setdefault(_node_id(catalog, schema, table), []).append(
            {"name": tag_name, "value": tag_value}
        )
    column_tags_by_key: Dict[Tuple[str, str, str, str], List[Dict[str, str]]] = {}
    for (catalog, schema, table, column, tag_name, tag_value) in column_tags:
        column_tags_by_key.setdefault((catalog, schema, table, column), []).append(
            {"name": tag_name, "value": tag_value}
        )

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
                "inferred": False,
            },
        )
        acc["fk_columns"].append(fk_column)
        acc["pk_columns"].append(pk_column)
    edges = list(edge_acc.values())

    # Heuristic, undeclared-relationship edges -- always computed and included (tagged
    # `inferred: true`) so the frontend can toggle them on/off client-side without a
    # second request; default OFF there keeps first load identical to pre-heuristic
    # behavior. Endpoints are already guaranteed in-scope (infer_relationships only sees
    # the same pre-filtered `columns`/`pks` rows as everything else above), but filtered
    # against `present` again anyway for defense-in-depth, matching the declared-edge check.
    edges += [
        e for e in infer_relationships(columns, pks, fk_cols)
        if e["source"] in present and e["target"] in present
    ]

    # --- nodes ---
    # Group columns by (catalog, schema, table) preserving ordinal order.
    cols_by_table: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for (catalog, schema, table, column_name, full_type, _ord, comment) in columns:
        is_pk = column_name in pk_cols.get((catalog, schema, table), set())
        is_fk = column_name in fk_cols.get((catalog, schema, table), set())
        cols_by_table.setdefault((catalog, schema, table), []).append(
            {
                "name": column_name,
                "type": full_type,
                "is_pk": is_pk,
                "is_fk": is_fk,
                "comment": comment,
                "tags": column_tags_by_key.get((catalog, schema, table, column_name), []),
            }
        )

    nodes = []
    for (catalog, schema, table, comment) in tables:
        node_id = _node_id(catalog, schema, table)
        nodes.append(
            {
                "id": node_id,
                "catalog": catalog,
                "schema": schema,
                "table": table,
                "comment": comment,
                "tags": table_tags_by_id.get(node_id, []),
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
        "view": "detail",
        "nodes": nodes,
        "edges": edges,
    }
    _CACHE[key] = (time.time(), payload)
    logger.info(
        "build_graph view=detail source=%s nodes=%d edges=%d %.0fms",
        source, len(nodes), len(edges), (time.perf_counter() - build_started) * 1000,
    )
    return payload


def build_schema_summary(catalogs: Optional[List[str]], tables: List[List[Any]],
                         source: str = "information_schema") -> Dict[str, Any]:
    """One node per (catalog, schema) with its table count, plus one aggregate edge per
    schema-to-schema FK relationship (deduped from every underlying table-level FK)
    -- the collapsed view `build_graph` falls back to above ERD_SCHEMA_COLLAPSE_THRESHOLD
    tables. `tables` is the same (catalog, schema, table, comment) rows the caller
    already queried, so this needs only one extra query (FKs, to compute schema-level
    edges) rather than re-deriving everything from scratch."""
    schema_counts: Dict[Tuple[str, str], int] = {}
    for (catalog, schema, _table, _comment) in tables:
        schema_counts[(catalog, schema)] = schema_counts.get((catalog, schema), 0) + 1

    nodes = [
        {"id": f"{catalog}.{schema}", "catalog": catalog, "schema": schema, "table_count": count}
        for (catalog, schema), count in sorted(schema_counts.items())
    ]
    present_schema_ids = {n["id"] for n in nodes}

    fks = _query_foreign_keys(catalogs, source=source)
    schema_edge_counts: Dict[Tuple[str, str], int] = {}
    for (fk_catalog, fk_schema, _fk_table, _fk_col, _ord,
         pk_catalog, pk_schema, _pk_table, _pk_col, _constraint_name) in fks:
        source = f"{fk_catalog}.{fk_schema}"
        target = f"{pk_catalog}.{pk_schema}"
        if source == target or source not in present_schema_ids or target not in present_schema_ids:
            continue
        schema_edge_counts[(source, target)] = schema_edge_counts.get((source, target), 0) + 1

    edges = [
        {
            "id": f"schema-edge:{source}->{target}",
            "source": source,
            "target": target,
            "fk_columns": [],
            "pk_columns": [],
            "constraint_name": None,
            "inferred": False,
            "relationship_count": count,
        }
        for (source, target), count in sorted(schema_edge_counts.items())
    ]

    return {
        "catalogs": sorted({n["catalog"] for n in nodes}),
        "unscoped": catalogs is None,
        "pairs": None,
        "view": "schema_summary",
        "nodes": nodes,
        "edges": edges,
    }
