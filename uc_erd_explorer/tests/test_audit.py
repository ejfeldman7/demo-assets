"""Unit tests for the deterministic schema-health audit (server/audit.py). Pure functions
over a synthetic graph payload -- no warehouse, no LLM."""
from server import audit


def col(name, pk=False, fk=False, comment=None, tags=None, type="string"):
    return {"name": name, "type": type, "is_pk": pk, "is_fk": fk,
            "comment": comment, "tags": tags or []}


def node(node_id, comment=None, tags=None, columns=None):
    catalog, schema, table = node_id.split(".")
    return {"id": node_id, "catalog": catalog, "schema": schema, "table": table,
            "comment": comment, "tags": tags or [], "columns": columns or []}


def edge(source, target, inferred=False, fk_cols=None, pk_cols=None):
    return {"id": f"{source}->{target}", "source": source, "target": target,
            "fk_columns": fk_cols or [], "pk_columns": pk_cols or [],
            "constraint_name": None, "inferred": inferred}


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


class TestMismatchedFkTypes:
    def _graph(self, fk_type, pk_type, inferred=False):
        nodes = [
            node("c.s.orders", columns=[col("id", pk=True), col("customer_id", fk=True, type=fk_type)]),
            node("c.s.customers", columns=[col("customer_id", pk=True, type=pk_type)]),
        ]
        edges = [edge("c.s.orders", "c.s.customers", inferred=inferred,
                      fk_cols=["customer_id"], pk_cols=["customer_id"])]
        return nodes, edges

    def test_matching_types_no_mismatch(self):
        nodes, edges = self._graph("bigint", "bigint")
        assert audit.mismatched_fk_types(nodes, edges) == []

    def test_family_mismatch_flagged_as_type(self):
        nodes, edges = self._graph("bigint", "string")
        result = audit.mismatched_fk_types(nodes, edges)
        assert len(result) == 1 and result[0]["kind"] == "type"
        assert "orders.customer_id (bigint)" in result[0]["label"]
        assert "customers.customer_id (string)" in result[0]["label"]

    def test_precision_mismatch_flagged_as_precision(self):
        nodes, edges = self._graph("decimal(10,2)", "decimal(12,2)")
        result = audit.mismatched_fk_types(nodes, edges)
        assert len(result) == 1 and result[0]["kind"] == "precision"

    def test_case_insensitive_exact_match_is_ok(self):
        nodes, edges = self._graph("BIGINT", "bigint")
        assert audit.mismatched_fk_types(nodes, edges) == []

    def test_inferred_edges_are_skipped(self):
        # An inferred edge already requires a type match by construction; never flag it.
        nodes, edges = self._graph("bigint", "string", inferred=True)
        assert audit.mismatched_fk_types(nodes, edges) == []

    def test_missing_column_in_payload_is_skipped(self):
        # PK column capped out of the (keys-only) payload -> can't compare -> skip, don't guess.
        nodes = [
            node("c.s.orders", columns=[col("customer_id", fk=True, type="bigint")]),
            node("c.s.customers", columns=[]),
        ]
        edges = [edge("c.s.orders", "c.s.customers", fk_cols=["customer_id"], pk_cols=["customer_id"])]
        assert audit.mismatched_fk_types(nodes, edges) == []

    def test_audit_graph_surfaces_blocked_and_precision_findings(self):
        # orders.customer_id (bigint) name-matches customers' PK (string) but is NOT a declared
        # FK (UC would reject that family mismatch) -> a BLOCKED relationship (warn).
        # orders.amt (decimal(10,2)) IS a declared FK to accounts.amt (decimal(12,2)) -> a
        # precision drift on an existing relationship (info), not "blocked".
        nodes = [
            node("c.s.orders", comment="o", columns=[
                col("id", pk=True),
                col("customer_id", type="bigint"),  # not is_fk: the type mismatch blocks declaring it
                col("amt", fk=True, type="decimal(10,2)"),
            ]),
            node("c.s.customers", comment="c", columns=[col("customer_id", pk=True, type="string")]),
            node("c.s.accounts", comment="a", columns=[col("amt", pk=True, type="decimal(12,2)")]),
        ]
        edges = [edge("c.s.orders", "c.s.accounts", fk_cols=["amt"], pk_cols=["amt"])]
        result = audit.audit_graph({"view": "detail", "nodes": nodes, "edges": edges})
        assert result["summary"]["type_mismatched_join_keys"] == 1
        assert result["summary"]["fk_precision_mismatches"] == 1
        cats = {f["category"]: f for f in result["findings"]}
        assert cats["type_mismatch_blocks_relationship"]["severity"] == "warn"
        assert cats["type_mismatch_blocks_relationship"]["count"] == 1
        assert cats["fk_precision_mismatch"]["severity"] == "info"
        assert cats["fk_precision_mismatch"]["count"] == 1
        # The family mismatch is NOT reported as a declared-FK finding (it can't be declared).
        assert "fk_type_mismatch" not in cats


class TestBlockedRelationshipsByType:
    def test_flags_name_match_with_different_type(self):
        nodes = [
            node("c.s.orders", columns=[col("id", pk=True), col("customer_id", type="bigint")]),
            node("c.s.customers", columns=[col("customer_id", pk=True, type="string")]),
        ]
        result = audit.blocked_relationships_by_type(nodes)
        assert len(result) == 1
        assert "orders.customer_id (bigint)" in result[0] and "customers.customer_id (string)" in result[0]

    def test_name_and_type_match_is_not_flagged(self):
        # Same name AND type -> a normal (declared/inferred) relationship, not blocked.
        nodes = [
            node("c.s.orders", columns=[col("id", pk=True), col("customer_id", type="bigint")]),
            node("c.s.customers", columns=[col("customer_id", pk=True, type="bigint")]),
        ]
        assert audit.blocked_relationships_by_type(nodes) == []

    def test_already_declared_fk_is_not_flagged(self):
        # A precision-drift column that IS a declared FK has a relationship -> not "blocked".
        nodes = [
            node("c.s.orders", columns=[col("id", pk=True), col("amt", fk=True, type="decimal(10,2)")]),
            node("c.s.accounts", columns=[col("amt", pk=True, type="decimal(12,2)")]),
        ]
        assert audit.blocked_relationships_by_type(nodes) == []

    def test_ambiguous_pk_name_is_skipped(self):
        # "code" is a single-column PK of TWO tables -> too ambiguous to attribute, skip.
        nodes = [
            node("c.s.a", columns=[col("code", pk=True, type="string")]),
            node("c.s.b", columns=[col("code", pk=True, type="string")]),
            node("c.s.uses", columns=[col("id", pk=True), col("code", type="bigint")]),
        ]
        assert audit.blocked_relationships_by_type(nodes) == []

    def test_composite_pk_is_not_a_target(self):
        # Only single-column PKs are candidate targets (mirrors the inferred heuristic).
        nodes = [
            node("c.s.bridge", columns=[col("a_id", pk=True, type="bigint"), col("b_id", pk=True, type="bigint")]),
            node("c.s.uses", columns=[col("id", pk=True), col("a_id", type="string")]),
        ]
        assert audit.blocked_relationships_by_type(nodes) == []


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
