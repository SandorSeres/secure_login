"""Ensure opt-in --from-main always references the verified remote commit."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_upload.sh"


class MainCandidateTests(unittest.TestCase):
    def test_shell_syntax_and_help(self):
        check = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--from-main", result.stdout)
        self.assertIn("--push", result.stdout)

    def test_version_and_main_are_mutually_exclusive(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--from-main", "--version", "v0.1.0"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choose --version or --from-main", result.stderr)

    def test_default_publish_disabled_and_source_immutable(self):
        script = SCRIPT.read_text()
        self.assertIn("PUSH=false", script)
        self.assertIn('if [[ "$PUSH" == true ]]', script)
        self.assertIn('git ls-remote origin refs/heads/main', script)
        self.assertIn('[[ "$commit" == "$remote_commit" ]]', script)
        self.assertIn("git merge-base --is-ancestor", script)
        self.assertIn("git archive", script)
        self.assertIn('VERSION="sha-$commit"', script)
        self.assertIn('python3 "$tmp/context/scripts/check_rem_contract.py"', script)
        self.assertIn("--platform linux/arm64", script)
        self.assertNotIn("docker compose up", script)
        self.assertNotIn("docker stop", script)


if __name__ == "__main__":
    unittest.main()
