import unittest
from tokensaver.compressor import compress_text, estimate_tokens


class CompressorTests(unittest.TestCase):
    def test_duplicate_lines_removed_but_unique_content_preserved(self):
        result = compress_text("Goal: build app\nGoal: build app\nKeep this constraint\n")
        self.assertEqual(result.text, "Goal: build app\nKeep this constraint")
        self.assertIn("Keep this constraint", result.text)

    def test_whitespace_normalized(self):
        result = compress_text("  hello   world\r\n\r\n\r\nnext\tline  ")
        self.assertEqual(result.text, "hello world\n\nnext line")

    def test_keep_duplicates_option(self):
        self.assertEqual(compress_text("same\nsame", deduplicate_lines=False).text, "same\nsame")

    def test_empty_estimate(self):
        self.assertEqual(estimate_tokens(""), 0)


if __name__ == "__main__":
    unittest.main()
