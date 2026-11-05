"""Packaging and negative compatibility contract tests (no Docker daemon)."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_upload.sh"
sys.path.insert(0, str(ROOT / "scripts"))
from check_rem_contract import check


class ReleaseSafetyTests(unittest.TestCase):
    def test_bash_syntax_and_help(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode, 0)
        result = subprocess.run(["bash", str(SCRIPT), "--help"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("secure-login", result.stdout)

    def test_push_is_explicit_and_runs_compatibility_gate_on_tag_archive(self):
        data = SCRIPT.read_text()
        self.assertIn("PUSH=false", data)
        self.assertIn('--push) PUSH=true', data)
        self.assertIn('if [[ "$PUSH" == true ]]', data)
        self.assertIn('python3 "$tmp/context/scripts/check_rem_contract.py" --source-root "$tmp/context"', data)
        self.assertIn("git archive", data)
        self.assertIn("git merge-base --is-ancestor", data)
        self.assertIn("api/Dockerfile", data)
        self.assertIn("linux/arm64", data)
        self.assertIn("containerimage.digest", data)
        self.assertNotIn("docker compose up", data)
        self.assertNotIn("docker stop", data)

    def test_fail_closed_when_source_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            failures = check(pathlib.Path(folder))
            self.assertTrue(failures)

    def test_contract_is_checked_instead_of_assumed(self):
        # Before source recovery, the original public tree is incomplete.
        # After import, the source is required to expose the static markers.
        failures = check(ROOT)
        recovered = (ROOT / "api/app/authorization.py").is_file()
        if recovered:
            self.assertEqual(failures, [])
        else:
            self.assertTrue(failures)

    def test_bad_versions_do_not_run_build(self):
        for args in ([], ["--version", "latest"], ["--version", "v0.1.0", "--namespace", "bad/name"]):
            result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR:", result.stderr)


if __name__ == "__main__":
    unittest.main()
