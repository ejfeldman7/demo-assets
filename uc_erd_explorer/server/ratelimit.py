"""Lightweight in-process rate limiting for the expensive endpoints.

Every /api/graph and /api/schema-tree load fans out several warehouse statements, and every
/api/genie/ask spends a Genie + warehouse call, so an accidental flood (a tight client
retry loop, a refresh-happy tab) or a deliberate one could rack up real compute. This caps
requests per identity within a rolling window and returns 429 (+ Retry-After) past the cap.

Identity: the logged-in user's forwarded email when present (Databricks Apps forward it in
both auth modes), else the client IP -- taken from the first hop of x-forwarded-for, since
Apps front the container with a proxy, falling back to the socket peer. Read straight from
the request headers here rather than from the contextvars server/routes/graph.py sets, so
the limiter doesn't depend on dependency-ordering to have an identity.

Scope: in-process only, no Redis. A Databricks App runs as a single container/process, so a
per-process counter effectively covers the whole app; if this is ever scaled to multiple
replicas the limit becomes per-replica (document, or move to a shared store then). Windows
are a rolling count of recent hit timestamps per key (a deque), swept on read; idle keys
are purged opportunistically so the map can't grow without bound.
"""
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request

# Above this many tracked identities, drop the ones whose window is currently empty on the
# next check -- keeps a long-lived process from accumulating a deque per IP seen forever.
_MAX_TRACKED_KEYS = 4096


class RateLimiter:
    """Rolling-window request counter. `limit` requests per `window_seconds` per key;
    a limit <= 0 disables it (check() always allows). Thread-safe: the graph routes touch
    it from the event loop while queries run in a worker threadpool."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Record a hit for `key` and report (allowed, retry_after_seconds). When denied,
        retry_after is how long until the oldest in-window hit ages out (>= 1s)."""
        if self.limit <= 0:
            return True, 0.0
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            if len(self._hits) > _MAX_TRACKED_KEYS:
                self._sweep(cutoff)
            dq = self._hits[key]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.limit:
                return False, max(1.0, dq[0] + self.window - now)
            dq.append(now)
            return True, 0.0

    def _sweep(self, cutoff: float) -> None:
        """Purge keys with no hits left in the window. Caller holds the lock."""
        for key in [k for k, dq in self._hits.items() if not dq or dq[-1] <= cutoff]:
            del self._hits[key]


def identity_key(request: Request) -> str:
    """Rate-limit bucket for this request: the forwarded user email if present, else the
    client IP (first x-forwarded-for hop, else the socket peer)."""
    email = request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-user")
    if email and email.strip():
        return f"user:{email.strip().lower()}"
    xff = request.headers.get("x-forwarded-for")
    if xff and xff.strip():
        return f"ip:{xff.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _limit_from_env(var: str, default: int) -> int:
    """Per-minute request cap from an env var; non-numeric/absent falls back to `default`,
    an explicit 0 disables the limit."""
    raw = os.environ.get(var)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


# One limiter for the warehouse-backed graph endpoints, a tighter one for Genie (each ask
# spends a Genie call). Per-minute caps, configurable; generous defaults tuned to "one
# interactive user clicking around" -- a real person never approaches them, but a runaway
# loop is capped. Set the env var to 0 to disable.
_graph_limiter = RateLimiter(_limit_from_env("ERD_RATE_LIMIT_PER_MIN", 120), window_seconds=60.0)
_genie_limiter = RateLimiter(_limit_from_env("ERD_GENIE_RATE_LIMIT_PER_MIN", 20), window_seconds=60.0)


def _enforce(limiter: RateLimiter, request: Request) -> None:
    allowed, retry_after = limiter.check(identity_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded -- too many requests in a short window. Please slow down.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


async def graph_rate_limit(request: Request) -> None:
    """FastAPI dependency: rate-limit the warehouse-backed graph endpoints."""
    _enforce(_graph_limiter, request)


async def genie_rate_limit(request: Request) -> None:
    """FastAPI dependency: rate-limit the Genie ask endpoint (tighter -- each call spends
    a Genie + warehouse round-trip)."""
    _enforce(_genie_limiter, request)
