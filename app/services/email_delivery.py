"""SMTP delivery with mandatory TLS. Credentials and message bodies are not logged."""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings


class EmailDeliveryUnavailable(RuntimeError):
    pass


def ensure_email_configured() -> None:
    if not all((
        settings.smtp_host.strip(),
        settings.smtp_username.strip(),
        settings.smtp_password.get_secret_value(),
        settings.smtp_from_email,
    )):
        raise EmailDeliveryUnavailable("Password recovery email is not configured.")


def _send_message(message: EmailMessage) -> None:
    ensure_email_configured()
    context = ssl.create_default_context()
    options = {
        "host": settings.smtp_host,
        "port": settings.smtp_port,
        "timeout": settings.smtp_timeout_seconds,
    }

    if settings.smtp_security == "ssl":
        connection = smtplib.SMTP_SSL(**options, context=context)
    else:
        connection = smtplib.SMTP(**options)

    with connection as smtp:
        if settings.smtp_security == "starttls":
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


def _message(recipient: str, subject: str, text: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name, str(settings.smtp_from_email)))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    return message


def send_password_reset_code(email: str, code: str, language: str) -> None:
    if language == "sw":
        subject = "Weka upya nenosiri lako la RespiraCare"
        text = (
            f"Msimbo wako wa kuweka upya nenosiri la RespiraCare ni:\n\n{code}\n\n"
            "Msimbo huu unaisha baada ya dakika 10 na unaweza kutumika mara moja pekee.\n"
            "Usishiriki msimbo huu. Ikiwa hukuomba kubadilisha nenosiri, puuza ujumbe huu.\n"
        )
    else:
        subject = "Reset your RespiraCare password"
        text = (
            f"Your RespiraCare password-reset code is:\n\n{code}\n\n"
            "This code expires after 10 minutes and can only be used once.\n"
            "Do not share this code. If you did not request a reset, ignore this email.\n"
        )
    _send_message(_message(email, subject, text))


def send_password_changed_notice(email: str, language: str) -> None:
    if language == "sw":
        subject = "Nenosiri lako la RespiraCare limebadilishwa"
        text = (
            "Nenosiri lako la RespiraCare limebadilishwa na vipindi vya awali vimefungwa.\n"
            "Ikiwa hukufanya mabadiliko haya, tumia huduma ya kurejesha nenosiri mara moja.\n"
        )
    else:
        subject = "Your RespiraCare password was changed"
        text = (
            "Your RespiraCare password was changed and previous sessions were signed out.\n"
            "If you did not make this change, use password recovery immediately.\n"
        )
    _send_message(_message(email, subject, text))
