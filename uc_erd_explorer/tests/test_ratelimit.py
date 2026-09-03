"""Unit tests for the in-process rate limiter (server/ratelimit.py). Time is controlled
via a fake clock so window expiry is deterministic and tests never sleep."""
from types import SimpleNamespace

import pytest

from server import ratelimit


@pytest.fixture
def clock(monkeypatch):
    """A movable clock patched over ratelimit.time.time -> {'t': ...}."""
    state = {"t": 1000.0}
    monkeypatch.setattr(ratelimit.time, "time", lambda: state["t"])
    return state


def _req(headers=None, host="1.2.3.4"):
    """Minimal stand-in for a Starlette Request: header .get + .client.host."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, default=None: h.get(k.lower(), default)),
        client=SimpleNamespace(host=host) if host else None,
    )


class TestRateLimiter:
    def test_allows_up_to_limit_then_denies(self, clock):
        rl = ratelimit.RateLimiter(limit=3, window_seconds=60)
        assert [rl.check("k")[0] for _ in range(3)] == [True, True, True]
        allowed, retry_after = rl.check("k")
        assert allowed is False
        assert retry_after >= 1.0

    def test_limit_zero_disables(self, clock):
        rl = ratelimit.RateLimiter(limit=0, window_seconds=60)
        assert all(rl.check("k")[0] for _ in range(100))

    def test_window_expiry_frees_capacity(self, clock):
        rl = ratelimit.RateLimiter(limit=2, window_seconds=60)
        assert rl.check("k")[0] is True
        assert rl.check("k")[0] is True
        assert rl.check("k")[0] is False  # at cap
        clock["t"] += 61  # both hits age out of the window
        assert rl.check("k")[0] is True

    def test_keys_are_independent(self, clock):
        rl = ratelimit.RateLimiter(limit=1, window_seconds=60)
        assert rl.check("a")[0] is True
        assert rl.check("b")[0] is True  # different key, own budget
        assert rl.check("a")[0] is False

    def test_retry_after_shrinks_as_window_advances(self, clock):
        rl = ratelimit.RateLimiter(limit=1, window_seconds=60)
        assert rl.check("k")[0] is True
        _, first = rl.check("k")
        clock["t"] += 30
        _, later = rl.check("k")
        assert later < first


class TestIdentityKey:
    def test_prefers_forwarded_email(self):
        req = _req({"x-forwarded-email": "User@Example.com"}, host="9.9.9.9")
        assert ratelimit.identity_key(req) == "user:user@example.com"

    def test_falls_back_to_first_forwarded_for_hop(self):
        req = _req({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}, host="9.9.9.9")
        assert ratelimit.identity_key(req) == "ip:203.0.113.7"

    def test_falls_back_to_socket_peer(self):
        assert ratelimit.identity_key(_req(host="5.6.7.8")) == "ip:5.6.7.8"

    def test_unknown_when_no_client(self):
        assert ratelimit.identity_key(_req(host=None)) == "ip:unknown"
