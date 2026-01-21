"""
Tests for MIME message builder.
"""

import unittest
from email.mime.multipart import MIMEMultipart
from email_sender.config import SMTPConfig
from email_sender.mime_builder import (
    create_mime_message,
    generate_message_id,
    get_envelope_from,
    format_for_display
)


class TestGenerateMessageId(unittest.TestCase):
    """Tests for Message-ID generation."""
    
    def test_format(self):
        """Test Message-ID format."""
        msg_id = generate_message_id("example.com")
        self.assertTrue(msg_id.startswith("<"))
        self.assertTrue(msg_id.endswith(">"))
        self.assertIn("@", msg_id)
    
    def test_uniqueness(self):
        """Test that Message-IDs are unique."""
        ids = set()
        for _ in range(100):
            msg_id = generate_message_id("example.com")
            self.assertNotIn(msg_id, ids)
            ids.add(msg_id)


class TestCreateMimeMessage(unittest.TestCase):
    """Tests for MIME message creation."""
    
    def get_smtp_config(self):
        """Return a valid SMTP config for testing."""
        return SMTPConfig(
            server="mail.example.com",
            port=587,
            user="testuser",
            password="testpass",
            sender_email="sender@example.com",
            domain="example.com",
            sender_name="Test Sender",
            reply_to="reply@example.com",
            list_id="Test List <list.example.com>",
            unsub_url="https://example.com/unsubscribe",
        )
    
    def test_basic_message(self):
        """Test basic message creation."""
        smtp_config = self.get_smtp_config()
        
        msg = create_mime_message(
            to_email="recipient@test.com",
            to_name="Recipient",
            subject="Test Subject",
            body_text="Plain text body",
            body_html="<p>HTML body</p>",
            smtp_config=smtp_config,
            mode="WARMUP"
        )
        
        self.assertIsInstance(msg, MIMEMultipart)
        self.assertEqual(msg["Subject"], "Test Subject")
        self.assertIn("recipient@test.com", msg["To"])
        self.assertIn("sender@example.com", msg["From"])
    
    def test_required_headers(self):
        """Test that required headers are present."""
        smtp_config = self.get_smtp_config()
        
        msg = create_mime_message(
            to_email="recipient@test.com",
            to_name="Recipient",
            subject="Test Subject",
            body_text="Plain text body",
            body_html="<p>HTML body</p>",
            smtp_config=smtp_config,
            mode="WARMUP"
        )
        
        required_headers = ["Message-ID", "From", "To", "Subject", "Date", "Reply-To"]
        for header in required_headers:
            self.assertIn(header, msg, f"Missing header: {header}")
    
    def test_offer_mode_unsubscribe_headers(self):
        """Test that OFFER mode includes unsubscribe headers."""
        smtp_config = self.get_smtp_config()
        
        msg = create_mime_message(
            to_email="recipient@test.com",
            to_name="Recipient",
            subject="Test Subject",
            body_text="Plain text body",
            body_html="<p>HTML body</p>",
            smtp_config=smtp_config,
            mode="OFFER"
        )
        
        self.assertIn("List-Unsubscribe", msg)
        self.assertIn("List-Unsubscribe-Post", msg)
        self.assertEqual(msg["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
    
    def test_warmup_mode_no_unsubscribe(self):
        """Test that WARMUP mode doesn't include unsubscribe headers."""
        smtp_config = self.get_smtp_config()
        
        msg = create_mime_message(
            to_email="recipient@test.com",
            to_name="Recipient",
            subject="Test Subject",
            body_text="Plain text body",
            body_html="<p>HTML body</p>",
            smtp_config=smtp_config,
            mode="WARMUP"
        )
        
        self.assertNotIn("List-Unsubscribe", msg)
    
    def test_offer_mode_footer(self):
        """Test that OFFER mode includes footer."""
        smtp_config = self.get_smtp_config()
        
        msg = create_mime_message(
            to_email="recipient@test.com",
            to_name="Recipient",
            subject="Test Subject",
            body_text="Plain text body",
            body_html="<p>HTML body</p>",
            smtp_config=smtp_config,
            mode="OFFER",
            company_name="Test Company",
            physical_address="123 Test St"
        )
        
        # Check that message has parts
        parts = list(msg.walk())
        
        # Find text and HTML parts
        text_content = None
        html_content = None
        for part in parts:
            content_type = part.get_content_type()
            if content_type == "text/plain":
                text_content = part.get_payload(decode=True).decode('utf-8')
            elif content_type == "text/html":
                html_content = part.get_payload(decode=True).decode('utf-8')
        
        # Verify footer content
        self.assertIn("Test Company", text_content)
        self.assertIn("123 Test St", text_content)
        self.assertIn("Unsubscribe", html_content)
    
    def test_multipart_structure(self):
        """Test that message has correct multipart structure."""
        smtp_config = self.get_smtp_config()
        
        msg = create_mime_message(
            to_email="recipient@test.com",
            to_name="Recipient",
            subject="Test Subject",
            body_text="Plain text body",
            body_html="<p>HTML body</p>",
            smtp_config=smtp_config,
            mode="WARMUP"
        )
        
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        
        # Should have text and HTML parts
        payloads = msg.get_payload()
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0].get_content_type(), "text/plain")
        self.assertEqual(payloads[1].get_content_type(), "text/html")
    
    def test_spintax_in_sender_name(self):
        """Test that spintax is parsed in sender name."""
        smtp_config = SMTPConfig(
            server="mail.example.com",
            port=587,
            user="testuser",
            password="testpass",
            sender_email="sender@example.com",
            domain="example.com",
            sender_name="{Alice|Bob|Charlie}",  # Spintax
            reply_to="reply@example.com",
        )
        
        # Create multiple messages and verify variety
        names_seen = set()
        for _ in range(20):
            msg = create_mime_message(
                to_email="recipient@test.com",
                to_name="Recipient",
                subject="Test",
                body_text="Body",
                body_html="<p>Body</p>",
                smtp_config=smtp_config,
                mode="WARMUP"
            )
            from_header = msg["From"]
            names_seen.add(from_header)
        
        # Should see some variation
        self.assertGreater(len(names_seen), 1)


class TestGetEnvelopeFrom(unittest.TestCase):
    """Tests for envelope-from generation."""
    
    def test_envelope_from(self):
        """Test that envelope-from matches sender email."""
        smtp_config = SMTPConfig(
            server="mail.example.com",
            port=587,
            user="testuser",
            password="testpass",
            sender_email="sender@example.com",
            domain="example.com",
            sender_name="Test Sender",
            reply_to="reply@example.com",
        )
        
        envelope_from = get_envelope_from(smtp_config)
        self.assertEqual(envelope_from, "sender@example.com")


class TestFormatForDisplay(unittest.TestCase):
    """Tests for message display formatting."""
    
    def test_format_display(self):
        """Test formatting message for display."""
        smtp_config = SMTPConfig(
            server="mail.example.com",
            port=587,
            user="testuser",
            password="testpass",
            sender_email="sender@example.com",
            domain="example.com",
            sender_name="Test Sender",
            reply_to="reply@example.com",
        )
        
        msg = create_mime_message(
            to_email="recipient@test.com",
            to_name="Recipient",
            subject="Test Subject",
            body_text="Body",
            body_html="<p>Body</p>",
            smtp_config=smtp_config,
            mode="WARMUP"
        )
        
        display = format_for_display(msg)
        self.assertIn("Message-ID:", display)
        self.assertIn("From:", display)
        self.assertIn("To:", display)
        self.assertIn("Subject:", display)


if __name__ == "__main__":
    unittest.main()
