from __future__ import annotations

from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional

from utils import is_valid_url, parse_spintax


def create_mime_message(
    to_email: str,
    to_name: str,
    subject: str,
    body_text: str,
    body_html: str,
    domain: str,
    from_name: str,
    from_email: str,
    reply_to: Optional[str],
    list_id: Optional[str],
    unsub_url: Optional[str],
    mode: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = formataddr((to_name, to_email))
    msg["Subject"] = Header(subject, "utf-8")
    msg["Date"] = formatdate(localtime=True)

    if reply_to:
        msg["Reply-To"] = reply_to

    if mode == "OFFER":
        if list_id:
            msg["List-ID"] = parse_spintax(list_id)
        if unsub_url and is_valid_url(unsub_url):
            msg["List-Unsubscribe"] = f"<{unsub_url}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg
