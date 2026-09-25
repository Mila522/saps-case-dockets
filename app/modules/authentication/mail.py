"""Authentication-only SMTP adapter. Never log messages or provider exceptions."""
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings


class MailUnavailable(Exception):
    pass


def send_code(recipient: str, code: str):
    if not (settings.smtp_host and settings.smtp_use_starttls and settings.email_from
            and settings.smtp_username.get_secret_value() and settings.smtp_password.get_secret_value()):
        raise MailUnavailable() from None
    try:
        message = EmailMessage()
        message['From'] = settings.email_from
        message['To'] = recipient
        message['Subject'] = 'SAPS Case-Docket sign-in verification'
        message.set_content(f'Your verification code is {code}.\n\n'
            'It expires in five minutes and can be used once. Do not share it.\n'
            'If you did not request this code, you can ignore this email.')
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(settings.smtp_username.get_secret_value(), settings.smtp_password.get_secret_value())
            if smtp.send_message(message):
                raise MailUnavailable()
        # SMTP acceptance is not proof of arrival in an inbox.
    except (smtplib.SMTPException, OSError, ValueError):
        raise MailUnavailable() from None
