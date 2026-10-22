"""Sends the shop's emails over SMTP; without SMTP_HOST it only logs them."""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger("example.mail")


class Mailer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.outbox: list[EmailMessage] = []   # what was sent, when only logging

    def send(self, to: str, subject: str, body: str) -> None:
        s = self.settings
        message = EmailMessage()
        message["From"] = s.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        if not s.smtp_host:
            self.outbox.append(message)
            log.info("Email to %s (no SMTP_HOST, so not sent): %s\n%s", to, subject, body)
            return

        context = ssl.create_default_context()
        if s.smtp_port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
        with server:
            if s.smtp_port != 465 and s.smtp_starttls:
                server.starttls(context=context)
            if s.smtp_username:
                server.login(s.smtp_username, s.smtp_password)
            server.send_message(message)
        log.info("Email to %s: %s", to, subject)
