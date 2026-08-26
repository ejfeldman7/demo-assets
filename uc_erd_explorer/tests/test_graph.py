"""Unit tests for the pure-logic pieces of server/graph.py -- SQL-fragment builders and
the inferred-relationship heuristic. No real warehouse/network calls.
"""
import pytest

from server import graph


class TestResolveCatalogs:
    def test_prod_env_unchanged(self, monkeypatch):
        monkeypatch.setattr(graph, "get_test_catalog_suffix", lambda: "_ts")
        assert graph._resolve_catalogs(["megacorp"], "prod") == ["megacorp"]

    def test_test_env_appends_suffix(self, monkeypatch):
        monkeypatch.setattr(graph, "get_test_catalog_suffix", lambda: "_ts")
        assert graph._resolve_catalogs(["megacorp", "sales"], "test") == ["megacorp_ts", "sales_ts"]

    def test_test_env_uses_configured_suffix(self, monkeypatch):
        monkeypatch.setattr(graph, "get_test_catalog_suffix", lambda: "_test")
        assert graph._resolve_catalogs(["edp_customer"], "test") == ["edp_customer_test"]

    def test_unscoped_none_unaffected_by_test_env(self, monkeypatch):
        monkeypatch.setattr(graph, "get_test_catalog_suffix", lambda: "_ts")
        assert graph._resolve_catalogs(None, "test") is None

    def test_empty_list_unaffected_by_test_env(self, monkeypatch):
        monkeypatch.setattr(graph, "get_test_catalog_suffix", lambda: "_ts")
        assert graph._resolve_catalogs([], "test") == []


class TestValidatePairs:
    def test_valid_pairs_pass_through_unchanged(self):
        pairs = [("megacorp", "erp"), ("megacorp", "factory")]
        assert graph.validate_pairs(pairs, allowed_catalogs=None) == pairs

    def test_bad_format_raises(self):
        with pytest.raises(ValueError, match="Invalid catalog.schema pair"):
            graph.validate_pairs([("mega-corp!", "erp")], allowed_catalogs=None)

    def test_out_of_scope_catalog_raises(self):
        with pytest.raises(ValueError, match="not in this deployment's allow-list"):
            graph.validate_pairs([("other_catalog", "erp")], allowed_catalogs=["megacorp"])

    def test_in_scope_catalog_passes(self):
        pairs = [("megacorp", "erp")]
        assert graph.validate_pairs(pairs, allowed_catalogs=["megacorp", "sales"]) == pairs

    def test_unscoped_allows_any_catalog(self):
        # allowed_catalogs=None means unscoped -- no allow-list to violate.
        pairs = [("anything", "erp")]
        assert graph.validate_pairs(pairs, allowed_catalogs=None) == pairs


class TestPairInClause:
    def test_single_pair(self):
        sql = graph._pair_in_clause("c", "s", [("megacorp", "erp")])
        assert sql == "(c, s) IN (('megacorp', 'erp'))"

    def test_multiple_pairs(self):
        sql = graph._pair_in_clause("c", "s", [("megacorp", "erp"), ("megacorp", "factory")])
        assert sql == "(c, s) IN (('megacorp', 'erp'), ('megacorp', 'factory'))"

    def test_empty_pairs_is_always_false(self):
        assert graph._pair_in_clause("c", "s", []) == "1=0"

    def test_unsafe_identifiers_dropped(self):
        # A pair with a non-identifier catalog/schema name is silently excluded from the
        # clause rather than being interpolated raw into SQL.
        sql = graph._pair_in_clause("c", "s", [("ok_cat", "ok_schema"), ("bad; drop table", "x")])
        assert sql == "(c, s) IN (('ok_cat', 'ok_schema'))"

    def test_all_unsafe_falls_back_to_always_false(self):
        assert graph._pair_in_clause("c", "s", [("bad;", "x")]) == "1=0"


class TestInternalSchemaExclusionSql:
    def test_excludes_information_schema(self, monkeypatch):
        monkeypatch.setattr(graph, "get_metadata_location", lambda: ("megacorp", "erd_meta"))
        sql = graph._internal_schema_exclusion_sql("cat", "sch")
        assert "sch != 'information_schema'" in sql

    def test_excludes_configured_metadata_location(self, monkeypatch):
        monkeypatch.setattr(graph, "get_metadata_location", lambda: ("megacorp", "erd_meta"))
        sql = graph._internal_schema_exclusion_sql("cat", "sch")
        assert "NOT (cat = 'megacorp' AND sch = 'erd_meta')" in sql

    def test_exclusion_is_conjunction_not_schema_name_alone(self, monkeypatch):
        # Scoped by (catalog, schema) TOGETHER via AND -- so an unrelated catalog that
        # happens to also have a schema literally named "erd_meta" doesn't get excluded
        # just because the schema name matches; the catalog must match too.
        monkeypatch.setattr(graph, "get_metadata_location", lambda: ("megacorp", "erd_meta"))
        sql = graph._internal_schema_exclusion_sql("cat", "sch")
        assert "NOT (cat = 'megacorp' AND sch = 'erd_meta')" in sql
        assert "NOT (sch = 'erd_meta')" not in sql

    def test_excludes_dunder_prefixed_catalogs(self, monkeypatch):
        monkeypatch.setattr(graph, "get_metadata_location", lambda: ("megacorp", "erd_meta"))
        sql = graph._internal_schema_exclusion_sql("cat", "sch")
        assert "substring(cat, 1, 2) != '__'" in sql

    def test_invalid_metadata_location_does_not_break_sql(self, monkeypatch):
        # A configured metadata location with characters that would break naive string
        # interpolation is defensively blanked out rather than injected raw.
        monkeypatch.setattr(graph, "get_metadata_location", lambda: ("bad; catalog", "erd_meta"))
        sql = graph._internal_schema_exclusion_sql("cat", "sch")
        assert "bad; catalog" not in sql


def _col(catalog, schema, table, column, full_type, ordinal=1, comment=None):
    return [catalog, schema, table, column, full_type, ordinal, comment]


def _pk(catalog, schema, table, column):
    return [catalog, schema, table, column]


class TestInferRelationships:
    def test_matches_undeclared_single_column_pk_reference(self):
        columns = [
            _col("c", "s", "operators", "operator_id", "bigint"),
            _col("c", "s", "operators", "operator_name", "string"),
            _col("c", "s", "quality_inspections", "inspection_id", "bigint"),
            _col("c", "s", "quality_inspections", "operator_id", "bigint"),
        ]
        pks = [
            _pk("c", "s", "operators", "operator_id"),
            _pk("c", "s", "quality_inspections", "inspection_id"),
        ]
        fk_cols = {}
        result = graph.infer_relationships(columns, pks, fk_cols)
        assert len(result) == 1
        edge = result[0]
        assert edge["source"] == "c.s.quality_inspections"
        assert edge["target"] == "c.s.operators"
        assert edge["fk_columns"] == ["operator_id"]
        assert edge["pk_columns"] == ["operator_id"]
        assert edge["inferred"] is True
        assert edge["constraint_name"] is None

    def test_no_match_when_no_similarly_named_pk_exists(self):
        columns = [
            _col("c", "s", "operators", "operator_id", "bigint"),
            _col("c", "s", "widgets", "unrelated_column", "string"),
        ]
        pks = [_pk("c", "s", "operators", "operator_id")]
        assert graph.infer_relationships(columns, pks, {}) == []

    def test_type_mismatch_prevents_match(self):
        columns = [
            _col("c", "s", "operators", "operator_id", "bigint"),
            _col("c", "s", "quality_inspections", "operator_id", "string"),  # wrong type
        ]
        pks = [_pk("c", "s", "operators", "operator_id")]
        assert graph.infer_relationships(columns, pks, {}) == []

    def test_ambiguous_match_across_multiple_tables_is_skipped(self):
        # Two different tables both declare a single-column PK named "code" with the
        # same type -- too ambiguous to guess which one a "code" column elsewhere refers
        # to, so no inferred edge should be produced at all.
        columns = [
            _col("c", "s", "table_a", "code", "string"),
            _col("c", "s", "table_b", "code", "string"),
            _col("c", "s", "table_c", "code", "string"),
        ]
        pks = [
            _pk("c", "s", "table_a", "code"),
            _pk("c", "s", "table_b", "code"),
        ]
        assert graph.infer_relationships(columns, pks, {}) == []

    def test_composite_pk_column_is_not_a_match_target(self):
        # work_order_operators has a 3-column composite PK that happens to reuse
        # operators' PK column name+type -- this must NOT count as a candidate (nor
        # create false ambiguity that blocks the real, unambiguous match).
        columns = [
            _col("c", "s", "operators", "operator_id", "bigint"),
            _col("c", "s", "work_order_operators", "work_order_id", "bigint"),
            _col("c", "s", "work_order_operators", "operator_id", "bigint"),
            _col("c", "s", "work_order_operators", "shift_id", "bigint"),
            _col("c", "s", "quality_inspections", "operator_id", "bigint"),
        ]
        pks = [
            _pk("c", "s", "operators", "operator_id"),
            _pk("c", "s", "work_order_operators", "work_order_id"),
            _pk("c", "s", "work_order_operators", "operator_id"),
            _pk("c", "s", "work_order_operators", "shift_id"),
        ]
        result = graph.infer_relationships(columns, pks, {})
        assert len(result) == 1
        assert result[0]["source"] == "c.s.quality_inspections"
        assert result[0]["target"] == "c.s.operators"

    def test_already_declared_fk_is_not_also_marked_inferred(self):
        columns = [
            _col("c", "s", "operators", "operator_id", "bigint"),
            _col("c", "s", "quality_inspections", "operator_id", "bigint"),
        ]
        pks = [_pk("c", "s", "operators", "operator_id")]
        fk_cols = {("c", "s", "quality_inspections"): {"operator_id"}}
        assert graph.infer_relationships(columns, pks, fk_cols) == []

    def test_own_primary_key_column_is_not_a_source_candidate(self):
        # A table's own PK column can't be "inferred" as referencing itself just because
        # it shares a name/type with another table's PK -- this shouldn't happen in
        # practice (duplicate column names aren't possible within one table), but the
        # guard exists defensively and is worth locking in.
        columns = [_col("c", "s", "operators", "operator_id", "bigint")]
        pks = [_pk("c", "s", "operators", "operator_id")]
        assert graph.infer_relationships(columns, pks, {}) == []

    def test_case_insensitive_column_name_match(self):
        columns = [
            _col("c", "s", "operators", "operator_id", "bigint"),
            _col("c", "s", "quality_inspections", "Operator_ID", "bigint"),
        ]
        pks = [_pk("c", "s", "operators", "operator_id")]
        result = graph.infer_relationships(columns, pks, {})
        assert len(result) == 1
        assert result[0]["fk_columns"] == ["Operator_ID"]


class TestResolveSource:
    def test_live_when_source_is_information_schema(self, monkeypatch):
        monkeypatch.setattr(graph, "get_metadata_source", lambda: "information_schema")
        assert graph._resolve_source("prod") == "information_schema"

    def test_test_env_forces_live_even_in_snapshot_mode(self, monkeypatch):
        # The snapshot only materializes the prod catalogs; Test queries the _ts catalogs.
        monkeypatch.setattr(graph, "get_metadata_source", lambda: "snapshot")
        assert graph._resolve_source("test") == "information_schema"

    def test_snapshot_when_configured_and_prod(self, monkeypatch):
        # Intent only -- no warehouse probe. Existence is handled by build_graph's
        # fallback on the first snapshot read (see TestSnapshotFallback).
        monkeypatch.setattr(graph, "get_metadata_source", lambda: "snapshot")
        assert graph._resolve_source("prod") == "snapshot"


class TestSnapshotFallback:
    def test_build_graph_falls_back_to_live_when_snapshot_missing(self, monkeypatch):
        monkeypatch.setattr(graph, "get_metadata_source", lambda: "snapshot")
        monkeypatch.setattr(graph, "get_catalogs", lambda: ["c"])
        monkeypatch.setattr(graph, "get_metadata_location", lambda: ("c", "erd_meta"))
        monkeypatch.setattr(graph, "get_schema_collapse_threshold", lambda: 0)  # never collapse
        graph._CACHE.clear()

        table_sources = []

        def fake_tables(catalogs, pairs, source="information_schema"):
            table_sources.append(source)
            if source == "snapshot":
                raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND: erd_snapshot_tables")
            return [("c", "s", "t", None)]

        monkeypatch.setattr(graph, "_query_tables", fake_tables)
        for fn in ("_query_columns", "_query_primary_keys", "_query_foreign_keys",
                   "_query_table_tags", "_query_column_tags"):
            monkeypatch.setattr(graph, fn, lambda *a, **k: [])

        payload = graph.build_graph(None, "prod")
        # tried snapshot first, then fell back to live and re-read
        assert table_sources == ["snapshot", "information_schema"]
        assert payload["view"] == "detail"
        assert [n["table"] for n in payload["nodes"]] == ["t"]


class TestSnapshotVsLiveQuerySql:
    """The source switch must produce snapshot-table SQL in snapshot mode and
    information_schema SQL otherwise. We capture the built statement via a stubbed
    _execute rather than hitting a warehouse."""

    def _capture(self, monkeypatch):
        cap = {}

        def fake_execute(stmt, label="query", timeout="50s"):
            cap["stmt"] = stmt
            return object()

        monkeypatch.setattr(graph, "_execute", fake_execute)
        monkeypatch.setattr(graph, "_rows", lambda r: [])
        monkeypatch.setattr(graph, "get_metadata_location", lambda: ("megacorp", "erd_meta"))
        return cap

    def test_columns_snapshot(self, monkeypatch):
        cap = self._capture(monkeypatch)
        graph._query_columns(["megacorp"], None, source="snapshot")
        assert "megacorp.erd_meta.erd_snapshot_columns" in cap["stmt"]
        assert "information_schema" not in cap["stmt"]

    def test_columns_live(self, monkeypatch):
        cap = self._capture(monkeypatch)
        graph._query_columns(["megacorp"], None, source="information_schema")
        assert "system.information_schema.columns" in cap["stmt"]

    def test_primary_keys_snapshot_no_join(self, monkeypatch):
        cap = self._capture(monkeypatch)
        graph._query_primary_keys(["megacorp"], None, source="snapshot")
        assert "erd_snapshot_primary_keys" in cap["stmt"]
        assert "JOIN" not in cap["stmt"].upper()

    def test_foreign_keys_snapshot_no_join(self, monkeypatch):
        cap = self._capture(monkeypatch)
        graph._query_foreign_keys(["megacorp"], None, source="snapshot")
        assert "erd_snapshot_foreign_keys" in cap["stmt"]
        assert "JOIN" not in cap["stmt"].upper()

    def test_foreign_keys_live_has_join(self, monkeypatch):
        cap = self._capture(monkeypatch)
        graph._query_foreign_keys(["megacorp"], None, source="information_schema")
        assert "referential_constraints" in cap["stmt"]
        assert "JOIN" in cap["stmt"].upper()

    def test_pair_filter_applies_in_snapshot(self, monkeypatch):
        cap = self._capture(monkeypatch)
        graph._query_columns(["megacorp"], [("megacorp", "erp")], source="snapshot")
        assert "('megacorp', 'erp')" in cap["stmt"]
