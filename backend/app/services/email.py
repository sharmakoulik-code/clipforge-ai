from email.message import EmailMessage
import smtplib
import ssl

from app.core.config import settings


def send_password_reset_email(recipient: str, token: str) -> None:
    """Deliver a one-use reset link. Credentials are read only from server configuration."""
    if not settings.smtp_host or not settings.smtp_from_email:
        raise RuntimeError("Password reset email transport is not configured")
    message = EmailMessage()
    message["Subject"] = "Reset your Frameforge password"
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = recipient
    reset_url = f"{settings.frontend_url.rstrip('/')}/reset-password?token={token}"
    message.set_content(
        "We received a request to reset your password.\n\n"
        f"Use this one-time link within one hour: {reset_url}\n\n"
        "If you did not request this, you can ignore this message."
    )
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        server.ehlo()
        if settings.smtp_starttls:
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)
