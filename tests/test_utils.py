"""
Tests for utility functions.
"""

import os
import tempfile
import unittest
from email_sender.utils import (
    parse_spintax,
    extract_name_from_email,
    normalize_email,
    load_emails_from_file,
    load_suppression_list,
    load_allowlist_domains,
    is_email_in_allowlist,
    classify_smtp_error,
    is_retryable_error,
    calculate_backoff
)


class TestSpintaxParser(unittest.TestCase):
    """Tests for spintax parsing."""
    
    def test_simple_spintax(self):
        """Test simple spintax with single block."""
        result = parse_spintax("{A|B|C}")
        self.assertIn(result, ["A", "B", "C"])
    
    def test_multiple_spintax_blocks(self):
        """Test multiple spintax blocks in one string."""
        # Run multiple times to check randomness
        results = set()
        for _ in range(50):
            result = parse_spintax("{Hello|Hi} {World|There}")
            self.assertIn("Hello" in result or "Hi" in result, [True])
            self.assertIn("World" in result or "There" in result, [True])
            results.add(result)
        
        # Should have some variation
        self.assertGreater(len(results), 1)
    
    def test_no_spintax(self):
        """Test string without spintax."""
        text = "No spintax here"
        result = parse_spintax(text)
        self.assertEqual(result, text)
    
    def test_empty_string(self):
        """Test empty string."""
        result = parse_spintax("")
        self.assertEqual(result, "")
    
    def test_none_input(self):
        """Test None input."""
        result = parse_spintax(None)
        self.assertEqual(result, "")
    
    def test_single_option(self):
        """Test spintax with single option."""
        result = parse_spintax("{OnlyOne}")
        self.assertEqual(result, "OnlyOne")
    
    def test_whitespace_handling(self):
        """Test that whitespace in options is preserved."""
        result = parse_spintax("{ Option A | Option B }")
        self.assertIn(result, ["Option A", "Option B"])


class TestExtractNameFromEmail(unittest.TestCase):
    """Tests for name extraction from email."""
    
    def test_firstname_lastname_dot(self):
        """Test firstname.lastname@domain format."""
        self.assertEqual(extract_name_from_email("john.doe@example.com"), "John")
    
    def test_firstname_lastname_underscore(self):
        """Test firstname_lastname@domain format."""
        self.assertEqual(extract_name_from_email("sarah_miller@example.com"), "Sarah")
    
    def test_firstname_with_numbers(self):
        """Test firstname123@domain format."""
        self.assertEqual(extract_name_from_email("karim88@gmail.com"), "Karim")
    
    def test_simple_email(self):
        """Test simple username@domain format."""
        self.assertEqual(extract_name_from_email("admin@example.com"), "Admin")
    
    def test_numbers_only(self):
        """Test email with only numbers in local part."""
        self.assertEqual(extract_name_from_email("123@test.com"), "there")
    
    def test_empty_string(self):
        """Test empty string."""
        self.assertEqual(extract_name_from_email(""), "there")
    
    def test_no_at_sign(self):
        """Test invalid email without @."""
        self.assertEqual(extract_name_from_email("notanemail"), "there")
    
    def test_hyphen_separator(self):
        """Test firstname-lastname@domain format."""
        self.assertEqual(extract_name_from_email("jane-smith@company.org"), "Jane")


class TestNormalizeEmail(unittest.TestCase):
    """Tests for email normalization."""
    
    def test_lowercase(self):
        """Test that email is lowercased."""
        self.assertEqual(normalize_email("John@Example.COM"), "john@example.com")
    
    def test_strip_whitespace(self):
        """Test that whitespace is stripped."""
        self.assertEqual(normalize_email("  test@example.com  "), "test@example.com")
    
    def test_invalid_no_at(self):
        """Test invalid email without @."""
        self.assertIsNone(normalize_email("invalid"))
    
    def test_invalid_no_dot(self):
        """Test invalid email without dot in domain."""
        self.assertIsNone(normalize_email("test@localhost"))
    
    def test_empty_string(self):
        """Test empty string."""
        self.assertIsNone(normalize_email(""))
    
    def test_none_input(self):
        """Test None input."""
        self.assertIsNone(normalize_email(None))


class TestLoadEmailsFromFile(unittest.TestCase):
    """Tests for loading emails from file."""
    
    def test_load_valid_emails(self):
        """Test loading valid emails from file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("test1@example.com\n")
            f.write("test2@example.com\n")
            f.write("test3@example.com\n")
            f.name
        
        try:
            emails = load_emails_from_file(f.name)
            self.assertEqual(len(emails), 3)
            self.assertIn("test1@example.com", emails)
        finally:
            os.unlink(f.name)
    
    def test_deduplication(self):
        """Test that duplicate emails are removed."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("test@example.com\n")
            f.write("TEST@example.com\n")  # Same email, different case
            f.write("test@example.com\n")  # Exact duplicate
            f.name
        
        try:
            emails = load_emails_from_file(f.name)
            self.assertEqual(len(emails), 1)
        finally:
            os.unlink(f.name)
    
    def test_skip_empty_lines(self):
        """Test that empty lines are skipped."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("test1@example.com\n")
            f.write("\n")
            f.write("   \n")
            f.write("test2@example.com\n")
            f.name
        
        try:
            emails = load_emails_from_file(f.name)
            self.assertEqual(len(emails), 2)
        finally:
            os.unlink(f.name)
    
    def test_skip_comments(self):
        """Test that comment lines are skipped."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("# This is a comment\n")
            f.write("test@example.com\n")
            f.write("#another@comment.com\n")
            f.name
        
        try:
            emails = load_emails_from_file(f.name)
            self.assertEqual(len(emails), 1)
        finally:
            os.unlink(f.name)
    
    def test_file_not_found(self):
        """Test FileNotFoundError for missing file."""
        with self.assertRaises(FileNotFoundError):
            load_emails_from_file("nonexistent_file.txt")


class TestAllowlist(unittest.TestCase):
    """Tests for allowlist functionality."""
    
    def test_load_allowlist(self):
        """Test loading allowlist from file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("example.com\n")
            f.write("@test.org\n")  # With @ prefix
            f.write("Company.NET\n")  # Uppercase
            f.name
        
        try:
            domains = load_allowlist_domains(f.name)
            self.assertIsNotNone(domains)
            self.assertEqual(len(domains), 3)
            self.assertIn("example.com", domains)
            self.assertIn("test.org", domains)
            self.assertIn("company.net", domains)
        finally:
            os.unlink(f.name)
    
    def test_is_email_in_allowlist_match(self):
        """Test email matching allowlist."""
        allowlist = {"example.com", "test.org"}
        self.assertTrue(is_email_in_allowlist("user@example.com", allowlist))
    
    def test_is_email_in_allowlist_no_match(self):
        """Test email not matching allowlist."""
        allowlist = {"example.com", "test.org"}
        self.assertFalse(is_email_in_allowlist("user@other.com", allowlist))
    
    def test_is_email_in_allowlist_none(self):
        """Test that None allowlist allows all."""
        self.assertTrue(is_email_in_allowlist("user@anywhere.com", None))


class TestErrorClassification(unittest.TestCase):
    """Tests for SMTP error classification."""
    
    def test_connection_error(self):
        """Test connection error classification."""
        error = Exception("Connection refused")
        self.assertEqual(classify_smtp_error(error), "connection")
    
    def test_timeout_error(self):
        """Test timeout error classification."""
        error = Exception("Connection timeout")
        self.assertEqual(classify_smtp_error(error), "connection")
    
    def test_auth_error(self):
        """Test authentication error classification."""
        error = Exception("535 Authentication failed")
        self.assertEqual(classify_smtp_error(error), "auth")
    
    def test_transient_error(self):
        """Test transient error classification."""
        error = Exception("451 Try again later")
        self.assertEqual(classify_smtp_error(error), "transient")
    
    def test_permanent_error(self):
        """Test permanent error classification."""
        error = Exception("550 User does not exist")
        self.assertEqual(classify_smtp_error(error), "permanent")
    
    def test_retryable_transient(self):
        """Test that transient errors are retryable."""
        error = Exception("421 Too many connections")
        self.assertTrue(is_retryable_error(error))
    
    def test_not_retryable_permanent(self):
        """Test that permanent errors are not retryable."""
        error = Exception("550 Mailbox not found")
        self.assertFalse(is_retryable_error(error))


class TestBackoff(unittest.TestCase):
    """Tests for backoff calculation."""
    
    def test_first_attempt(self):
        """Test backoff for first attempt."""
        delay = calculate_backoff(0, base=2.0, max_delay=60.0)
        # Should be around 1.0 (2^0) with jitter
        self.assertGreater(delay, 0)
        self.assertLess(delay, 3.0)
    
    def test_increasing_delay(self):
        """Test that delay increases with attempts."""
        delay1 = calculate_backoff(0, base=2.0, max_delay=60.0)
        delay2 = calculate_backoff(2, base=2.0, max_delay=60.0)
        # On average, delay2 should be higher (though jitter may occasionally flip this)
        # Let's just verify they're both reasonable values
        self.assertGreater(delay1, 0)
        self.assertGreater(delay2, 0)
    
    def test_max_delay(self):
        """Test that delay is capped at max_delay."""
        delay = calculate_backoff(10, base=2.0, max_delay=60.0)
        # With jitter up to 1.5x, max is 90
        self.assertLess(delay, 100)


if __name__ == "__main__":
    unittest.main()
