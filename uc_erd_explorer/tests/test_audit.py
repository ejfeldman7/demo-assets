"""Unit tests for the deterministic schema-health audit (server/audit.py). Pure functions
over a synthetic graph payload -- no warehouse, no LLM."""
from server import audit


def col(name, pk=False, fk=False, comment=None, tags=None):
    return {"name": name, "type": "string", "is_pk": pk, "is_fk": fk,
            "comment": comment, "tags": tags or []}


def node(node_id, comment=None, tags=None, columns=None):
    catalog, schema, table = node_id.split(".")
    return {"id": node_id, "catalog": catalog, "schema": schema, "table": table,
            "comment": comment, "tags": tags or [], "columns": columns or []}


def edge(source, target, inferred=False):
    return {"id": f"{source}->{target}", "source": source, "target": target,
            "fk_columns": [], "pk_columns": [], "constraint_name": None, "inferred": inferred}


class TestTablesWithoutPrimaryKey:
    def test_flags_table_with_no_pk_column(self):
        nodes = [node("c.s.orders", columns=[col("id"), col("total")])]
        assert audit.tables_without_primary_key(nodes) == ["c.s.orders"]

    def test_table_with_pk_not_flagged(self):
        nodes = [node("c.s.orders", columns=[col("id", pk=True)])]
        assert audit.tables_without_primary_key(nodes) == []

    def test_table_with_no_columns_is_skipped(self):
        # A card with zero columns (e.g. nothing queryable) shouldn't be reported as "no PK".
        assert audit.tables_without_primary_key([node("c.s.empty", columns=[])]) == []


class TestOrphanTables:
    def test_table_in_no_declared_edge_is_orphan(self):
        nodes = [node("c.s.a"), node("c.s.b"), node("c.s.island")]
        edges = [edge("c.s.a", "c.s.b")]
        assert audit.orphan_tables(nodes, edges) == ["c.s.island"]

    def test_inferred_edges_do_not_rescue_from_orphan(self):
        nodes = [node("c.s.a"), node("c.s.b")]
        edges = [edge("c.s.a", "c.s.b", inferred=True)]
        assert set(audit.orphan_tables(nodes, edges)) == {"c.s.a", "c.s.b"}


class TestDocumentation:
    def test_undocumented_tables(self):
        nodes = [node("c.s.a", comment="has one"), node("c.s.b", comment=""), node("c.s.c")]
        assert audit.undocumented_tables(nodes) == ["c.s.b", "c.s.c"]

    def test_column_documentation_counts(self):
        nodes = [node("c.s.a", columns=[col("x", comment="doc"), col("y"), col("z", comment="  ")])]
        assert audit.column_documentation(nodes) == {"total": 3, "documented": 1}


class TestPossiblePii:
    def test_flags_pii_named_untagged_column(self):
        nodes = [node("c.s.cust", columns=[col("email"), col("customer_ssn")])]
        assert audit.possible_pii_untagged(nodes) == ["c.s.cust.email", "c.s.cust.customer_ssn"]

    def test_id_suffix_excluded(self):
        # address_id is a surrogate key, not an address.
        nodes = [node("c.s.t", columns=[col("address_id")])]
        assert audit.possible_pii_untagged(nodes) == []

    def test_sensitive_ids_flagged_despite_id_suffix(self):
        # national_id / tax_id ARE personal data and must not be swept up by the _id exclusion.
        assert audit._looks_like_pii("national_id") is True
        assert audit._looks_like_pii("tax_id") is True
        assert audit._looks_like_pii("customer_id") is False  # ordinary surrogate key

    def test_tagged_pii_not_flagged(self):
        nodes = [node("c.s.cust", columns=[col("email", tags=[{"name": "pii", "value": "email"}])])]
        assert audit.possible_pii_untagged(nodes) == []


class TestAuditGraph:
    def test_schema_summary_reports_unavailable(self):
        result = audit.audit_graph({"view": "schema_summary", "nodes": [], "edges": []})
        assert result["available"] is False
        assert "collapsed overview" in result["reason"]

    def test_detail_view_returns_summary_and_findings(self):
        payload = {
            "view": "detail",
            "nodes": [
                node("c.s.customers", comment="Customers", columns=[
                    col("customer_id", pk=True, comment="PK"),
                    col("email"),  # possible PII, untagged
                ]),
                node("c.s.loose", columns=[col("val")]),  # no PK, undocumented, orphan
            ],
            "edges": [],
        }
        result = audit.audit_graph(payload)
        assert result["available"] is True
        s = result["summary"]
        assert s["tables"] == 2
        assert s["columns"] == 3
        assert s["tables_without_pk"] == 1
        assert s["orphan_tables"] == 2
        assert s["possible_pii_untagged"] == 1
        assert 0 <= s["column_doc_coverage_pct"] <= 100
        categories = {f["category"] for f in result["findings"]}
        assert {"no_primary_key", "possible_pii_untagged", "orphan_table"} <= categories

    def test_findings_cap_objects_but_count_is_true(self):
        nodes = [node(f"c.s.t{i}", columns=[col("val")]) for i in range(150)]
        result = audit.audit_graph({"view": "detail", "nodes": nodes, "edges": []})
        no_pk = next(f for f in result["findings"] if f["category"] == "no_primary_key")
        assert no_pk["count"] == 150          # true count
        assert len(no_pk["objects"]) == 100   # object list capped
