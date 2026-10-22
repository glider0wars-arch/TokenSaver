import unittest

from benchmarks.run import run


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


if __name__ == "__main__":
    unittest.main()
