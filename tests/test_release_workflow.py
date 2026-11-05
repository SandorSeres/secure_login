"""ARM64 publication workflow is explicit-only and does not deploy live auth."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/secure-login-arm64-release.yml"


class ReleaseWorkflowSafetyTests(unittest.TestCase):
    def test_manual_only_publish_gate(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertNotIn("pull_request:", content)
        self.assertNotIn("schedule:", content)
        self.assertIn("default: false", content)
        self.assertIn('PUBLISH_ALLOWED', content)
        self.assertIn('DOCKERHUB_PUBLISH_ENABLED', content)
        self.assertIn('refs/heads/main', content)
        self.assertIn('dockerhub-publish', content)
        self.assertIn('if: ${{ inputs.publish }}', content)
        self.assertIn("scripts/check_rem_contract.py", content)
        self.assertIn("platforms: linux/arm64", content)
        self.assertIn("docker buildx imagetools inspect", content)
        self.assertIn("secure-login-published-image.json", content)
        self.assertNotIn("docker compose up", content)
        self.assertNotIn("docker.sock", content)

if __name__ == "__main__":
    unittest.main()
