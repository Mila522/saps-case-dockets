import pytest
from app.core.config import settings
from app.modules.authentication import mail
from auth_mailbox import messages, send


@pytest.fixture(autouse=True)
def fake_auth_mail(monkeypatch):
    messages.clear()
    monkeypatch.setattr(mail, 'send_code', send)
    # General business regression fixtures can log in immediately. Rate-limit
    # tests explicitly restore production values and manipulate stored timestamps.
    monkeypatch.setattr(settings, 'email_resend_seconds', 0)
    yield
    messages.clear()
