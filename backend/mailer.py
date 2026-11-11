"""Alert emails, sent through your own mail account's SMTP server.

The server and its password live in .env (SMTP_*), never in the database or a backup; the address to write to and the
on/off switch are settings (`alert_email`, `email_alerts`). Nothing is sent unless both are in place."""
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

log = logging.getLogger("clearyield")

EMAIL = re.compile(r"[^@\s<>\"',;:()\[\]]+@[^@\s<>\"',;:()\[\]]+\.[A-Za-z]{2,}")
APP_URL = "http://localhost:8001/"
TIMEOUT = 30
PLACEHOLDERS = {"", "my_password", "your_app_password", "app_password", "abcdefghijklmnop"}  # the last is .env.example's
SET_UP = "add SMTP_HOST, SMTP_USER and SMTP_PASSWORD to .env (see .env.example), then restart the app"
GMAIL_HINT = ("For Gmail, use an app password (Google Account, Security, 2-Step Verification, App passwords), "
              "not your normal password.")


def config():
    """The mail server from .env, read on every call. `problem` says what's missing; the password never leaves here."""
    host = os.getenv("SMTP_HOST", "").strip()
    user, password = os.getenv("SMTP_USER", "").strip(), os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "").strip() or user
    try:
        port = int(os.getenv("SMTP_PORT", "").strip() or 587)
    except ValueError:
        port = None
    security = os.getenv("SMTP_SECURITY", "").strip().lower() or ("ssl" if port == 465 else "starttls")
    problem = None
    if not host:
        problem = f"Email isn't set up: {SET_UP}."
    elif port is None:
        problem = "SMTP_PORT in .env must be a number, such as 587."
    elif security not in ("starttls", "ssl", "none"):
        problem = "SMTP_SECURITY in .env must be starttls, ssl or none."
    elif user and password.strip() in PLACEHOLDERS:
        problem = "SMTP_PASSWORD in .env isn't filled in."
    elif not EMAIL.fullmatch(sender):
        problem = "Set SMTP_FROM (or SMTP_USER) in .env to the address emails come from."
    return {"host": host, "port": port, "user": user, "password": password, "sender": sender, "security": security, "problem": problem}


def status():
    """For the Settings page: whether email can be sent, and through which server. No user name or password."""
    c = config()
    return {"ready": c["problem"] is None, "problem": c["problem"], "server": f"{c['host']}:{c['port']}" if c["host"] else None}


def _context():
    # The bundle main.py writes trusts what Windows trusts, so antivirus that re-signs secure mail connections passes too
    return ssl.create_default_context(cafile=os.getenv("SSL_CERT_FILE") or None)


def send(to, subject, text):
    """Send one plain-text email. Raises ValueError with a sentence fit to show on the page."""
    c = config()
    if c["problem"]:
        raise ValueError(c["problem"])
    if not isinstance(to, str) or not EMAIL.fullmatch(to.strip()):
        raise ValueError("Enter a valid email address to send to.")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"ClearYield <{c['sender']}>", to.strip(), subject
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain=c["sender"].split("@")[1])
    msg.set_content(text)
    try:
        if c["security"] == "ssl":
            server = smtplib.SMTP_SSL(c["host"], c["port"], timeout=TIMEOUT, context=_context())
        else:
            server = smtplib.SMTP(c["host"], c["port"], timeout=TIMEOUT)
        with server:
            if c["security"] == "starttls":
                server.starttls(context=_context())
            if c["user"]:
                server.login(c["user"], c["password"])
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise ValueError(f"The mail server ({c['host']}) refused the user name or password. {GMAIL_HINT}") from None
    except (smtplib.SMTPException, OSError) as e:  # OSError: no connection, timeout, certificate
        raise ValueError(f"Couldn't send through {c['host']}:{c['port']}: {e}") from None


def alert_text(fired):
    """(subject, body) for alerts that fired in one check."""
    subject = f"ClearYield alert: {fired[0]['message']}" if len(fired) == 1 else f"ClearYield: {len(fired)} alerts triggered"
    lines = [f"- {a['message']}\n  {APP_URL}#research/{a['ticker']}" for a in fired]
    body = ("Triggered just now:\n\n" + "\n\n".join(lines) + "\n\n"
            "Each alert fires once. Set it again on the Journal & Alerts page if you want to keep watching.\n"
            "They also stay in the dashboard digest until you dismiss them.\n\n"
            "Sent by ClearYield on your computer, because email alerts are on in Settings. "
            "Decision support, not financial advice.\n")
    return subject, body


def send_alerts(fired, settings):
    """Email what fired, when that's turned on. Returns None when nothing was meant to go, else {"sent"} or {"error"};
    a failure is logged and never stops the check: the alerts are still in the digest."""
    to = settings.get("alert_email")
    if not fired or not settings.get("email_alerts") or not to:
        return None
    try:
        send(to, *alert_text(fired))
    except ValueError as e:
        log.warning("alert email not sent: %s", e)
        return {"error": str(e)}
    log.info("alert email sent for %d alert(s)", len(fired))
    return {"sent": len(fired)}
