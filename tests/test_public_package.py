import subprocess
import sys
import unittest

import toknix
from toknix.proxy import transform_payload as toknix_transform_payload
from tokensaver.proxy import transform_payload as legacy_transform_payload


class PublicPackageTests(unittest.TestCase):
    def test_public_version_and_legacy_module_alias(self):
        self.assertTrue(toknix.__version__)
        self.assertIs(toknix_transform_payload, legacy_transform_payload)

    def test_module_cli_help_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "toknix.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Toknix", result.stdout)
        self.assertIn("serve", result.stdout)


if __name__ == "__main__":
    unittest.main()
