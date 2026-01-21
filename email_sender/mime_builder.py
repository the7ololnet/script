"""
MIME message builder module.

Creates properly formatted email messages with compliance headers.
"""

import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional
from urllib.parse import urlparse

from .config import SMTPConfig
from .utils import parse_spintax


# Company footer template for OFFER mode
OFFER_FOOTER_TEXT = """

---
{company_name}
{physical_address}

To unsubscribe: {unsub_url}
You received this email because you signed up for our list.
"""

OFFER_FOOTER_HTML = """
<hr style="border: none; border-top: 1px solid #ccc; margin: 20px 0;">
<p style="font-size: 12px; color: #666;">
<strong>{company_name}</strong><br>
{physical_address}<br><br>
<a href="{unsub_url}">Unsubscribe</a> | 
You received this email because you signed up for our list.
</p>
"""


def generate_message_id(domain: str) -> str:
    """
    Generate a standards-compliant Message-ID.
    
    Uses Python's make_msgid for proper formatting while ensuring
    the domain is aligned with the sender.
    
    Args:
        domain: Sending domain to include in Message-ID
        
    Returns:
        RFC 2822 compliant Message-ID
    """
    return make_msgid(domain=domain)


def create_mime_message(
    to_email: str,
    to_name: str,
    subject: str,
    body_text: str,
    body_html: str,
    smtp_config: SMTPConfig,
    mode: str = "WARMUP",
    company_name: str = "[Company Name]",
    physical_address: str = "[Physical Address]"
) -> MIMEMultipart:
    """
    Create a properly formatted MIME message with compliance headers.
    
    Args:
        to_email: Recipient email address
        to_name: Recipient display name
        subject: Email subject line
        body_text: Plain text body
        body_html: HTML body
        smtp_config: SMTP configuration with sender details
        mode: Campaign mode (WARMUP or OFFER)
        company_name: Company name for footer (OFFER mode)
        physical_address: Physical address for footer (OFFER mode)
        
    Returns:
        MIMEMultipart message ready for sending
    """
    # Parse spintax in sender name
    sender_name = parse_spintax(smtp_config.sender_name)
    
    # Create multipart/alternative message
    msg = MIMEMultipart('alternative')
    
    # ==========================================================
    # Required Headers
    # ==========================================================
    
    # Message-ID: Unique, domain-aligned identifier
    msg['Message-ID'] = generate_message_id(smtp_config.domain)
    
    # From: Properly formatted sender
    msg['From'] = formataddr((sender_name, smtp_config.sender_email))
    
    # To: Recipient with name
    msg['To'] = formataddr((to_name, to_email))
    
    # Subject: Encoded for Unicode support
    msg['Subject'] = Header(subject, 'utf-8')
    
    # Date: RFC 2822 format
    msg['Date'] = formatdate(localtime=True)
    
    # Reply-To: Where replies should go
    msg['Reply-To'] = smtp_config.reply_to
    
    # MIME-Version: Required for multipart
    msg['MIME-Version'] = '1.0'
    
    # ==========================================================
    # List Headers (for mailing list identification)
    # ==========================================================
    
    if smtp_config.list_id:
        # Parse spintax in list_id if present
        list_id = parse_spintax(smtp_config.list_id)
        msg['List-Id'] = list_id
    
    # ==========================================================
    # Unsubscribe Headers (RFC 8058 compliance)
    # ==========================================================
    
    # Only include unsubscribe headers for OFFER mode or if explicitly configured
    if mode == "OFFER" and smtp_config.unsub_url:
        # Validate URL
        try:
            parsed = urlparse(smtp_config.unsub_url)
            if parsed.scheme and parsed.netloc:
                # List-Unsubscribe with both mailto and HTTPS options
                unsub_email = f"unsubscribe@{smtp_config.domain}"
                msg['List-Unsubscribe'] = f"<mailto:{unsub_email}?subject=unsubscribe>, <{smtp_config.unsub_url}>"
                
                # List-Unsubscribe-Post for one-click unsubscribe (RFC 8058)
                msg['List-Unsubscribe-Post'] = "List-Unsubscribe=One-Click"
        except Exception:
            pass  # Skip if URL is invalid
    
    # ==========================================================
    # Priority Headers (normal priority)
    # ==========================================================
    
    msg['X-Priority'] = '3'  # Normal
    msg['X-MSMail-Priority'] = 'Normal'
    
    # ==========================================================
    # Message Body
    # ==========================================================
    
    # Add compliance footer for OFFER mode
    if mode == "OFFER" and smtp_config.unsub_url:
        footer_text = OFFER_FOOTER_TEXT.format(
            company_name=company_name,
            physical_address=physical_address,
            unsub_url=smtp_config.unsub_url
        )
        footer_html = OFFER_FOOTER_HTML.format(
            company_name=company_name,
            physical_address=physical_address,
            unsub_url=smtp_config.unsub_url
        )
        body_text = body_text + footer_text
        body_html = body_html + footer_html
    
    # Plain text version (attached first - fallback)
    text_part = MIMEText(body_text, 'plain', 'utf-8')
    msg.attach(text_part)
    
    # HTML version (attached second - preferred)
    html_part = MIMEText(body_html, 'html', 'utf-8')
    msg.attach(html_part)
    
    return msg


def get_envelope_from(smtp_config: SMTPConfig) -> str:
    """
    Get the envelope-from (MAIL FROM) address.
    
    This should align with the sender_email for proper SPF alignment.
    
    Args:
        smtp_config: SMTP configuration
        
    Returns:
        Email address for MAIL FROM
    """
    return smtp_config.sender_email


def format_for_display(msg: MIMEMultipart) -> str:
    """
    Format message headers for display (dry-run mode).
    
    Args:
        msg: MIME message
        
    Returns:
        Formatted string of headers
    """
    lines = []
    headers_to_show = [
        'Message-ID', 'From', 'To', 'Subject', 'Date',
        'Reply-To', 'List-Id', 'List-Unsubscribe'
    ]
    
    for header in headers_to_show:
        if header in msg:
            lines.append(f"{header}: {msg[header]}")
    
    return "\n".join(lines)
