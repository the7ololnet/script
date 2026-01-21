import tempfile
import unittest

from state import StateStore


class TestState(unittest.TestCase):
    def test_idempotency(self) -> None:
        with tempfile.NamedTemporaryFile() as temp:
            state = StateStore(temp.name)
            state.initialize()
            state.ensure_recipients(["a@example.com", "b@example.com"])
            state.mark_sent("a@example.com")

            pending = state.get_recipients_to_send()
            self.assertIn("b@example.com", pending)
            self.assertNotIn("a@example.com", pending)

            state.mark_suppressed("b@example.com", "manual")
            pending_after = state.get_recipients_to_send()
            self.assertNotIn("b@example.com", pending_after)
            state.close()


if __name__ == "__main__":
    unittest.main()
