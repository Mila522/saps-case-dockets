"""SMTP adapter. Never log messages or provider exceptions."""
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings


class MailUnavailable(Exception):
    pass


def send_code(recipient: str, code: str):
    state, _ = _submit(recipient, 'SAPS Case-Docket sign-in verification',
        f'Your verification code is {code}.\n\n'
        'It expires in five minutes and can be used once. Do not share it.\n'
        'If you did not request this code, you can ignore this email.')
    if state != 'ACCEPTED':
        raise MailUnavailable() from None


def case_mail_configured():
    return bool(settings.smtp_host and settings.smtp_use_starttls and settings.email_from
        and settings.smtp_username.get_secret_value() and settings.smtp_password.get_secret_value())


def _submit(recipient, subject, body, identity=None):
    """Shared SMTP transport; return only safe, fixed outcome labels.

    Once DATA may have been sent, transport failures are ambiguous and must not
    be retried automatically. SMTP acceptance never proves inbox delivery.
    """
    if not case_mail_configured():
        return 'BLOCKED', 'SMTP_NOT_CONFIGURED'
    sending = False
    accepted = False
    try:
        message = EmailMessage()
        message['From'] = settings.email_from
        message['To'] = recipient
        message['Subject'] = subject
        if identity:
            message['Message-ID'] = f'<case-{identity}@case-docket.invalid>'
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(settings.smtp_username.get_secret_value(), settings.smtp_password.get_secret_value())
            sending = True
            if smtp.send_message(message):
                return 'REJECTED', 'SMTP_REJECTED'
            accepted = True
    except smtplib.SMTPAuthenticationError:
        return 'BLOCKED', 'SMTP_AUTHENTICATION_FAILED'
    except PermissionError:
        return ('UNKNOWN', 'SEND_OUTCOME_UNKNOWN') if sending else ('BLOCKED', 'SMTP_NETWORK_BLOCKED')
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        return 'REJECTED', 'SMTP_REJECTED'
    except (smtplib.SMTPException, OSError, ValueError):
        if accepted:
            return 'ACCEPTED', 'SMTP_ACCEPTED'
        return ('UNKNOWN', 'SEND_OUTCOME_UNKNOWN') if sending else ('BLOCKED', 'SMTP_CONNECTION_FAILED')
    return 'ACCEPTED', 'SMTP_ACCEPTED'


def send_case_message(recipient, subject, body, identity):
    return _submit(recipient, subject, body, identity)
