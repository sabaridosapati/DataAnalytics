from email.message import EmailMessage

import aiosmtplib


class MailError(Exception):
    pass


async def send_email(settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        raise MailError(str(exc)) from exc
