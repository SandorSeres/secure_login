import logging
from email.message import EmailMessage
import smtplib
from .config import settings

_log = logging.getLogger(__name__)

def send_reset_email(to_email: str, token: str) -> bool:
    try:
        reset_link = f"{settings.APP_HOST.rstrip('/')}/reset-password?token={token}"

        msg = EmailMessage()
        msg["Subject"] = "Jelszó visszaállítás"
        msg["From"]    = settings.SMTP_FROM
        msg["To"]      = to_email            # <-- most tényleg e-mail

        # Simpla szöveg (MailHog jól mutatja)
        body = (
            "Jelszó visszaállítás kérése érkezett.\n\n"
            f"Ha te voltál: kattints ide: {reset_link}\n"
            "Ha nem te voltál: hagyd figyelmen kívül."
        )
        msg.set_content(body)

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            if settings.SMTP_STARTTLS:
                server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)

        return True
    except Exception as e:
        # A ciphertext/SMTP hiba soha nem kerül a kliens felé; a token sem.
        _log.warning("password reset email delivery failed: %s", type(e).__name__)
        return False

