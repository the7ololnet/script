import unittest

from mime_builder import create_mime_message


class TestMimeBuilder(unittest.TestCase):
    def test_warmup_no_marketing_headers(self) -> None:
        message = create_mime_message(
            to_email="user@example.com",
            to_name="User",
            subject="Hello",
            body_text="Hi",
            body_html="<p>Hi</p>",
            domain="example.com",
            from_name="Sender",
            from_email="sender@example.com",
            reply_to="reply@example.com",
            list_id="List <list.example.com>",
            unsub_url="https://example.com/unsub",
            mode="WARMUP",
        )
        self.assertNotIn("List-Unsubscribe", message)
        self.assertNotIn("List-ID", message)

    def test_offer_unsubscribe_headers(self) -> None:
        message = create_mime_message(
            to_email="user@example.com",
            to_name="User",
            subject="Offer",
            body_text="Hi",
            body_html="<p>Hi</p>",
            domain="example.com",
            from_name="Sender",
            from_email="sender@example.com",
            reply_to="reply@example.com",
            list_id="List <list.example.com>",
            unsub_url="https://example.com/unsub",
            mode="OFFER",
        )
        self.assertIn("List-Unsubscribe", message)
        self.assertIn("List-ID", message)


if __name__ == "__main__":
    unittest.main()
