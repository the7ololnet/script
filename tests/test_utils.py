import random
import unittest

from utils import extract_name_from_email, parse_spintax


class TestUtils(unittest.TestCase):
    def test_extract_name_from_email(self) -> None:
        self.assertEqual(extract_name_from_email("karim.ben88@gmail.com"), "Karim")
        self.assertEqual(extract_name_from_email("john_doe@company.com"), "John")
        self.assertEqual(extract_name_from_email("admin@example.com"), "Admin")

    def test_parse_spintax(self) -> None:
        rng = random.Random(0)
        output = parse_spintax("{A|B|C} {X|Y}", rng=rng)
        self.assertIn(output.split()[0], {"A", "B", "C"})
        self.assertIn(output.split()[1], {"X", "Y"})


if __name__ == "__main__":
    unittest.main()
