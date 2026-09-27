"""Diagnose transport/authentication without sending email or printing secrets."""
import smtplib
import ssl
from app.core.config import settings
from app.modules.authentication.mail import case_mail_configured


def main():
    if not case_mail_configured():
        print('SMTP configuration incomplete; check SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, EMAIL_FROM and SMTP_USE_STARTTLS.')
        return 1
    stage = 'connect'
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=12) as smtp:
            stage = 'ehlo'
            smtp.ehlo()
            stage = 'starttls'
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            stage = 'authenticate'
            smtp.login(settings.smtp_username.get_secret_value(), settings.smtp_password.get_secret_value())
            print('SMTP authentication succeeded. No email was sent.')
            stage = 'quit'
    except (smtplib.SMTPException, OSError, ValueError) as error:
        # Never print exception text, host, sender, username or server responses.
        print(f'SMTP check failed at {stage}; type={type(error).__name__}; '
              f'SMTP status={getattr(error, "smtp_code", None)}; OS status={getattr(error, "winerror", None)}.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
