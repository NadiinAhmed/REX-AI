"""Optional e-mail distribution of APPROVED documents (SMTP). Disabled unless SMTP_HOST and SMTP_FROM are set.

Privacy: recipient addresses are never written to logs; only counts are.
"""
from __future__ import annotations

import re
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage

from src.config import Settings
from src.utils.logging_config import get_logger

logger = get_logger("email")
MAX_RECIPIENTS = 10
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]{2,}$")


class EmailError(Exception):
    """E-mail could not be sent (message is safe to show)."""


@dataclass
class Attachment:
    filename: str
    data: bytes
    mime: str = "application/pdf"


def parse_recipients(raw: str | list[str]) -> list[str]:
    items = re.split(r"[,;\s]+", raw) if isinstance(raw, str) else list(raw)
    addresses = list(dict.fromkeys(a.strip().lower() for a in items if a and a.strip()))
    if not addresses:
        raise EmailError("Enter at least one recipient e-mail address.")
    if len(addresses) > MAX_RECIPIENTS:
        raise EmailError(f"At most {MAX_RECIPIENTS} recipients per message.")
    bad = [a for a in addresses if not _ADDRESS.match(a)]
    if bad:
        raise EmailError("One or more addresses are not valid e-mail addresses.")
    return addresses


class EmailService:
    def __init__(self, settings: Settings, transport: Callable[[EmailMessage], None] | None = None) -> None:
        self._s = settings
        self._transport = transport          # tests inject a fake; production uses SMTP

    @property
    def enabled(self) -> bool:
        return self._s.email_enabled

    def send(self, recipients: str | list[str], subject: str, body: str, attachment: Attachment | None = None) -> int:
        if not self.enabled:
            raise EmailError("E-mail is not configured: set SMTP_HOST and SMTP_FROM in .env.")
        to = parse_recipients(recipients)
        if "\n" in subject or "\r" in subject:
            raise EmailError("The subject cannot contain line breaks.")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self._s.smtp_from, ", ".join(to), subject[:200]
        msg.set_content(body)
        if attachment:
            maintype, _, subtype = attachment.mime.partition("/")
            msg.add_attachment(attachment.data, maintype=maintype, subtype=subtype, filename=attachment.filename)
        try:
            (self._transport or self._smtp_send)(msg)
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("E-mail sending failed: %s", type(exc).__name__)
            raise EmailError("The e-mail could not be sent. Check the SMTP settings and try again.") from exc
        logger.info("E-mail sent to %d recipient(s)", len(to))
        return len(to)

    def _smtp_send(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port, timeout=20) as smtp:
            if self._s.smtp_starttls:
                smtp.starttls()
            if self._s.smtp_user and self._s.smtp_password:
                smtp.login(self._s.smtp_user, self._s.smtp_password)
            smtp.send_message(msg)
