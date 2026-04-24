import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from app.config import settings


class EmailError(Exception):
    pass


def send_greeting_email(
    to_email: str,
    subject: str,
    text: str,
    image_bytes: Optional[bytes] = None,
) -> None:
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_password and settings.smtp_from):
        raise EmailError("SMTP не настроен. Заполните SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM.")

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(
        f"""<html><body>
        <p style="font-size:15px;line-height:1.5;white-space:pre-wrap;">{_html_escape(text)}</p>
        {'<img src="cid:card" style="max-width:500px;border-radius:12px;" />' if image_bytes else ''}
        </body></html>""",
        subtype="html",
    )
    if image_bytes:
        msg.get_payload()[1].add_related(image_bytes, maintype="image", subtype="jpeg", cid="card")

    try:
        ctx = ssl.create_default_context()
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ctx, timeout=20) as s:
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
    except Exception as e:
        raise EmailError(f"Не удалось отправить письмо: {e}")


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
