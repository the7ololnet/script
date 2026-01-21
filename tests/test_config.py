import unittest

from pydantic import ValidationError

from config import SMTPConfig


class TestConfig(unittest.TestCase):
    def test_smtp_config_validation(self) -> None:
        with self.assertRaises(ValidationError):
            SMTPConfig(
                id="1",
                server="",
                port=0,
                user="user",
                pass_="pass",
                sender_email="mail@example.com",
                domain="example.com",
                sender_name="Sender",
                reply_to="reply@example.com",
            )


if __name__ == "__main__":
    unittest.main()
