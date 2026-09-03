"""Unit tests for the transient-retry helper (server/retry.py). No real network -- the
callable is a counter that fails a controlled number of times."""
import pytest

from server import retry


class _FakeErr(Exception):
    """Stand-in for an SDK/HTTP error carrying an error_code and/or status_code."""

    def __init__(self, msg="", error_code=None, status_code=None):
        super().__init__(msg)
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code


class TestIsTransient:
    def test_transient_databricks_error_code(self):
        assert retry.is_transient(_FakeErr(error_code="TEMPORARILY_UNAVAILABLE")) is True

    def test_error_code_match_is_case_insensitive(self):
        assert retry.is_transient(_FakeErr(error_code="too_many_requests")) is True

    def test_non_transient_error_code(self):
        assert retry.is_transient(_FakeErr(error_code="PERMISSION_DENIED")) is False

    def test_transient_http_status(self):
        assert retry.is_transient(_FakeErr(status_code=503)) is True

    def test_client_error_status_is_not_transient(self):
        assert retry.is_transient(_FakeErr(status_code=400)) is False

    def test_status_on_response_attribute(self):
        class WithResponse(Exception):
            response = type("R", (), {"status_code": 429})()

        assert retry.is_transient(WithResponse()) is True

    def test_transport_message_substring(self):
        assert retry.is_transient(Exception("Connection reset by peer")) is True

    def test_wait_timeout_cancel_is_not_retried(self):
        # Our own _execute raises this when a statement exceeds wait_timeout and is
        # cancelled -- retrying would only prolong an already-too-slow query, so it must
        # NOT be classified transient (no bare "timeout" match).
        exc = RuntimeError("Query 'columns' did not succeed (state=CANCELED): no result returned")
        assert retry.is_transient(exc) is False

    def test_plain_error_is_not_transient(self):
        assert retry.is_transient(ValueError("bad input")) is False


class TestRetryTransient:
    def test_returns_on_first_success(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return "ok"

        assert retry.retry_transient(fn, label="t", base_delay=0.0) == "ok"
        assert calls["n"] == 1

    def test_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _FakeErr(status_code=503)
            return "recovered"

        assert retry.retry_transient(fn, label="t", attempts=3, base_delay=0.0) == "recovered"
        assert calls["n"] == 3

    def test_non_transient_raises_immediately(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _FakeErr(error_code="PERMISSION_DENIED")

        with pytest.raises(_FakeErr):
            retry.retry_transient(fn, label="t", attempts=3, base_delay=0.0)
        assert calls["n"] == 1  # no retry on a non-transient error

    def test_exhausts_attempts_then_raises_last(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _FakeErr(status_code=503)

        with pytest.raises(_FakeErr):
            retry.retry_transient(fn, label="t", attempts=3, base_delay=0.0)
        assert calls["n"] == 3
