"""
mailer.py — SMTP/SendGrid email sending for Watchtower automations.

Config lives in the `watchtower` Databricks secret scope (keys: smtp_host, smtp_port,
smtp_user, smtp_password, smtp_from). If the scope isn't populated the sender reports
"not configured" and the caller keeps the action as a draft — so nothing is sent until
a credential is dropped in. The app SP has READ on the scope.
"""

from __future__ import annotations

import base64
import os
import smtplib
import ssl
from email.message import EmailMessage

from .db import w

SCOPE = os.environ.get("WT_SMTP_SCOPE", "watchtower")


def _secret(key: str) -> str | None:
    try:
        r = w.secrets.get_secret(scope=SCOPE, key=key)
        return base64.b64decode(r.value).decode("utf-8")
    except Exception:
        return None


def smtp_configured() -> bool:
    return bool(_secret("smtp_host"))


def send_email(to: str | list[str] | None, subject: str, body: str) -> tuple[bool, str]:
    host = _secret("smtp_host")
    if not host:
        return False, "SMTP not configured — set smtp_* keys in the 'watchtower' secret scope"
    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = [r for r in recipients if r]
    if not recipients:
        return False, "no recipients (add emails to the distribution list)"
    port = int(_secret("smtp_port") or 587)
    user = _secret("smtp_user")
    password = _secret("smtp_password")
    sender = _secret("smtp_from") or user or "watchtower@localhost"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        _smtp_send(host, port, user, password, msg)
        return True, f"sent to {len(recipients)} recipient(s): {', '.join(recipients)}"
    except Exception as exc:
        return False, f"send failed: {exc}"


def _smtp_send(host: str, port: int, user, password, msg: EmailMessage) -> None:
    """Send via implicit TLS (465) or STARTTLS (587/others)."""
    ctx = ssl.create_default_context()
    server = (smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
              if port == 465 else smtplib.SMTP(host, port, timeout=20))
    with server as s:
        if port != 465:
            s.starttls(context=ctx)
        if user and password:
            s.login(user, password)
        s.send_message(msg)
