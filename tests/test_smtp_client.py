import socket
import smtplib
import unittest

from smtp_client import classify_smtp_error


class TestSmtpClient(unittest.TestCase):
    def test_retry_classifier(self) -> None:
        transient = smtplib.SMTPResponseException(421, b"Try again")
        category, retryable, code, _ = classify_smtp_error(transient)
        self.assertEqual(category, "transient")
        self.assertTrue(retryable)
        self.assertEqual(code, 421)

        auth_error = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        category, retryable, code, _ = classify_smtp_error(auth_error)
        self.assertEqual(category, "auth")
        self.assertFalse(retryable)
        self.assertEqual(code, 535)

        timeout_error = socket.timeout("timeout")
        category, retryable, code, _ = classify_smtp_error(timeout_error)
        self.assertEqual(category, "timeout")
        self.assertTrue(retryable)
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
