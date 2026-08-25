"""
mailer.py — SMTP send for the poller's auto-send path (critical findings).

Config is read ONCE per poll (load_config) from the `watchtower` secret scope, then passed
to send() — avoids per-send secret-manager round-trips. Mirrors app/server/mailer.py.
Supports implicit TLS (port 465) and STARTTLS (587/others).
"""

from __future__ import annotations

import base64
import os
import smtplib
import ssl
from email.message import EmailMessage

from databricks.sdk import WorkspaceClient

SCOPE = os.environ.get("WT_SMTP_SCOPE", "watchtower")


def load_config(w: WorkspaceClient) -> dict | None:
    """Read all SMTP settings once. Returns None if not configured (no smtp_host)."""
    def g(key):
        try:
            return base64.b64decode(w.secrets.get_secret(scope=SCOPE, key=key).value).decode("utf-8")
        except Exception:
            return None
    host = g("smtp_host")
    if not host:
        return None
    return {"host": host, "port": int(g("smtp_port") or 587), "user": g("smtp_user"),
            "password": g("smtp_password"), "from": g("smtp_from") or g("smtp_user") or "watchtower@localhost"}


def send(cfg: dict, to_list: list[str], subject: str, body: str) -> tuple[bool, str]:
    to = [t for t in (to_list or []) if t]
    if not to:
        return False, "no recipients"
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    try:
        server = (smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20, context=ctx)
                  if cfg["port"] == 465 else smtplib.SMTP(cfg["host"], cfg["port"], timeout=20))
        with server as s:
            if cfg["port"] != 465:
                s.starttls(context=ctx)
            if cfg["user"] and cfg["password"]:
                s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        return True, f"sent to {len(to)} recipient(s)"
    except Exception as exc:
        return False, f"send failed: {exc}"
