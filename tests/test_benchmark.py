import unittest
from unittest.mock import patch

from benchmarks.run import _live_usage, run


class BenchmarkTests(unittest.TestCase):
    def test_fixtures_preserve_at_least_95_percent_of_needles(self):
        report, failed = run(
            cached_ratio=0.1,
            input_price=1e-6,
            cacheable_fraction=0.7,
            live=False,
            minimum_retention=95.0,
        )
        self.assertFalse(failed, report)
        self.assertIn("After if cache breaks", report)
        self.assertIn("Needle retention", report)
        self.assertIn("mixed-persian-english", report)


    def test_live_mode_refuses_to_send_api_key_over_remote_http(self):
        with patch("benchmarks.run.urllib.request.urlopen") as urlopen:
            usage = _live_usage(
                {"model": "test"},
                url="http://api.example.com/v1/chat/completions",
                key="must-not-be-sent",
            )
        self.assertIsNone(usage)
        urlopen.assert_not_called()

    def test_live_mode_handles_malformed_provider_usage(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"usage":{"prompt_tokens":"not-a-number","completion_tokens":4}}'

        with patch("benchmarks.run.urllib.request.urlopen", return_value=FakeResponse()):
            usage = _live_usage(
                {"model": "test"},
                url="https://api.example.com/v1/chat/completions",
                key="test-key",
            )
        self.assertIsNone(usage)


if __name__ == "__main__":
    unittest.main()
