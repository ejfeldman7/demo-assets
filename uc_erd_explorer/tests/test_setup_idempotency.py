"""Unit tests for the setup/ scripts' idempotency-relevant logic: statement building for
create_scoped_views.py and create_genie_space.py (re-running with the same config must
produce equivalent output, not drift or duplicate), the catalog-name substitution in
create_megacorp_demo.py and create_logistics_demo.py, and the branching logic in
grant_catalog_access.py. All pure functions / mocked I/O -- no real warehouse or
Databricks credentials needed.
"""
import re

import pytest

import build_erd_snapshot
import create_genie_space
import create_logistics_demo
import create_megacorp_demo
import create_scoped_views
import grant_catalog_access


class TestCreateScopedViewsBuildStatements:
    def test_deterministic_across_calls(self):
        # Re-running setup with the same config must produce byte-identical DDL, or a
        # naive "run it again" could introduce drift (e.g. non-deterministic ordering).
        a = create_scoped_views.build_statements(["megacorp"], "megacorp", "erd_meta")
        b = create_scoped_views.build_statements(["megacorp"], "megacorp", "erd_meta")
        assert a == b

    def test_scoped_mode_filters_by_catalog(self):
        statements = create_scoped_views.build_statements(["megacorp"], "megacorp", "erd_meta")
        joined = "\n".join(statements)
        assert "table_catalog IN ('megacorp')" in joined

    def test_unscoped_mode_omits_catalog_filter(self):
        statements = create_scoped_views.build_statements([], "megacorp", "erd_meta")
        joined = "\n".join(statements)
        assert "table_catalog IN" not in joined
        assert "ALL catalogs visible to this deployment" in joined

    def test_excludes_own_metadata_schema_from_results(self):
        # The erd_meta schema's own housekeeping views must not leak into
        # table_summary/scoped_tables as fake business tables.
        statements = create_scoped_views.build_statements(["megacorp"], "megacorp", "erd_meta")
        joined = "\n".join(statements)
        assert "'erd_meta'" in joined

    def test_invalid_catalog_identifier_raises(self):
        with pytest.raises(ValueError):
            create_scoped_views.build_statements(["bad; catalog"], "megacorp", "erd_meta")

    def test_multi_catalog_scoping(self):
        statements = create_scoped_views.build_statements(["megacorp", "sales"], "megacorp", "erd_meta")
        joined = "\n".join(statements)
        assert "'megacorp'" in joined and "'sales'" in joined


class TestBuildErdSnapshotStatements:
    def _sql(self, statements):
        return "\n".join(sql for _label, sql, _optional in statements)

    def test_deterministic_across_calls(self):
        a = build_erd_snapshot.build_statements(["megacorp"], "megacorp", "erd_meta")
        b = build_erd_snapshot.build_statements(["megacorp"], "megacorp", "erd_meta")
        assert a == b

    def test_creates_all_snapshot_tables_plus_meta(self):
        statements = build_erd_snapshot.build_statements(["megacorp"], "megacorp", "erd_meta")
        labels = {label for label, _sql, _opt in statements}
        assert {
            "erd_snapshot_tables", "erd_snapshot_columns", "erd_snapshot_primary_keys",
            "erd_snapshot_foreign_keys", "erd_snapshot_table_tags", "erd_snapshot_column_tags",
            "erd_snapshot_meta",
        }.issubset(labels)

    def test_writes_to_metadata_location(self):
        statements = build_erd_snapshot.build_statements(["megacorp"], "megacorp", "erd_meta")
        joined = self._sql(statements)
        assert "CREATE OR REPLACE TABLE megacorp.erd_meta.erd_snapshot_tables" in joined
        # The FK edge list is materialized (the join done in the snapshot, not the app).
        assert "megacorp.erd_meta.erd_snapshot_foreign_keys" in joined
        assert "referential_constraints" in joined

    def test_scoped_mode_filters_by_catalog(self):
        joined = self._sql(build_erd_snapshot.build_statements(["megacorp"], "megacorp", "erd_meta"))
        assert "IN ('megacorp')" in joined

    def test_unscoped_mode_omits_catalog_filter(self):
        joined = self._sql(build_erd_snapshot.build_statements([], "megacorp", "erd_meta"))
        assert "table_catalog IN" not in joined

    def test_tag_snapshots_flagged_optional(self):
        statements = build_erd_snapshot.build_statements(["megacorp"], "megacorp", "erd_meta")
        optional = {label for label, _sql, opt in statements if opt}
        assert optional == {"erd_snapshot_table_tags", "erd_snapshot_column_tags"}

    def test_invalid_catalog_identifier_raises(self):
        with pytest.raises(ValueError):
            build_erd_snapshot.build_statements(["bad; catalog"], "megacorp", "erd_meta")


class _FakeState:
    def __init__(self, value):
        self.value = value


class _FakeStatus:
    def __init__(self, value):
        self.state = _FakeState(value)
        self.error = None  # real StatementResponse.status has both state and error


class _FakeResp:
    def __init__(self, value):
        self.status = _FakeStatus(value)
        self.statement_id = "sid"


class _FakeStmtExec:
    """Minimal stand-in for w.statement_execution: statements whose text contains any of
    fail_substrings come back FAILED, everything else SUCCEEDED (terminal, so _run's poll
    loop doesn't spin)."""
    def __init__(self, fail_substrings=()):
        self.fail_substrings = fail_substrings
        self.executed = []

    def execute_statement(self, warehouse_id, statement, wait_timeout):
        self.executed.append(statement)
        failed = any(s in statement for s in self.fail_substrings)
        return _FakeResp("FAILED" if failed else "SUCCEEDED")

    def get_statement(self, statement_id):
        return _FakeResp("SUCCEEDED")


class _FakeW:
    def __init__(self, fail_substrings=()):
        self.statement_execution = _FakeStmtExec(fail_substrings)


class TestBuildErdSnapshotMaterialize:
    def test_runs_all_statements_and_returns_loc(self):
        w = _FakeW()
        loc = build_erd_snapshot.materialize(w, "wh", ["c"], "c", "erd_meta", log=lambda *a: None)
        assert loc == "c.erd_meta"
        assert len(w.statement_execution.executed) == 8  # schema + 6 tables + meta

    def test_optional_tag_source_missing_creates_empty_table(self):
        # column_tags CTAS fails -> the optional branch creates an empty table instead of
        # raising (and instead of silently leaving it un-created).
        w = _FakeW(fail_substrings=("information_schema.column_tags",))
        build_erd_snapshot.materialize(w, "wh", ["c"], "c", "erd_meta", log=lambda *a: None)
        assert any("erd_snapshot_column_tags (" in s for s in w.statement_execution.executed)

    def test_required_statement_failure_raises(self):
        # A non-optional failure (columns CTAS) must raise, not silently pass.
        w = _FakeW(fail_substrings=("information_schema.columns",))
        with pytest.raises(RuntimeError):
            build_erd_snapshot.materialize(w, "wh", ["c"], "c", "erd_meta", log=lambda *a: None)


class TestCreateGenieSpaceBuildSerializedSpace:
    def test_structurally_equivalent_across_calls(self):
        # Not byte-identical (ids are freshly generated uuids each call by design), but
        # re-running setup must produce a space with the same content -- same tables,
        # same instruction/example counts -- not a duplicated or drifted one.
        a = create_genie_space.build_serialized_space(["megacorp"], "megacorp.erd_meta", "wh-123")
        b = create_genie_space.build_serialized_space(["megacorp"], "megacorp.erd_meta", "wh-123")
        a_dict, b_dict = a.to_dict(), b.to_dict()

        a_tables = [t["identifier"] for t in a_dict["data_sources"]["tables"]]
        b_tables = [t["identifier"] for t in b_dict["data_sources"]["tables"]]
        assert a_tables == b_tables

        assert len(a_dict["instructions"]["example_question_sqls"]) == len(b_dict["instructions"]["example_question_sqls"])
        assert len(a_dict["benchmarks"]["questions"]) == len(b_dict["benchmarks"]["questions"])

    def test_validates_successfully(self):
        builder = create_genie_space.build_serialized_space(["megacorp"], "megacorp.erd_meta", "wh-123")
        assert builder.validate() is True

    def test_curates_only_the_three_narrow_views(self):
        builder = create_genie_space.build_serialized_space(["megacorp"], "megacorp.erd_meta", "wh-123")
        tables = [t["identifier"] for t in builder.to_dict()["data_sources"]["tables"]]
        assert set(tables) == {
            "megacorp.erd_meta.table_summary",
            "megacorp.erd_meta.column_inventory",
            "megacorp.erd_meta.fk_edges",
        }

    def test_unscoped_title_and_description_are_generic(self):
        builder = create_genie_space.build_serialized_space([], "megacorp.erd_meta", "wh-123")
        assert "ALL catalogs visible to this deployment" in builder.title
        assert "megacorp" not in builder.title.lower()

    def test_ids_are_sorted_ascending_in_every_collection(self):
        # A real, undocumented Genie API requirement discovered against a live
        # workspace: every id-keyed collection must be sorted ascending by id.
        builder = create_genie_space.build_serialized_space(["megacorp"], "megacorp.erd_meta", "wh-123")
        d = builder.to_dict()
        for path in (
            d["instructions"]["example_question_sqls"],
            d["instructions"]["join_specs"],
            d["instructions"]["sql_snippets"]["filters"],
            d["instructions"]["sql_snippets"]["expressions"],
            d["instructions"]["sql_snippets"]["measures"],
            d["benchmarks"]["questions"],
        ):
            ids = [item["id"] for item in path]
            assert ids == sorted(ids)

    def test_column_configs_sorted_by_column_name(self):
        # Another real, undocumented API requirement discovered the hard way.
        builder = create_genie_space.build_serialized_space(["megacorp"], "megacorp.erd_meta", "wh-123")
        for table in builder.to_dict()["data_sources"]["tables"]:
            names = [c["column_name"] for c in table.get("column_configs", [])]
            assert names == sorted(names)


class TestCreateGenieSpaceResolveCatalogs:
    def test_explicit_arg_wins(self, monkeypatch):
        monkeypatch.setenv("ERD_CATALOGS", "sales")
        assert create_genie_space.resolve_catalogs("megacorp,factory") == ["megacorp", "factory"]

    def test_falls_back_to_env_var(self, monkeypatch):
        monkeypatch.setenv("ERD_CATALOGS", "sales,inventory")
        assert create_genie_space.resolve_catalogs("") == ["sales", "inventory"]

    def test_unscoped_when_both_unset(self, monkeypatch):
        monkeypatch.delenv("ERD_CATALOGS", raising=False)
        assert create_genie_space.resolve_catalogs("") == []


class TestCreateGenieSpaceResolveMetadataLocation:
    def test_explicit_arg_wins(self):
        assert create_genie_space.resolve_metadata_location("sales.meta", ["megacorp"]) == "sales.meta"

    def test_defaults_to_first_catalog(self):
        assert create_genie_space.resolve_metadata_location("", ["megacorp", "sales"]) == "megacorp.erd_meta"

    def test_unscoped_without_explicit_location_exits(self):
        with pytest.raises(SystemExit):
            create_genie_space.resolve_metadata_location("", [])


class TestSubstituteCatalog:
    SAMPLE_SQL = (
        "CREATE CATALOG IF NOT EXISTS megacorp COMMENT 'demo';\n"
        "CREATE SCHEMA IF NOT EXISTS megacorp.factory;\n"
        "CREATE TABLE IF NOT EXISTS megacorp.factory.plants (id BIGINT);\n"
    )

    def test_replaces_qualifier_references(self):
        result = create_megacorp_demo.substitute_catalog(self.SAMPLE_SQL, "my_catalog", include_create_catalog=True)
        assert "megacorp.factory" not in result
        assert "my_catalog.factory" in result
        assert "my_catalog.factory.plants" in result

    def test_replaces_create_catalog_statement(self):
        result = create_megacorp_demo.substitute_catalog(self.SAMPLE_SQL, "my_catalog", include_create_catalog=True)
        assert "CREATE CATALOG IF NOT EXISTS my_catalog COMMENT 'demo';" in result

    def test_drops_create_catalog_statement_when_catalog_already_exists(self):
        result = create_megacorp_demo.substitute_catalog(self.SAMPLE_SQL, "my_catalog", include_create_catalog=False)
        assert "CREATE CATALOG" not in result
        # The rest of the DDL is untouched.
        assert "CREATE SCHEMA IF NOT EXISTS my_catalog.factory;" in result
        assert "CREATE TABLE IF NOT EXISTS my_catalog.factory.plants" in result

    def test_does_not_touch_prose_mentioning_megacorp(self):
        sql = "-- A comment describing megacorp's structure, not an identifier\nCREATE SCHEMA IF NOT EXISTS megacorp.factory;"
        result = create_megacorp_demo.substitute_catalog(sql, "my_catalog", include_create_catalog=True)
        assert "describing megacorp's structure" in result

    def test_default_catalog_name_is_a_no_op(self):
        result = create_megacorp_demo.substitute_catalog(self.SAMPLE_SQL, "megacorp", include_create_catalog=True)
        assert result == self.SAMPLE_SQL


class TestSubstituteCatalogsForLogistics:
    SAMPLE_SQL = (
        "CREATE CATALOG IF NOT EXISTS logistics COMMENT 'demo';\n"
        "CREATE SCHEMA IF NOT EXISTS logistics.shipping;\n"
        "CREATE TABLE IF NOT EXISTS logistics.shipping.shipments (\n"
        "  sales_order_id BIGINT,\n"
        "  CONSTRAINT shipments_sales_order_id_fk FOREIGN KEY (sales_order_id) REFERENCES megacorp.erp.sales_orders (sales_order_id)\n"
        ");\n"
    )

    def test_replaces_both_qualifiers_independently(self):
        result = create_logistics_demo.substitute_catalogs(
            self.SAMPLE_SQL, "my_logistics", "my_megacorp", include_create_catalog=True
        )
        # Word-boundary check, not a bare substring check -- "my_logistics.shipping"
        # legitimately contains "logistics.shipping" as a substring.
        assert not re.search(r"(?<![\w])logistics\.", result)
        assert not re.search(r"(?<![\w])megacorp\.", result)
        assert "my_logistics.shipping.shipments" in result
        assert "my_megacorp.erp.sales_orders" in result

    def test_replaces_create_catalog_statement(self):
        result = create_logistics_demo.substitute_catalogs(
            self.SAMPLE_SQL, "my_logistics", "megacorp", include_create_catalog=True
        )
        assert "CREATE CATALOG IF NOT EXISTS my_logistics COMMENT 'demo';" in result

    def test_drops_create_catalog_statement_when_catalog_already_exists(self):
        result = create_logistics_demo.substitute_catalogs(
            self.SAMPLE_SQL, "my_logistics", "megacorp", include_create_catalog=False
        )
        assert "CREATE CATALOG" not in result
        assert "CREATE SCHEMA IF NOT EXISTS my_logistics.shipping;" in result

    def test_test_environment_pairing_never_cross_wires_prod(self):
        # The exact scenario this exists for: logistics_ts must reference megacorp_ts,
        # never the prod megacorp catalog.
        result = create_logistics_demo.substitute_catalogs(
            self.SAMPLE_SQL, "logistics_ts", "megacorp_ts", include_create_catalog=True
        )
        assert "megacorp_ts.erp.sales_orders" in result
        assert "logistics_ts.shipping.shipments" in result
        # Neither bare prod name should survive as its own qualifier (only as a prefix
        # of the _ts-suffixed one).
        assert not re.search(r"(?<![\w])logistics\.", result)
        assert not re.search(r"(?<![\w])megacorp\.", result)

    def test_default_catalog_names_are_a_no_op(self):
        result = create_logistics_demo.substitute_catalogs(
            self.SAMPLE_SQL, "logistics", "megacorp", include_create_catalog=True
        )
        assert result == self.SAMPLE_SQL


class TestGrantCatalogAccess:
    def _capture(self, monkeypatch, schemas_by_catalog=None):
        """Replace the real network calls with recorders, return the list of attempted
        GRANT statements in order."""
        attempted = []
        monkeypatch.setattr(
            grant_catalog_access, "_run_grant",
            lambda w, warehouse_id, statement: attempted.append(statement),
        )
        monkeypatch.setattr(
            grant_catalog_access, "_list_schemas",
            lambda w, warehouse_id, catalog: (schemas_by_catalog or {}).get(catalog, []),
        )
        return attempted

    def test_scoped_mode_grants_catalog_and_per_schema_and_metadata(self, monkeypatch):
        attempted = self._capture(monkeypatch, {"megacorp": ["factory", "erp", "erd_meta"]})
        grant_catalog_access.grant_catalog_access(
            w=None, warehouse_id="wh", catalogs=["megacorp"],
            metadata_catalog="megacorp", metadata_schema="erd_meta", sp_client_id="sp-123",
        )
        joined = "\n".join(attempted)
        assert "GRANT USE CATALOG ON CATALOG megacorp TO `sp-123`" in joined
        assert "GRANT SELECT ON CATALOG megacorp TO `sp-123`" in joined
        assert "GRANT USE SCHEMA ON SCHEMA megacorp.factory TO `sp-123`" in joined
        assert "GRANT SELECT ON SCHEMA megacorp.factory TO `sp-123`" in joined
        assert "GRANT USE SCHEMA ON SCHEMA megacorp.erp TO `sp-123`" in joined
        assert "GRANT USE SCHEMA ON SCHEMA megacorp.erd_meta TO `sp-123`" in joined

    def test_unscoped_mode_skips_catalog_grants_but_still_grants_metadata(self, monkeypatch):
        # Regression test for a real bug: this used to return early on empty catalogs
        # and skip the metadata-location grant entirely, even though Genie's scoped
        # views need it regardless of whether the main graph is scoped or unscoped.
        attempted = self._capture(monkeypatch)
        grant_catalog_access.grant_catalog_access(
            w=None, warehouse_id="wh", catalogs=[],
            metadata_catalog="megacorp", metadata_schema="erd_meta", sp_client_id="sp-123",
        )
        # No cascading catalog-level USE SCHEMA/SELECT grants -- those only happen inside
        # the per-data-catalog loop, which is skipped entirely when catalogs is empty.
        assert not any("USE SCHEMA ON CATALOG" in s or "SELECT ON CATALOG" in s for s in attempted)
        # The metadata location still gets its own catalog-level bootstrap grant (since
        # it's never itself "in" an empty catalogs list) plus its schema-level grants.
        assert "GRANT USE CATALOG ON CATALOG megacorp TO `sp-123`" in attempted
        assert "GRANT USE SCHEMA ON SCHEMA megacorp.erd_meta TO `sp-123`" in attempted
        assert "GRANT SELECT ON SCHEMA megacorp.erd_meta TO `sp-123`" in attempted

    def test_metadata_catalog_not_in_scoped_catalogs_gets_its_own_catalog_grant(self, monkeypatch):
        attempted = self._capture(monkeypatch, {"megacorp": []})
        grant_catalog_access.grant_catalog_access(
            w=None, warehouse_id="wh", catalogs=["megacorp"],
            metadata_catalog="other_catalog", metadata_schema="erd_meta", sp_client_id="sp-123",
        )
        joined = "\n".join(attempted)
        assert "GRANT USE CATALOG ON CATALOG other_catalog TO `sp-123`" in joined

    def test_metadata_catalog_already_in_scoped_catalogs_not_granted_twice_at_catalog_level(self, monkeypatch):
        attempted = self._capture(monkeypatch, {"megacorp": []})
        grant_catalog_access.grant_catalog_access(
            w=None, warehouse_id="wh", catalogs=["megacorp"],
            metadata_catalog="megacorp", metadata_schema="erd_meta", sp_client_id="sp-123",
        )
        catalog_grant = "GRANT USE CATALOG ON CATALOG megacorp TO `sp-123`"
        assert attempted.count(catalog_grant) == 1
