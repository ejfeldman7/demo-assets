"""
llm.py — bounded Foundation Model classifier for `semantic` pattern rules.

A semantic rule carries a natural-language intent ("queries that scan raw PII", "cross-region
reads", …). Rather than one model call per query (expensive at poll cadence), we BATCH: one call
per semantic rule per scan classifies a capped list of candidate queries and returns the ids that
match. If no semantic rules exist, the model is never called.

Uses the SDK's OpenAI-compatible client against a serving endpoint (auth/base_url handled). The
poller runs on jobs compute, which can reach serving endpoints. Model via WT_MODEL.
"""

from __future__ import annotations

import json
import logging
import os

from databricks.sdk import WorkspaceClient

log = logging.getLogger("poller")

MODEL = os.environ.get("WT_MODEL", "databricks-claude-sonnet-5")
_TIMEOUT_SEC = float(os.environ.get("WT_LLM_TIMEOUT_SEC", "60"))
# Cap candidates per scan so a busy window can't blow up token cost.
SEMANTIC_CAP = int(os.environ.get("WT_SEMANTIC_CAP", "40"))

_client = None


def _client_(w: WorkspaceClient):
    global _client
    if _client is None:
        _client = w.serving_endpoints.get_open_ai_client()
    return _client


def classify_matches(w: WorkspaceClient, intent: str, items: list[tuple[str, str]]) -> set[str]:
    """Return the ids from `items` (list of (id, query_text)) whose query matches `intent`.

    One batched chat call. Truncates each query and the candidate list to bound tokens. On any
    error (timeout, parse), returns an empty set — semantic rules fail safe (no finding) rather
    than blocking the poll.
    """
    items = [(i, (t or "").strip()) for i, t in items if (t or "").strip()]
    if len(items) > SEMANTIC_CAP:
        # Don't silently drop coverage — surface that some queries went unclassified this scan.
        log.warning("semantic classify capped at %d of %d candidate queries (raise WT_SEMANTIC_CAP "
                    "or shorten the window if this recurs)", SEMANTIC_CAP, len(items))
        items = items[:SEMANTIC_CAP]
    if not items:
        return set()
    catalogue = "\n".join(f"[{i}] {t[:600]}" for i, t in items)
    system = (
        "You are a SQL governance classifier. Given an INTENT and a numbered list of SQL queries, "
        "return ONLY the ids of queries that match the intent. Respond with strict JSON: "
        '{\"matches\": [\"id1\", \"id2\"]}. No prose.')
    user = f"INTENT: {intent}\n\nQUERIES:\n{catalogue}"
    try:
        resp = _client_(w).chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=800,
            timeout=_TIMEOUT_SEC,
        )
        content = _text(resp.choices[0].message.content)
        ids = json.loads(_json_slice(content)).get("matches", [])
        valid = {i for i, _ in items}
        return {str(i) for i in ids if str(i) in valid}
    except Exception:
        return set()


def _text(content) -> str:
    """Coalesce a chat message's content to text (some models return a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p if isinstance(p, str) else (p.get("text") or "" if isinstance(p, dict) else getattr(p, "text", "") or "")
            for p in content)
    return str(content or "")


def _json_slice(s: str) -> str:
    """Extract the first {...} object from a response that may be fenced or padded."""
    a, b = s.find("{"), s.rfind("}")
    return s[a:b + 1] if a != -1 and b > a else "{}"
