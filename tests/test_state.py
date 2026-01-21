"""
Tests for state management.
"""

import os
import tempfile
import unittest
from email_sender.state import StateStore, RecipientStatus


class TestStateStore(unittest.TestCase):
    """Tests for SQLite state store."""
    
    def setUp(self):
        """Create a temporary database for each test."""
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_file.close()
        self.store = StateStore(self.temp_file.name)
    
    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.temp_file.name)
    
    def test_add_recipients(self):
        """Test adding recipients."""
        emails = ["test1@example.com", "test2@example.com"]
        added = self.store.add_recipients(emails)
        self.assertEqual(added, 2)
    
    def test_add_recipients_deduplication(self):
        """Test that duplicate recipients are not added."""
        emails = ["test@example.com", "TEST@example.com"]
        added = self.store.add_recipients(emails)
        self.assertEqual(added, 1)  # Only one should be added
    
    def test_add_recipients_idempotent(self):
        """Test that adding same recipients twice is idempotent."""
        emails = ["test@example.com"]
        self.store.add_recipients(emails)
        added = self.store.add_recipients(emails)
        self.assertEqual(added, 0)  # Already exists
    
    def test_get_pending_recipients(self):
        """Test getting pending recipients."""
        emails = ["test1@example.com", "test2@example.com"]
        self.store.add_recipients(emails)
        
        pending = self.store.get_pending_recipients()
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[0].status, RecipientStatus.PENDING)
    
    def test_get_pending_with_limit(self):
        """Test getting pending recipients with limit."""
        emails = [f"test{i}@example.com" for i in range(10)]
        self.store.add_recipients(emails)
        
        pending = self.store.get_pending_recipients(limit=5)
        self.assertEqual(len(pending), 5)
    
    def test_update_recipient_status(self):
        """Test updating recipient status."""
        self.store.add_recipients(["test@example.com"])
        
        self.store.update_recipient_status(
            "test@example.com",
            RecipientStatus.SENT
        )
        
        status = self.store.get_recipient_status("test@example.com")
        self.assertEqual(status, RecipientStatus.SENT)
    
    def test_update_recipient_status_with_error(self):
        """Test updating recipient status with error."""
        self.store.add_recipients(["test@example.com"])
        
        self.store.update_recipient_status(
            "test@example.com",
            RecipientStatus.FAILED,
            error="Connection refused"
        )
        
        status = self.store.get_recipient_status("test@example.com")
        self.assertEqual(status, RecipientStatus.FAILED)
    
    def test_record_send(self):
        """Test recording a send attempt."""
        self.store.add_recipients(["test@example.com"])
        
        message_uuid = self.store.record_send(
            email="test@example.com",
            smtp_server_id="server_0",
            mode="WARMUP",
            result="success",
            subject="Test Subject"
        )
        
        self.assertIsNotNone(message_uuid)
        self.assertEqual(len(message_uuid), 36)  # UUID format
    
    def test_suppression(self):
        """Test suppression list functionality."""
        # Add to suppression
        self.store.add_to_suppression("spam@example.com", "hard_bounce")
        
        # Check suppression
        self.assertTrue(self.store.is_suppressed("spam@example.com"))
        self.assertFalse(self.store.is_suppressed("clean@example.com"))
    
    def test_suppression_case_insensitive(self):
        """Test that suppression check is case-insensitive."""
        self.store.add_to_suppression("SPAM@Example.com", "hard_bounce")
        self.assertTrue(self.store.is_suppressed("spam@example.com"))
    
    def test_load_suppression_file(self):
        """Test loading suppression list from file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("suppress1@example.com\n")
            f.write("suppress2@example.com\n")
            f.name
        
        try:
            added = self.store.load_suppression_file(f.name)
            self.assertEqual(added, 2)
            self.assertTrue(self.store.is_suppressed("suppress1@example.com"))
        finally:
            os.unlink(f.name)
    
    def test_cache_content(self):
        """Test content caching."""
        self.store.cache_content(
            email="test@example.com",
            mode="WARMUP",
            subject="Test Subject",
            body_text="Plain text",
            body_html="<p>HTML</p>"
        )
        
        cached = self.store.get_cached_content("test@example.com", "WARMUP")
        self.assertIsNotNone(cached)
        self.assertEqual(cached['subject'], "Test Subject")
        self.assertEqual(cached['body_text'], "Plain text")
        self.assertEqual(cached['body_html'], "<p>HTML</p>")
    
    def test_cache_content_mode_specific(self):
        """Test that cache is mode-specific."""
        self.store.cache_content(
            email="test@example.com",
            mode="WARMUP",
            subject="Warmup Subject",
            body_text="Warmup text",
            body_html="<p>Warmup</p>"
        )
        
        # Different mode should not find cache
        cached = self.store.get_cached_content("test@example.com", "OFFER")
        self.assertIsNone(cached)
    
    def test_get_statistics(self):
        """Test getting campaign statistics."""
        # Add and process some recipients
        self.store.add_recipients(["sent@test.com", "failed@test.com", "pending@test.com"])
        
        self.store.update_recipient_status("sent@test.com", RecipientStatus.SENT)
        self.store.update_recipient_status("failed@test.com", RecipientStatus.FAILED)
        
        self.store.record_send("sent@test.com", "server_0", "WARMUP", "success")
        self.store.record_send("failed@test.com", "server_0", "WARMUP", "failed", error="Test error")
        
        stats = self.store.get_statistics()
        
        self.assertEqual(stats['sent'], 1)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['pending'], 1)
        self.assertEqual(stats['total_sends'], 2)
    
    def test_idempotent_sending(self):
        """Test idempotent sending (skip already sent)."""
        self.store.add_recipients(["test@example.com"])
        
        # First send
        self.store.update_recipient_status("test@example.com", RecipientStatus.SENT)
        
        # Should not appear in pending
        pending = self.store.get_pending_recipients()
        emails = [r.email for r in pending]
        self.assertNotIn("test@example.com", emails)


class TestStateStoreErrorSummary(unittest.TestCase):
    """Tests for error summary functionality."""
    
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_file.close()
        self.store = StateStore(self.temp_file.name)
    
    def tearDown(self):
        os.unlink(self.temp_file.name)
    
    def test_error_summary(self):
        """Test error summary aggregation."""
        self.store.add_recipients(["test1@test.com", "test2@test.com", "test3@test.com"])
        
        # Record failures with same error
        self.store.record_send("test1@test.com", "server_0", "WARMUP", "failed", error="Connection refused")
        self.store.record_send("test2@test.com", "server_0", "WARMUP", "failed", error="Connection refused")
        self.store.record_send("test3@test.com", "server_0", "WARMUP", "failed", error="Auth failed")
        
        summary = self.store.get_error_summary()
        
        # Should be sorted by count
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[0][0], "Connection refused")
        self.assertEqual(summary[0][1], 2)


if __name__ == "__main__":
    unittest.main()
