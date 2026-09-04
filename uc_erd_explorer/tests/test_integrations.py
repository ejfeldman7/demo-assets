"""Unit tests for dbxmetagen detection (server/integrations.py). The warehouse query is
mocked via _execute/_rows; no real network."""
import pytest

from server import integrations


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ERD_DBXMETAGEN_LOCATION", raising=False)
    monkeypatch.setattr(integrations, "get_user_cache_key", lambda: "")
    integrations._cache.clear()
    yield
    integrations._cache.clear()


def _fake_rows(rows):
    """Patch _execute to a sentinel and _rows to return the given (catalog, schema, name) rows."""
    def apply(monkeypatch):
        monkeypatch.setattr(integrations, "_execute", lambda *a, **k: object())
        monkeypatch.setattr(integrations, "_rows", lambda _r: rows)
    return apply


class TestDetect:
    def test_present_when_anchor_table_found(self, monkeypatch):
        _fake_rows([
            ["megacorp", "dbxmetagen", "table_knowledge_base"],
            ["megacorp", "dbxmetagen", "fk_predictions"],
        ])(monkeypatch)
        r = integrations.detect_dbxmetagen(["megacorp"])
        assert r["present"] is True
        assert r["location"] == "megacorp.dbxmetagen"
        assert "table_knowledge_base" in r["tables_found"] and "fk_predictions" in r["tables_found"]
        assert r["repo_url"] == integrations.DBXMETAGEN_REPO_URL

    def test_absent_when_no_signature_tables(self, monkeypatch):
        _fake_rows([])(monkeypatch)
        r = integrations.detect_dbxmetagen(["megacorp"])
        assert r["present"] is False
        assert r["location"] is None
        assert r["repo_url"] == integrations.DBXMETAGEN_REPO_URL  # link offered even when absent

    def test_anchor_required_other_tables_alone_do_not_count(self, monkeypatch):
        # fk_predictions without table_knowledge_base isn't treated as a dbxmetagen location.
        _fake_rows([["c", "s", "fk_predictions"]])(monkeypatch)
        assert integrations.detect_dbxmetagen(["c"])["present"] is False

    def test_prefers_most_complete_location(self, monkeypatch):
        _fake_rows([
            ["c", "partial", "table_knowledge_base"],
            ["c", "full", "table_knowledge_base"],
            ["c", "full", "column_knowledge_base"],
            ["c", "full", "fk_predictions"],
        ])(monkeypatch)
        assert integrations.detect_dbxmetagen(["c"])["location"] == "c.full"

    def test_detection_never_raises(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("warehouse down")
        monkeypatch.setattr(integrations, "_execute", boom)
        r = integrations.detect_dbxmetagen(["c"])
        assert r["present"] is False  # error reads as "not present", app unaffected

    def test_result_is_cached(self, monkeypatch):
        calls = {"n": 0}

        def counting_rows(_r):
            calls["n"] += 1
            return [["c", "s", "table_knowledge_base"]]
        monkeypatch.setattr(integrations, "_execute", lambda *a, **k: object())
        monkeypatch.setattr(integrations, "_rows", counting_rows)
        integrations.detect_dbxmetagen(["c"])
        integrations.detect_dbxmetagen(["c"])
        assert calls["n"] == 1  # second call served from cache


class TestFetchFkPredictions:
    def _present(self, monkeypatch, location="megacorp.dbxmetagen"):
        """Force detection to 'present' at a location (so fetch proceeds to the query)."""
        monkeypatch.setattr(integrations, "detect_dbxmetagen",
                            lambda catalogs: {"present": True, "location": location,
                                              "tables_found": [], "repo_url": integrations.DBXMETAGEN_REPO_URL})

    def test_maps_rows_to_overlay_edges(self, monkeypatch):
        self._present(monkeypatch)
        rows = [
            ["megacorp.erp.sales_orders", "customer_id", "megacorp.erp.customers", "customer_id", 0.98, True, "name+type match"],
        ]
        monkeypatch.setattr(integrations, "_execute", lambda *a, **k: object())
        monkeypatch.setattr(integrations, "_rows", lambda _r: rows)
        r = integrations.fetch_fk_predictions(["megacorp"])
        assert r["present"] is True and r["location"] == "megacorp.dbxmetagen"
        e = r["edges"][0]
        assert e["source"] == "megacorp.erp.sales_orders"
        assert e["target"] == "megacorp.erp.customers"
        assert e["fk_columns"] == ["customer_id"] and e["pk_columns"] == ["customer_id"]
        assert e["predicted"] is True and e["is_fk"] is True and e["confidence"] == 0.98

    def test_absent_when_dbxmetagen_not_detected(self, monkeypatch):
        monkeypatch.setattr(integrations, "detect_dbxmetagen",
                            lambda catalogs: {"present": False, "location": None, "tables_found": [], "repo_url": ""})
        r = integrations.fetch_fk_predictions(["megacorp"])
        assert r["present"] is False and r["edges"] == []

    def test_missing_fk_table_degrades_to_no_edges(self, monkeypatch):
        self._present(monkeypatch)

        def boom(*a, **k):
            raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND: fk_predictions")
        monkeypatch.setattr(integrations, "_execute", boom)
        r = integrations.fetch_fk_predictions(["megacorp"])
        assert r["present"] is True and r["edges"] == []  # present, just no predictions yet


class TestExplicitLocation:
    def test_env_location_parsed(self, monkeypatch):
        monkeypatch.setenv("ERD_DBXMETAGEN_LOCATION", " mycat.myschema ")
        assert integrations.get_dbxmetagen_location() == ("mycat", "myschema")

    def test_no_env_location_returns_none(self):
        assert integrations.get_dbxmetagen_location() is None
