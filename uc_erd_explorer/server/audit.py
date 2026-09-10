"""Deterministic schema-health audit over the ERD graph.

Pure, LLM-free rule checks that run on the graph payload build_graph() already produced --
no extra queries, no Foundation Model, no writes. It flags structural and documentation
gaps that make a schema hard to understand, join, or govern: tables with no primary key,
tables with no declared relationships, undocumented tables/columns, and columns whose names
look like personal data but carry no governance tag.

This is intentionally a *structural* read (diagram-native), distinct from a documentation
platform's coverage tracking. The PII check is a name heuristic only -- it points at
candidates a human (or a real classifier like dbxmetagen) should confirm; it never asserts
that a column IS personal data. Kept as pure functions of the payload so it's fully
unit-testable without a warehouse.
"""
import re
from typing import Any, Dict, List

# Column-name fragments that commonly denote personal data. Substring/word matches, lowercased.
# Deliberately conservative and about *names*, not values -- this only surfaces candidates to
# review or to run a real classifier over, never a classification.
_PII_PATTERNS = [
    "email", "e_mail", "phone", "mobile", "ssn", "social_security", "national_id",
    "passport", "license", "licence", "dob", "date_of_birth", "birth_date", "birthdate",
    "first_name", "last_name", "full_name", "surname", "given_name",
    "address", "street", "zip", "zipcode", "postal", "postcode",
    "credit_card", "card_number", "cardno", "account_number", "acct_no", "routing_number",
    "iban", "swift", "tax_id", "taxid", "gender", "ethnicity", "ip_address",
]
_PII_RE = re.compile("|".join(re.escape(p) for p in _PII_PATTERNS))

# A column ending in "_id" is usually a benign surrogate/foreign key, not personal data, even
# when it contains a fragment above (e.g. "address_id" is a key, not an address) -- so those
# are excluded. The exception: identifiers that ARE personal data in their own right
# (national_id, tax_id) must still be flagged despite ending in "_id".
_PII_EXCLUDE_SUFFIXES = ("_id",)
_PII_SENSITIVE_IDS = ("national_id", "tax_id")


def _looks_like_pii(column_name: str) -> bool:
    name = (column_name or "").lower()
    if name.endswith(_PII_EXCLUDE_SUFFIXES) and not any(s in name for s in _PII_SENSITIVE_IDS):
        return False
    return bool(_PII_RE.search(name))


def _short(node_id: str) -> str:
    """Last segment (table name) of a catalog.schema.table id, for compact finding labels."""
    return node_id.split(".")[-1] if node_id else node_id


def tables_without_primary_key(nodes: List[Dict[str, Any]]) -> List[str]:
    """Node ids of tables that declare no primary-key column. A missing PK blocks reliable
    joins, FK relationships, and lineage, and it's the single most common reason a table
    shows up isolated on the diagram."""
    out = []
    for n in nodes:
        cols = n.get("columns") or []
        if cols and not any(c.get("is_pk") for c in cols):
            out.append(n["id"])
    return out


def orphan_tables(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[str]:
    """Node ids with no DECLARED foreign-key relationship (in or out). Inferred/heuristic
    edges don't count -- an orphan here means UC has no declared constraint touching it, so
    it renders disconnected. Often expected (dimension/reference tables), so: info, not warn."""
    connected = set()
    for e in edges:
        if e.get("inferred"):
            continue
        connected.add(e.get("source"))
        connected.add(e.get("target"))
    return [n["id"] for n in nodes if n["id"] not in connected]


def undocumented_tables(nodes: List[Dict[str, Any]]) -> List[str]:
    """Node ids whose table-level comment is empty/None."""
    return [n["id"] for n in nodes if not (n.get("comment") or "").strip()]


def column_documentation(nodes: List[Dict[str, Any]]) -> Dict[str, int]:
    """Total vs documented column counts across all tables (a column is documented if it has
    a non-empty comment)."""
    total = documented = 0
    for n in nodes:
        for c in n.get("columns") or []:
            total += 1
            if (c.get("comment") or "").strip():
                documented += 1
    return {"total": total, "documented": documented}


def _base_type(t: str) -> str:
    """Base type name without precision/scale/length params, lowercased -- 'decimal(12,2)' ->
    'decimal', 'BIGINT' -> 'bigint', 'varchar(50)' -> 'varchar'. Used to tell a type-FAMILY
    mismatch (different base, e.g. bigint vs string) from a precision/scale drift (same base,
    e.g. decimal(10,2) vs decimal(12,2))."""
    return (t or "").strip().lower().split("(", 1)[0].strip()


def mismatched_fk_types(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Declared foreign keys whose FK column type doesn't match the referenced PK column type,
    compared position-by-position (fk_columns[i] <-> pk_columns[i]). Returns one entry per
    mismatched column pair: {kind, label} where kind is "type" (different base family, e.g.
    bigint vs string -- the join silently breaks or coerces) or "precision" (same base, drifted
    precision/scale/length, e.g. decimal(10,2) vs decimal(12,2)).

    Only DECLARED edges are checked -- an inferred edge already requires matching types by
    construction, and predicted (dbxmetagen) edges aren't declared constraints. Columns absent
    from the payload (e.g. capped out of a keys-only card) are skipped rather than guessed."""
    types: Dict[str, Dict[str, str]] = {}
    for n in nodes:
        types[n["id"]] = {(c.get("name") or "").lower(): (c.get("type") or "") for c in (n.get("columns") or [])}
    out: List[Dict[str, str]] = []
    for e in edges:
        if e.get("inferred"):
            continue
        src = e.get("source")
        tgt = e.get("target")
        src_types = types.get(src, {})
        tgt_types = types.get(tgt, {})
        for fk_c, pk_c in zip(e.get("fk_columns") or [], e.get("pk_columns") or []):
            ft = src_types.get((fk_c or "").lower())
            pt = tgt_types.get((pk_c or "").lower())
            if ft is None or pt is None:
                continue
            if ft.strip().lower() == pt.strip().lower():
                continue
            kind = "type" if _base_type(ft) != _base_type(pt) else "precision"
            out.append({
                "kind": kind,
                "label": f"{_short(src)}.{fk_c} ({ft}) → {_short(tgt)}.{pk_c} ({pt})",
            })
    return out


def blocked_relationships_by_type(nodes: List[Dict[str, Any]]) -> List[str]:
    """Columns that share a NAME with another table's single-column primary key but have a
    DIFFERENT type -- a relationship the type mismatch silently BLOCKS. Unity Catalog rejects a
    foreign key whose child column type doesn't match the parent's, and the inferred-FK
    heuristic requires a type match too -- so such a pair is never drawn as an edge at all: the
    join a reader would expect from the shared name simply doesn't exist. This is the honest
    reading of "related keys that don't have matching types" (a declared FK can't carry a
    family mismatch in the first place; that's why it hides here instead).

    Mirrors the inferred heuristic: only SINGLE-column primary keys are candidate targets, and
    an ambiguous PK name (declared by two or more tables) is skipped rather than guessed."""
    pk_by_name: Dict[str, List[Any]] = {}
    for n in nodes:
        pk_cols = [c for c in (n.get("columns") or []) if c.get("is_pk")]
        if len(pk_cols) == 1:  # single-column PK only -- same constraint as the inferred heuristic
            c = pk_cols[0]
            pk_by_name.setdefault((c.get("name") or "").lower(), []).append((n["id"], c.get("type") or ""))
    out = []
    for n in nodes:
        for c in n.get("columns") or []:
            if c.get("is_pk") or c.get("is_fk"):
                continue  # own PK, or an already-declared FK (which HAS a relationship, so not "blocked")
            candidates = [(tid, t) for (tid, t) in pk_by_name.get((c.get("name") or "").lower(), []) if tid != n["id"]]
            if len(candidates) != 1:
                continue  # no external match, or ambiguous -> skip (same rule as inference)
            tgt_id, pk_type = candidates[0]
            col_type = c.get("type") or ""
            if col_type.strip().lower() == pk_type.strip().lower():
                continue  # types match -> a normal (declared/inferred) relationship, not blocked
            out.append(f"{_short(n['id'])}.{c['name']} ({col_type}) ✕ {_short(tgt_id)}.{c['name']} ({pk_type})")
    return out


def possible_pii_untagged(nodes: List[Dict[str, Any]]) -> List[str]:
    """'catalog.schema.table.column' for columns whose NAME looks like personal data but that
    carry no Unity Catalog tag. A governance smell worth a human/classifier pass -- never a
    classification itself (see module docstring)."""
    out = []
    for n in nodes:
        for c in n.get("columns") or []:
            if _looks_like_pii(c.get("name", "")) and not (c.get("tags") or []):
                out.append(f"{n['id']}.{c['name']}")
    return out


def audit_graph(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run every deterministic check over a build_graph() payload and return a summary plus a
    findings list (most actionable first). The collapsed schema-summary view carries no column
    detail, so the audit reports itself unavailable there rather than returning misleading
    zeros."""
    if payload.get("view") != "detail":
        return {
            "available": False,
            "reason": "Select a schema (or narrow the scope) to run the health audit — the "
            "collapsed overview doesn't carry the column-level detail it needs.",
            "summary": {},
            "findings": [],
        }

    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []

    no_pk = tables_without_primary_key(nodes)
    orphans = orphan_tables(nodes, edges)
    undoc_tables = undocumented_tables(nodes)
    col_doc = column_documentation(nodes)
    pii = possible_pii_untagged(nodes)
    precision_mm = [m["label"] for m in mismatched_fk_types(nodes, edges) if m["kind"] == "precision"]
    blocked = blocked_relationships_by_type(nodes)

    total_cols = col_doc["total"]
    coverage_pct = round(100 * col_doc["documented"] / total_cols) if total_cols else 100

    summary = {
        "tables": len(nodes),
        "columns": total_cols,
        "tables_without_pk": len(no_pk),
        "orphan_tables": len(orphans),
        "undocumented_tables": len(undoc_tables),
        "column_doc_coverage_pct": coverage_pct,
        "possible_pii_untagged": len(pii),
        "type_mismatched_join_keys": len(blocked),
        "fk_precision_mismatches": len(precision_mm),
    }

    # Each finding: severity ("warn"|"info"), a stable category id, a human title, the count,
    # and the affected objects (capped so a pathological schema can't return a huge payload;
    # the count is the source of truth for "how many").
    def finding(severity, category, title, detail, objects):
        return {
            "severity": severity,
            "category": category,
            "title": title,
            "detail": detail,
            "count": len(objects),
            "objects": objects[:100],
        }

    findings = []
    if blocked:
        findings.append(finding(
            "warn", "type_mismatch_blocks_relationship",
            f"{len(blocked)} column(s) match a key by name but not by type",
            "These columns share a name with another table's primary key but have a different "
            "data type, so nothing links them: Unity Catalog rejects a type-mismatched foreign "
            "key, and the inferred-relationship heuristic requires a matching type — so a join a "
            "reader would expect from the shared name silently doesn't exist. Align the types "
            "(or confirm the columns are genuinely unrelated).",
            blocked,
        ))
    if no_pk:
        findings.append(finding(
            "warn", "no_primary_key",
            f"{len(no_pk)} table(s) have no primary key",
            "A missing primary key blocks reliable joins, foreign-key relationships, and "
            "lineage — these tables tend to render disconnected.",
            [_short(i) for i in no_pk],
        ))
    if pii:
        findings.append(finding(
            "warn", "possible_pii_untagged",
            f"{len(pii)} column(s) look like personal data but carry no tag",
            "Column names match common personal-data patterns and have no Unity Catalog tag. "
            "Review them (or run a real classifier like dbxmetagen) and tag what's confirmed — "
            "this is a name heuristic, not a classification.",
            pii,
        ))
    if undoc_tables:
        findings.append(finding(
            "info", "undocumented_table",
            f"{len(undoc_tables)} table(s) have no description",
            "No table-level COMMENT set. Descriptions make the diagram and Genie far more useful.",
            [_short(i) for i in undoc_tables],
        ))
    if precision_mm:
        findings.append(finding(
            "info", "fk_precision_mismatch",
            f"{len(precision_mm)} foreign key(s) have a precision/scale mismatch",
            "A declared foreign key and its referenced primary key share a base type but differ "
            "in precision/scale/length (e.g. decimal(10,2) vs decimal(12,2)). The join still "
            "works via coercion, but the drift can round or truncate a key — worth aligning.",
            precision_mm,
        ))
    if total_cols and coverage_pct < 100:
        findings.append(finding(
            "info", "column_doc_coverage",
            f"{coverage_pct}% of columns are documented",
            f"{col_doc['documented']} of {total_cols} columns have a COMMENT. Undocumented "
            "columns are harder for people and Genie to interpret.",
            [],
        ))
    if orphans:
        findings.append(finding(
            "info", "orphan_table",
            f"{len(orphans)} table(s) have no declared relationships",
            "No declared foreign key points to or from these tables. Often expected for "
            "reference/dimension tables; worth confirming the relationships aren't just undeclared.",
            [_short(i) for i in orphans],
        ))

    return {"available": True, "summary": summary, "findings": findings}
