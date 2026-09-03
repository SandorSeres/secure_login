import os
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class AdminScriptTests(unittest.TestCase):
    def test_groups_are_read_from_database_without_env_file(self):
        environment = os.environ.copy()
        fixture_directory = REPOSITORY_ROOT / "tests" / "fixtures"
        environment["PATH"] = f"{fixture_directory}{os.pathsep}{environment['PATH']}"

        result = subprocess.run(
            [REPOSITORY_ROOT / "deploy" / "admin.sh"],
            input="alice\n1\n",
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1) free  [jelenlegi]", result.stdout)
        self.assertIn("2) pro", result.stdout)
        self.assertIn("Nem történt módosítás.", result.stdout)


if __name__ == "__main__":
    unittest.main()
