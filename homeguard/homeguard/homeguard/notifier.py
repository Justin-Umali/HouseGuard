"""Email alerts over SMTP (Gmail app password works out of the box)."""
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from pathlib import Path

from . import config, db


def _recipients(raw: str) -> list[str]:
    return [r.strip() for r in raw.replace(";", ",").split(",") if r.strip()]


def smtp_configured() -> bool:
    return bool(config.SMTP_USER and config.SMTP_PASS)


def send_email(subject: str, body: str, recipients: str, snapshot: Path | None = None) -> tuple[bool, str]:
    to = _recipients(recipients)
    if not to:
        return False, "No recipient email set (Settings page)."
    if not smtp_configured():
        return False, "SMTP_USER / SMTP_PASS are not set in .env."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    if snapshot and snapshot.exists():
        msg.add_attachment(snapshot.read_bytes(), maintype="image", subtype="jpeg", filename=snapshot.name)

    try:
        ctx = ssl.create_default_context()
        if config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=20) as s:
                s.login(config.SMTP_USER, config.SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(config.SMTP_USER, config.SMTP_PASS)
                s.send_message(msg)
        return True, "sent"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def send_alert_async(event_id: int, recipients: str, snapshot: Path | None, detail: str) -> None:
    """Send in a background thread so detection never blocks on the network."""

    def _job():
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        body = (
            "HomeGuard detected a possible intruder.\n\n"
            f"Time: {when}\n"
            f"Details: {detail}\n\n"
            "Open your HomeGuard dashboard to view the live camera and event history."
        )
        ok, info = send_email("HomeGuard ALERT: person detected", body, recipients, snapshot)
        db.mark_emailed(event_id, ok)
        if not ok:
            print(f"[notifier] email failed: {info}")

    threading.Thread(target=_job, daemon=True, name="email").start()
