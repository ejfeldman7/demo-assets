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

# A column named exactly one of these is a benign identifier, not personal data, even though
# it may contain a fragment above (e.g. "address_id" is a surrogate key, not an address).
_PII_EXCLUDE_SUFFIXES = ("_id",)


def _looks_like_pii(column_name: str) -> bool:
    name = (column_name or "").lower()
    if name.endswith(_PII_EXCLUDE_SUFFIXES):
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
