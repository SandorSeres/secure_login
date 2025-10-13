from email.message import EmailMessage
import smtplib
from .config import settings

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
        print("[email_utils] email send failed:", e)
        return False

