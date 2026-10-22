import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tokensaver.memory import RollingMemory, build_request
from tokensaver.preferences import STYLES, response_style_guidance
from tokensaver.cli import main


class ResponsePreferenceTests(unittest.TestCase):
    def test_all_supported_styles_have_non_empty_guidance(self):
        self.assertEqual(STYLES, ("adaptive", "concise", "detailed"))
        for style in STYLES:
            self.assertTrue(response_style_guidance(style).strip())

    def test_style_validation_is_case_insensitive_and_rejects_unknown_values(self):
        self.assertEqual(response_style_guidance(" Concise "), response_style_guidance("concise"))
        with self.assertRaisesRegex(ValueError, "adaptive, concise, detailed"):
            response_style_guidance("aggressive")
        with self.assertRaises(ValueError):
            response_style_guidance(None)

    def test_request_keeps_new_message_whitespace_and_adds_advisory_style(self):
        memory = RollingMemory(project="Toknix")
        message = "Fix this:\n  return 1\n\n"
        prompt = build_request(memory, message, response_style="detailed")
        self.assertIn("[TOKNIX RESPONSE PREFERENCE: detailed]", prompt)
        self.assertIn("Prefer thorough, structured answers", prompt)
        self.assertIn("[NEW MESSAGE]\n" + message, prompt)
        self.assertTrue(prompt.endswith(message))

    def test_cli_request_accepts_concise_style_and_saves_original_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            RollingMemory(project="test").save(path)
            output = io.StringIO()
            with patch.object(sys, "argv", ["toknix", "request", str(path), "Keep exact", "--style", "concise"]), patch("sys.stdout", output):
                self.assertEqual(main(), 0)
            self.assertIn("[TOKNIX RESPONSE PREFERENCE: concise]", output.getvalue())
            self.assertEqual(RollingMemory.load(path).recent_turns[-1]["content"], "Keep exact")


if __name__ == "__main__":
    unittest.main()
