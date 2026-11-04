import ssl
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.services import email_delivery


@pytest.fixture
def smtp_settings(monkeypatch):
    for key, value in {
        "smtp_host": "smtp.example.com", "smtp_port": 587,
        "smtp_username": "sender@example.com", "smtp_password": SecretStr("synthetic-app-password"),
        "smtp_from_email": "sender@example.com", "smtp_security": "starttls",
    }.items():
        monkeypatch.setattr(settings, key, value)


def test_starttls_is_required_before_login_and_delivery(smtp_settings, monkeypatch):
    connection = MagicMock()
    smtp = connection.__enter__.return_value
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", factory)
    email_delivery.send_password_reset_code("recipient@example.com", "123456", "en")
    assert [call[0] for call in smtp.mock_calls] == ["ehlo", "starttls", "ehlo", "login", "send_message"]
    assert smtp.starttls.call_args.kwargs["context"].check_hostname
    message = smtp.send_message.call_args.args[0]
    assert message["To"] == "recipient@example.com"
    assert "123456" in message.get_content()
    assert "123456" not in message["Subject"]


def test_implicit_tls_is_supported(smtp_settings, monkeypatch):
    monkeypatch.setattr(settings, "smtp_security", "ssl")
    monkeypatch.setattr(settings, "smtp_port", 465)
    connection = MagicMock()
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(email_delivery.smtplib, "SMTP_SSL", factory)
    email_delivery.send_password_reset_code("recipient@example.com", "123456", "en")
    smtp = connection.__enter__.return_value
    smtp.starttls.assert_not_called()
    smtp.login.assert_called_once()
    assert factory.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED


def test_tls_failure_never_falls_back_to_sending_credentials(smtp_settings, monkeypatch):
    connection = MagicMock()
    smtp = connection.__enter__.return_value
    smtp.starttls.side_effect = ssl.SSLError("synthetic TLS failure")
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", MagicMock(return_value=connection))
    with pytest.raises(ssl.SSLError):
        email_delivery.send_password_reset_code("recipient@example.com", "123456", "en")
    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()


def test_missing_credentials_disable_recovery(smtp_settings, monkeypatch):
    monkeypatch.setattr(settings, "smtp_password", SecretStr(""))
    with pytest.raises(email_delivery.EmailDeliveryUnavailable):
        email_delivery.ensure_email_configured()
