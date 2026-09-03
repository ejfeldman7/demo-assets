"""Bounded retry-with-backoff for transient failures on external calls.

The app's warehouse metadata queries and Genie REST calls occasionally hit *transient*
conditions -- a SQL warehouse spinning up from cold, a momentary 429/503 from the control
plane, a dropped connection -- that succeed on a second try a fraction of a second later.
Without a retry those surface to the user as a hard 500. This wraps a synchronous callable
in a small, jittered exponential backoff that retries ONLY errors that look transient and
re-raises everything else (a malformed query, a 4xx auth/scope error, a genuine bug)
immediately, so a real problem is never masked or silently hammered.

Synchronous on purpose: the callers run this inside a worker thread (the graph query
threadpool, or asyncio.to_thread for Genie), so the time.sleep() between attempts never
blocks the FastAPI event loop.

Retries are only ever applied to calls that are safe to repeat -- idempotent reads
(SELECTs) and, narrowly, Genie's poll GET -- never blindly to a state-changing POST; the
callers decide what to wrap. See server/graph.py (_execute) and server/routes/genie.py.
"""
import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger("erd")

T = TypeVar("T")

# Databricks error_code values (DatabricksError.error_code) that mean "try again", as
# opposed to a client error (bad SQL, missing grant) that will fail identically on retry.
_TRANSIENT_ERROR_CODES = frozenset({
    "TEMPORARILY_UNAVAILABLE",
    "INTERNAL_ERROR",
    "TOO_MANY_REQUESTS",
    "REQUEST_LIMIT_EXCEEDED",
    "DEADLINE_EXCEEDED",
    "ABORTED",
    "SERVICE_UNDER_MAINTENANCE",
    "BAD_GATEWAY",
})
_TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})
# Last-resort message sniffing for lower-level (requests/urllib3) errors that don't carry a
# Databricks error_code or an HTTP status. Kept to phrases that are unambiguously transport
# blips -- deliberately NOT the generic word "timeout" alone, so it never swallows our own
# "query did not succeed (state=CANCELED)" wait-timeout, which retrying would only prolong.
_TRANSIENT_SUBSTRINGS = (
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "connection refused",
    "read timed out",
    "connection timed out",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "remote end closed connection",
    "eof occurred",
    "max retries exceeded",
)


def is_transient(exc: BaseException) -> bool:
    """True if `exc` looks like a momentary condition worth retrying (vs. a deterministic
    failure that will recur). Checks, in order: a Databricks error_code, an HTTP status
    (on the exception or its .response), then a narrow transport-error message match."""
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code.upper() in _TRANSIENT_ERROR_CODES:
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int) and status in _TRANSIENT_HTTP:
        return True
    text = str(exc).lower()
    return any(s in text for s in _TRANSIENT_SUBSTRINGS)


def retry_transient(
    fn: Callable[[], T],
    *,
    label: str,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 4.0,
) -> T:
    """Call `fn`, retrying up to `attempts` times on a transient error with jittered
    exponential backoff (base_delay, doubling, capped at max_delay). A non-transient error
    -- or the final attempt -- is re-raised immediately. `label` names the operation in the
    retry log line."""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- classified by is_transient, else re-raised
            last_exc = exc
            if attempt >= attempts or not is_transient(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter to avoid synchronized retries
            logger.warning(
                "transient failure on %s (attempt %d/%d): %s -- retrying in %.2fs",
                label, attempt, attempts, exc, delay,
            )
            time.sleep(delay)
    # Unreachable: the loop returns or raises on every path. Here for the type checker.
    raise last_exc  # type: ignore[misc]
