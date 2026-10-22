import unittest
from tokensaver.compressor import compress_text, estimate_tokens


class CompressorTests(unittest.TestCase):
    def test_duplicate_long_prose_removed_but_unique_content_preserved(self):
        line = "This is a sufficiently long repeated prose line."
        result = compress_text(f"{line}\n{line}\nKeep this constraint in the final version.")
        self.assertEqual(result.text.count(line), 1)
        self.assertIn("Keep this constraint in the final version.", result.text)

    def test_whitespace_normalized_outside_fences(self):
        result = compress_text("hello   world\n\n\nnext\tline")
        self.assertEqual(result.text, "hello world\n\nnext line")

    def test_preserves_indentation_and_fenced_code_exactly(self):
        source = "  key:   value\n  nested:\n    item:  true\n\n~~~python\ndef f():\n    return  1\n    return  1\n}\n~~~"
        result = compress_text(source)
        self.assertIn("  key:   value", result.text)
        self.assertIn("    item:  true", result.text)
        self.assertIn("    return  1\n    return  1", result.text)
        self.assertIn("\n}\n~~~", result.text)

    def test_blank_lines_inside_fences_are_preserved(self):
        source = "~~~python\nfirst\n\n\nsecond\n~~~"
        self.assertEqual(compress_text(source).text, source)

    def test_short_duplicate_code_lines_survive_outside_fence(self):
        result = compress_text("}\n}\nreturn\nreturn\nend\nend")
        self.assertEqual(result.text, "}\n}\nreturn\nreturn\nend\nend")

    def test_duplicate_lines_inside_fences_survive(self):
        result = compress_text("~~~\nlong repeated source line that is longer than twenty chars\nlong repeated source line that is longer than twenty chars\n~~~")
        self.assertEqual(result.text.count("long repeated source line"), 2)

    def test_keep_duplicates_option(self):
        self.assertEqual(compress_text("same\nsame", deduplicate_lines=False).text, "same\nsame")

    def test_empty_estimate(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_persian_and_cjk_estimates_are_not_len_divided_by_four(self):
        english = "a" * 100
        persian = "ب" * 100
        cjk = "漢" * 100
        self.assertGreater(estimate_tokens(persian), estimate_tokens(english))
        self.assertGreater(estimate_tokens(cjk), estimate_tokens(english))


if __name__ == "__main__":
    unittest.main()
