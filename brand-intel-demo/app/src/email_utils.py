"""
SMTP email utilities for Brand Manager Forecasting Intelligence.

Loads SMTP credentials from Databricks Secrets (scope ``smtp-scope``) and
sends HTML emails with optional PDF attachments. Split out of ``report_runner``
so the email transport is isolated and reusable.
"""

import base64
import logging
import smtplib
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SMTP Configuration (loaded from Databricks Secrets)
# ---------------------------------------------------------------------------
SECRET_SCOPE = "smtp-scope"
_smtp_config: dict = {}


def _secret_str(val) -> str:
    """Decode a secret value — the SDK may return a base64-encoded string or bytes."""
    if isinstance(val, bytes):
        raw = val.decode()
    else:
        raw = str(val)
    # Databricks secrets are base64-encoded — try to decode, fall back to raw
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:
        return raw


def load_smtp_config() -> dict:
    """Load SMTP credentials from Databricks secrets. Cached after first success."""
    global _smtp_config
    if _smtp_config:
        return _smtp_config
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()

        _smtp_config = {
            "host": _secret_str(w.secrets.get_secret(SECRET_SCOPE, "smtp-host").value),
            "port": int(_secret_str(w.secrets.get_secret(SECRET_SCOPE, "smtp-port").value)),
            "user": _secret_str(w.secrets.get_secret(SECRET_SCOPE, "smtp-user").value),
            "password": _secret_str(w.secrets.get_secret(SECRET_SCOPE, "smtp-password").value),
        }
        _smtp_config["from"] = _smtp_config["user"]
        logger.info("SMTP config loaded from Databricks secrets (scope=%s)", SECRET_SCOPE)
    except Exception as e:
        logger.warning("Failed to load SMTP secrets: %s", e)
        # Don't cache failures — allow retry on next call
        return {}
    return _smtp_config


def smtp_available() -> bool:
    """True if SMTP is configured (host + user + password present)."""
    cfg = load_smtp_config()
    return bool(cfg.get("host") and cfg.get("user") and cfg.get("password"))


def send_email(
    recipients: list[str],
    subject: str,
    html_body: str,
    pdf_bytes: Optional[bytes] = None,
    pdf_filename: str = "report.pdf",
) -> bool:
    """Send an HTML email with optional PDF attachment via SMTP."""
    cfg = load_smtp_config()
    if not cfg:
        logger.warning("SMTP not configured — email not sent. Add secrets to scope '%s'.", SECRET_SCOPE)
        return False

    if not recipients:
        logger.warning("No recipients specified — email not sent.")
        return False

    msg = MIMEMultipart("mixed")
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = Header(subject, "utf-8")

    # HTML body
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # PDF attachment
    if pdf_bytes:
        pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
        pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
        msg.attach(pdf_part)

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
            server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        logger.info("Email sent to %s", recipients)
        return True
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False
