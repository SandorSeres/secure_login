# app/email_utils.py

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

from .config import settings


def _build_reset_link(token: str) -> str:
    """
    Teljes visszaállítási link építése a .env-ben lévő APP_HOST alapján.
    Példa: APP_HOST=http://localhost  -> http://localhost/reset-password?token=...
            APP_HOST=https://app.mydomain.com -> https://app.mydomain.com/reset-password?token=...
    """
    host = (settings.APP_HOST or "http://localhost").rstrip("/")
    return f"{host}/reset-password?token={quote(token)}"


def send_reset_email(to_email: str, token: str) -> bool:
    """
    Jelszó-visszaállító e-mail küldése.
    Beállítások (config.py / .env):
      - SMTP_HOST, SMTP_PORT
      - SMTP_USER, SMTP_PASSWORD (opcionális)
      - SMTP_SSL (bool)  -> közvetlen SSL (465 tipikusan)
      - SMTP_STARTTLS (bool) -> STARTTLS (587 tipikusan)
      - MAIL_FROM
      - APP_HOST -> a link hostname-je
    Visszatér: True ha sikeres, False ha hiba történt.
    """
    reset_link = _build_reset_link(token)

    subject = "Jelszó visszaállítás"
    body = (
        "Jelszó visszaállítás kérése érkezett.\n\n"
        f"Ha te voltál: kattints ide: {reset_link}\n"
        "Ha nem te voltál: hagyd figyelmen kívül."
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email
    # UTF-8 tartalom, plain text
    msg.set_content(body, subtype="plain", charset="utf-8")

    try:
        context = ssl.create_default_context()

        if getattr(settings, "SMTP_SSL", False):
            # Pl. 465
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context, timeout=15) as server:
                _maybe_login(server)
                server.send_message(msg)
        else:
            # Pl. 1025 (MailHog), vagy 587 STARTTLS
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.ehlo()
                if getattr(settings, "SMTP_STARTTLS", False):
                    server.starttls(context=context)
                    server.ehlo()
                _maybe_login(server)
                server.send_message(msg)

        print("[email_utils] reset mail sent to:", to_email)
        return True

    except Exception as e:
        print("[email_utils] email send failed:", repr(e))
        return False


def _maybe_login(server: smtplib.SMTP) -> None:
    """
    Ha meg van adva SMTP_USER / SMTP_PASSWORD, akkor belép.
    Ha nincs, (pl. MailHog) egyszerűen kihagyja.
    """
    user = getattr(settings, "SMTP_USER", None)
    pwd = getattr(settings, "SMTP_PASSWORD", None)
    if user and pwd:
        server.login(user, pwd)

