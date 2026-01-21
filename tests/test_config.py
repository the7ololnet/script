"""
Tests for configuration validation.
"""

import os
import unittest
from email_sender.config import (
    SMTPConfig, CampaignConfig, AIConfig, AppConfig,
    ConfigurationError, _validate_email, _validate_url, _validate_port
)


class TestSMTPConfig(unittest.TestCase):
    """Tests for SMTP configuration validation."""
    
    def get_valid_config(self):
        """Return a valid SMTP config dict."""
        return {
            "server": "mail.example.com",
            "port": 587,
            "user": "testuser",
            "password": "testpass",
            "sender_email": "sender@example.com",
            "domain": "example.com",
            "sender_name": "Test Sender",
            "reply_to": "reply@example.com",
        }
    
    def test_valid_config(self):
        """Test valid SMTP configuration."""
        config = SMTPConfig(**self.get_valid_config())
        self.assertEqual(config.server, "mail.example.com")
        self.assertEqual(config.port, 587)
    
    def test_missing_server(self):
        """Test validation fails for missing server."""
        params = self.get_valid_config()
        params["server"] = ""
        with self.assertRaises(ConfigurationError):
            SMTPConfig(**params)
    
    def test_missing_password(self):
        """Test validation fails for missing password."""
        params = self.get_valid_config()
        params["password"] = ""
        with self.assertRaises(ConfigurationError):
            SMTPConfig(**params)
    
    def test_invalid_sender_email(self):
        """Test validation fails for invalid sender email."""
        params = self.get_valid_config()
        params["sender_email"] = "not-an-email"
        with self.assertRaises(ConfigurationError):
            SMTPConfig(**params)
    
    def test_invalid_port(self):
        """Test validation fails for invalid port."""
        params = self.get_valid_config()
        params["port"] = 99999
        with self.assertRaises(ConfigurationError):
            SMTPConfig(**params)
    
    def test_invalid_unsub_url(self):
        """Test validation fails for invalid unsubscribe URL."""
        params = self.get_valid_config()
        params["unsub_url"] = "not-a-url"
        with self.assertRaises(ConfigurationError):
            SMTPConfig(**params)
    
    def test_valid_unsub_url(self):
        """Test valid unsubscribe URL passes."""
        params = self.get_valid_config()
        params["unsub_url"] = "https://example.com/unsubscribe"
        config = SMTPConfig(**params)
        self.assertEqual(config.unsub_url, "https://example.com/unsubscribe")


class TestCampaignConfig(unittest.TestCase):
    """Tests for campaign configuration validation."""
    
    def test_valid_warmup_mode(self):
        """Test valid WARMUP mode."""
        config = CampaignConfig(mode="WARMUP")
        self.assertEqual(config.mode, "WARMUP")
    
    def test_valid_offer_mode(self):
        """Test valid OFFER mode."""
        config = CampaignConfig(mode="OFFER")
        self.assertEqual(config.mode, "OFFER")
    
    def test_invalid_mode(self):
        """Test invalid mode raises error."""
        with self.assertRaises(ConfigurationError):
            CampaignConfig(mode="INVALID")
    
    def test_invalid_sleep_range(self):
        """Test invalid sleep range raises error."""
        with self.assertRaises(ConfigurationError):
            CampaignConfig(min_sleep=30, max_sleep=10)
    
    def test_invalid_rate(self):
        """Test invalid rate raises error."""
        with self.assertRaises(ConfigurationError):
            CampaignConfig(rate_per_minute=0)
    
    def test_invalid_rotation(self):
        """Test invalid rotation strategy raises error."""
        with self.assertRaises(ConfigurationError):
            CampaignConfig(server_rotation="invalid")


class TestAIConfig(unittest.TestCase):
    """Tests for AI configuration validation."""
    
    def test_enabled_without_key(self):
        """Test that enabled AI without API key raises error."""
        with self.assertRaises(ConfigurationError):
            AIConfig(enabled=True, api_key="")
    
    def test_enabled_with_key(self):
        """Test that enabled AI with key passes."""
        config = AIConfig(enabled=True, api_key="sk-test-key")
        self.assertTrue(config.enabled)
    
    def test_disabled_without_key(self):
        """Test that disabled AI without key passes."""
        config = AIConfig(enabled=False, api_key="")
        self.assertFalse(config.enabled)


class TestValidators(unittest.TestCase):
    """Tests for individual validators."""
    
    def test_validate_email_valid(self):
        """Test valid email addresses."""
        valid_emails = [
            "test@example.com",
            "user.name@domain.org",
            "user+tag@sub.domain.co.uk",
        ]
        for email in valid_emails:
            result = _validate_email(email, "test")
            self.assertEqual(result, email)
    
    def test_validate_email_invalid(self):
        """Test invalid email addresses."""
        invalid_emails = [
            "not-an-email",
            "@nodomain.com",
            "noat.com",
            "spaces not@allowed.com",
        ]
        for email in invalid_emails:
            with self.assertRaises(ConfigurationError):
                _validate_email(email, "test")
    
    def test_validate_url_valid(self):
        """Test valid URLs."""
        valid_urls = [
            "https://example.com",
            "http://test.org/path",
            "https://sub.domain.co.uk/path?query=1",
        ]
        for url in valid_urls:
            result = _validate_url(url, "test")
            self.assertEqual(result, url)
    
    def test_validate_url_invalid(self):
        """Test invalid URLs."""
        invalid_urls = [
            "not-a-url",
            "ftp://not-http.com",
            "//no-scheme.com",
        ]
        for url in invalid_urls:
            with self.assertRaises(ConfigurationError):
                _validate_url(url, "test")
    
    def test_validate_port_valid(self):
        """Test valid ports."""
        for port in [25, 465, 587, 2525]:
            result = _validate_port(port, "test")
            self.assertEqual(result, port)
    
    def test_validate_port_invalid(self):
        """Test invalid ports."""
        for port in [0, -1, 65536, 100000]:
            with self.assertRaises(ConfigurationError):
                _validate_port(port, "test")


if __name__ == "__main__":
    unittest.main()
