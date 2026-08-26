"""
llm.py — Foundation Model client for the agentic features (Triage Copilot + Ask Watchtower).

Uses the workspace's serving endpoints via the SDK's OpenAI-compatible client (auth +
base_url handled for us). The model is env-configurable so it's a one-line swap between,
e.g., databricks-claude-sonnet-5 and databricks-glm-5-2 — no code change.
"""

from __future__ import annotations

import os

from .db import w

MODEL = os.environ.get("WT_MODEL", "databricks-claude-sonnet-5")
# Fail fast rather than tie up a worker if the serving endpoint hangs.
_TIMEOUT_SEC = float(os.environ.get("WT_LLM_TIMEOUT_SEC", "60"))

_client = None


def _client_():
    global _client
    if _client is None:
        _client = w.serving_endpoints.get_open_ai_client()
    return _client


def _text(content) -> str:
    """Coalesce a chat message's content to text. Some models return `content` as a list
    of blocks (e.g. [{'type':'text','text':...}] or block objects) rather than a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(p.get("text") or p.get("content") or "")
            else:
                parts.append(getattr(p, "text", "") or "")
        return "".join(parts)
    return str(content)


def chat(system: str, user: str, max_tokens: int = 1200) -> str:
    # Note: no temperature — some models (e.g. Claude reasoning models) reject it.
    resp = _client_().chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        timeout=_TIMEOUT_SEC,
    )
    return _text(resp.choices[0].message.content).strip()
