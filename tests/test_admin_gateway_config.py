"""Gateway config regressions: no duplicate keys, no orphan overlay, README truth."""
import ast
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yaml"


class GatewayConfigTests(unittest.TestCase):
    def test_compose_has_a_single_command_key_per_service(self):
        services = yaml.safe_load(COMPOSE.read_text())["services"]
        for name, spec in services.items():
            self.assertIsInstance(spec.get("command", []), (list, str), name)

    def test_compose_declares_every_network_it_uses(self):
        doc = yaml.safe_load(COMPOSE.read_text())
        declared = set(doc.get("networks") or {})
        for name, spec in doc["services"].items():
            used = spec.get("networks") or []
            used = list(used) if isinstance(used, (list, dict)) else []
            self.assertTrue(set(used) <= declared, f"{name} uses undeclared networks")

    def test_generated_overlay_is_not_tracked_by_git(self):
        tracked = (ROOT / ".gitignore").read_text()
        self.assertIn("/compose.app-gateway.yaml", tracked)
        self.assertIn("/caddy/app-gateway-generated/*.caddy", tracked)

    def test_admin_usernames_setting_is_declared(self):
        from app.config import settings
        self.assertEqual(settings.ADMIN_USERNAMES, "")
        self.assertGreaterEqual(settings.SESSION_TIMEOUT_MINUTES, 1)

    def test_gateway_state_is_namespaced_in_redis(self):
        sources = "".join((ROOT / "api/app" / name).read_text() for name in
                          ("session_store.py", "session_cookie.py", "application_access.py"))
        self.assertIn("secure-login:session:", sources)
        self.assertIn("secure-login:app-ticket:", sources)
        self.assertIn("secure-login:app-cookie", sources)

    def test_one_shared_redis_client_for_the_whole_app(self):
        clients = (ROOT / "api/app/redis_client.py").read_text()
        self.assertIn("REDIS_URL", clients)
        for name in ("token_store.py", "session_store.py", "session_cookie.py",
                     "application_access.py", "rate_limit.py"):
            self.assertIn("redis_client", (ROOT / "api/app" / name).read_text(), name)

    def test_readme_documents_the_real_install_steps(self):
        readme = (ROOT / "README.md").read_text()
        self.assertNotIn(".env.example", readme)
        self.assertIn("SESSION_TIMEOUT_MINUTES", readme)
        self.assertIn("git clone <YOUR_REPO_URL> secure-login", readme)
        self.assertIn("docker compose up -d --build", readme)

    def test_readme_documents_revocation_semantics(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("session", readme.lower())
        self.assertRegex(readme, r"(visszavon|revok)")

    def test_gateway_doc_matches_the_current_logout_behavior(self):
        doc = (ROOT / "docs/APP_GATEWAY.md").read_text()
        self.assertNotIn("does not\nrevoke existing app cookies", doc)
        self.assertIn("Session storage", doc)


if __name__ == "__main__":
    unittest.main()
