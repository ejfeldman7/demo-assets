"""Unit tests for server/config.py's dual-mode auth / catalog / metadata-location
resolution logic. Pure-logic + mocked WorkspaceClient -- no real network calls, no
Databricks credentials needed, safe to run in CI on every push.
"""
import pytest

from server import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every ERD_*/DATABRICKS_* env var this module reads, cleared before each test so
    tests can't leak into each other via the real environment."""
    for var in (
        "ERD_CATALOGS",
        "ERD_METADATA_LOCATION",
        "DATABRICKS_APP_NAME",
        "DATABRICKS_PROFILE",
        "DATABRICKS_WAREHOUSE_ID",
        "GENIE_SPACE_ID",
        "ERD_CACHE_TTL_SECONDS",
        "ERD_SCHEMA_COLLAPSE_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)
    config.get_workspace_name.cache_clear()
    yield
    config.get_workspace_name.cache_clear()


class TestGetCatalogs:
    def test_unset_is_unscoped(self):
        assert config.get_catalogs() is None

    def test_blank_is_unscoped(self, monkeypatch):
        monkeypatch.setenv("ERD_CATALOGS", "")
        assert config.get_catalogs() is None

    def test_whitespace_only_is_unscoped(self, monkeypatch):
        # e.g. "erd_catalogs=  ,  " -- every entry strips to empty, so this is
        # unscoped too, not a single-empty-string catalog list.
        monkeypatch.setenv("ERD_CATALOGS", "  ,  ")
        assert config.get_catalogs() is None

    def test_single_catalog(self, monkeypatch):
        monkeypatch.setenv("ERD_CATALOGS", "megacorp")
        assert config.get_catalogs() == ["megacorp"]

    def test_multiple_catalogs_trims_whitespace(self, monkeypatch):
        monkeypatch.setenv("ERD_CATALOGS", "megacorp, sales , inventory")
        assert config.get_catalogs() == ["megacorp", "sales", "inventory"]


class TestGetMetadataLocation:
    def test_explicit_location(self, monkeypatch):
        monkeypatch.setenv("ERD_METADATA_LOCATION", "sales.erd_meta")
        assert config.get_metadata_location() == ("sales", "erd_meta")

    def test_explicit_location_trims_whitespace(self, monkeypatch):
        monkeypatch.setenv("ERD_METADATA_LOCATION", " sales . erd_meta ")
        assert config.get_metadata_location() == ("sales", "erd_meta")

    def test_defaults_to_first_catalog_erd_meta(self, monkeypatch):
        monkeypatch.setenv("ERD_CATALOGS", "megacorp,sales")
        assert config.get_metadata_location() == ("megacorp", "erd_meta")

    def test_unscoped_without_explicit_location_raises(self, monkeypatch):
        # ERD_CATALOGS unset (unscoped) AND ERD_METADATA_LOCATION unset -- there's no
        # catalog to default the metadata views into, so this must fail loudly rather
        # than silently picking one.
        with pytest.raises(RuntimeError, match="ERD_METADATA_LOCATION is required"):
            config.get_metadata_location()

    def test_value_without_dot_falls_back_to_default(self, monkeypatch):
        # A malformed "catalog.schema" value (no dot) should be treated as if unset,
        # not crash on `.split(".", 1)` unpacking a 1-element list.
        monkeypatch.setenv("ERD_METADATA_LOCATION", "not_a_valid_location")
        monkeypatch.setenv("ERD_CATALOGS", "megacorp")
        assert config.get_metadata_location() == ("megacorp", "erd_meta")


class TestGetWorkspaceClient:
    def test_databricks_app_mode_uses_ambient_auth(self, monkeypatch):
        monkeypatch.setattr(config, "IS_DATABRICKS_APP", True)
        calls = []
        monkeypatch.setattr(config, "WorkspaceClient", lambda **kw: calls.append(kw) or object())
        config.get_workspace_client()
        assert calls == [{}]

    def test_local_mode_uses_default_profile_when_unset(self, monkeypatch):
        monkeypatch.setattr(config, "IS_DATABRICKS_APP", False)
        calls = []
        monkeypatch.setattr(config, "WorkspaceClient", lambda **kw: calls.append(kw) or object())
        config.get_workspace_client()
        assert calls == [{}]

    def test_local_mode_uses_named_profile(self, monkeypatch):
        monkeypatch.setattr(config, "IS_DATABRICKS_APP", False)
        monkeypatch.setenv("DATABRICKS_PROFILE", "my-profile")
        calls = []
        monkeypatch.setattr(config, "WorkspaceClient", lambda **kw: calls.append(kw) or object())
        config.get_workspace_client()
        assert calls == [{"profile": "my-profile"}]


class TestGetWarehouseId:
    def test_unset_returns_none_no_hardcoded_fallback(self):
        assert config.get_warehouse_id() is None

    def test_set(self, monkeypatch):
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123")
        assert config.get_warehouse_id() == "abc123"


class TestGetGenieSpaceId:
    def test_unset_returns_none(self):
        assert config.get_genie_space_id() is None

    def test_blank_returns_none(self, monkeypatch):
        monkeypatch.setenv("GENIE_SPACE_ID", "")
        assert config.get_genie_space_id() is None

    def test_set(self, monkeypatch):
        monkeypatch.setenv("GENIE_SPACE_ID", "abc-123")
        assert config.get_genie_space_id() == "abc-123"


class TestGetCacheTtlSeconds:
    def test_default(self):
        assert config.get_cache_ttl_seconds() == 300

    def test_override(self, monkeypatch):
        monkeypatch.setenv("ERD_CACHE_TTL_SECONDS", "60")
        assert config.get_cache_ttl_seconds() == 60

    def test_negative_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("ERD_CACHE_TTL_SECONDS", "-5")
        assert config.get_cache_ttl_seconds() == 0

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ERD_CACHE_TTL_SECONDS", "not-a-number")
        assert config.get_cache_ttl_seconds() == 300


class TestGetSchemaCollapseThreshold:
    def test_default(self):
        assert config.get_schema_collapse_threshold() == 80

    def test_override(self, monkeypatch):
        monkeypatch.setenv("ERD_SCHEMA_COLLAPSE_THRESHOLD", "150")
        assert config.get_schema_collapse_threshold() == 150

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("ERD_SCHEMA_COLLAPSE_THRESHOLD", "0")
        assert config.get_schema_collapse_threshold() is None

    def test_negative_disables(self, monkeypatch):
        monkeypatch.setenv("ERD_SCHEMA_COLLAPSE_THRESHOLD", "-1")
        assert config.get_schema_collapse_threshold() is None

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ERD_SCHEMA_COLLAPSE_THRESHOLD", "not-a-number")
        assert config.get_schema_collapse_threshold() == 80


class _FakeClientConfig:
    def __init__(self, host):
        self.host = host


class _FakeClient:
    def __init__(self, host):
        self.config = _FakeClientConfig(host)


class TestGetWorkspaceName:
    def test_strips_aws_suffix(self, monkeypatch):
        monkeypatch.setattr(config, "get_workspace_client", lambda: _FakeClient("https://fe-vm-ef-demo-workspace.cloud.databricks.com"))
        assert config.get_workspace_name() == "fe-vm-ef-demo-workspace"

    def test_strips_azure_suffix(self, monkeypatch):
        monkeypatch.setattr(config, "get_workspace_client", lambda: _FakeClient("https://adb-1234567890123456.14.azuredatabricks.net"))
        assert config.get_workspace_name() == "adb-1234567890123456.14"

    def test_strips_gcp_suffix(self, monkeypatch):
        monkeypatch.setattr(config, "get_workspace_client", lambda: _FakeClient("https://my-workspace.gcp.databricks.com"))
        assert config.get_workspace_name() == "my-workspace"

    def test_unrecognized_host_returned_as_is(self, monkeypatch):
        monkeypatch.setattr(config, "get_workspace_client", lambda: _FakeClient("https://something-unusual.example.com"))
        assert config.get_workspace_name() == "something-unusual.example.com"

    def test_empty_host_falls_back_to_workspace(self, monkeypatch):
        monkeypatch.setattr(config, "get_workspace_client", lambda: _FakeClient(""))
        assert config.get_workspace_name() == "workspace"

    def test_cached_across_calls(self, monkeypatch):
        calls = []

        def make_client():
            calls.append(1)
            return _FakeClient("https://fe-vm-ef-demo-workspace.cloud.databricks.com")

        monkeypatch.setattr(config, "get_workspace_client", make_client)
        config.get_workspace_name()
        config.get_workspace_name()
        assert len(calls) == 1
