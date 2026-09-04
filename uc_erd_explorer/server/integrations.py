"""dbxmetagen integration: detect whether the companion accelerator's output is present.

dbxmetagen (https://github.com/databricks-industry-solutions/dbxmetagen) is the write-side
metadata *generation* platform this read-only viewer complements. When it has run against a
catalog, it materializes signature tables -- table_knowledge_base, column_knowledge_base,
fk_predictions -- into its configured catalog.schema, and it also applies reviewed
comments/tags to Unity Catalog's native metadata (which this app already surfaces).

This module answers one question, read-only and best-effort: is dbxmetagen output present for
the catalogs in scope? If yes, the app can point at the richer metadata; if no, the app shows
a note recommending it. Detection is a single information_schema lookup for the signature
tables, cached briefly. It never writes and never raises -- any failure reads as "not present"
so a detection hiccup can't break the graph.
"""
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import get_user_cache_key
from .graph import _execute, _in_clause, _rows

DBXMETAGEN_REPO_URL = "https://github.com/databricks-industry-solutions/dbxmetagen"

# The tables dbxmetagen materializes. table_knowledge_base is the anchor (a location that has
# it is a dbxmetagen output schema); the others enrich what we can offer.
_SIGNATURE_TABLES = ("table_knowledge_base", "column_knowledge_base", "fk_predictions")
_ANCHOR_TABLE = "table_knowledge_base"

_CACHE_TTL_SECONDS = 300.0
_cache: Dict[Tuple[str, frozenset], Tuple[float, Dict[str, Any]]] = {}


def get_dbxmetagen_location() -> Optional[Tuple[str, str]]:
    """Explicit (catalog, schema) where dbxmetagen writes its tables, from
    ERD_DBXMETAGEN_LOCATION ("catalog.schema"). Optional -- when unset, detection scans the
    in-scope catalogs instead. Lets a deployment point straight at a known output schema
    (e.g. when dbxmetagen writes to a catalog this app doesn't otherwise render)."""
    raw = (os.environ.get("ERD_DBXMETAGEN_LOCATION") or "").strip()
    if raw and "." in raw:
        catalog, schema = raw.split(".", 1)
        return catalog.strip(), schema.strip()
    return None


def _detect(catalogs: Optional[List[str]]) -> Dict[str, Any]:
    """Look for dbxmetagen's signature tables and return the detection result. Best-effort:
    returns present=False on any error."""
    result: Dict[str, Any] = {"present": False, "location": None, "tables_found": [], "repo_url": DBXMETAGEN_REPO_URL}
    try:
        location = get_dbxmetagen_location()
        names_in = _in_clause(list(_SIGNATURE_TABLES))
        if location:
            cat, sch = location
            where = f"table_catalog = '{cat}' AND table_schema = '{sch}' AND table_name IN {names_in}"
        else:
            cat_filter = f"AND table_catalog IN {_in_clause(catalogs)}" if catalogs else ""
            where = f"table_name IN {names_in} {cat_filter}"
        rows = _rows(_execute(
            f"SELECT table_catalog, table_schema, table_name FROM system.information_schema.tables WHERE {where}",
            "dbxmetagen_detect", "30s",
        ))
        # Group tables by (catalog, schema); the anchor table decides a valid location.
        by_loc: Dict[Tuple[str, str], set] = {}
        for cat, sch, name in rows:
            by_loc.setdefault((cat, sch), set()).add(name)
        anchored = [(loc, names) for loc, names in by_loc.items() if _ANCHOR_TABLE in names]
        if anchored:
            # Prefer the location carrying the most signature tables (the most complete run).
            (cat, sch), names = max(anchored, key=lambda item: len(item[1]))
            result.update(
                present=True,
                location=f"{cat}.{sch}",
                tables_found=sorted(names & set(_SIGNATURE_TABLES)),
            )
    except Exception:  # noqa: BLE001 -- detection must never break the app
        pass
    return result


def detect_dbxmetagen(catalogs: Optional[List[str]]) -> Dict[str, Any]:
    """Cached detection of dbxmetagen output for the in-scope catalogs. Keyed by the per-user
    cache identity too, so an OBO user's view of what they can see isn't shared across users."""
    key = (get_user_cache_key(), frozenset(catalogs or ()))
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    result = _detect(catalogs)
    _cache[key] = (now, result)
    return result
