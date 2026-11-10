"""Shared research-code.hu gateway rendering regressions."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "render_app_gateway.py"
spec = importlib.util.spec_from_file_location("app_gateway_renderer", MODULE)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class SharedGatewayTests(unittest.TestCase):
    def test_root_dashboard_and_named_app_routes(self):
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000",
             "ai-dashboard-secure-app_dashboard-control"],
            "research.code.hu",
        )
        other = renderer.validate_row(
            ["korrupcio-kutatas", "https://research.code.hu/apps/korrupcio-kutatas",
             "http://research:8205", "research_net"],
            "research.code.hu",
        )
        site = renderer.render_site("research.code.hu", [root, other])
        self.assertEqual(site.count("research.code.hu {"), 1)
        self.assertIn("handle /apps/korrupcio-kutatas/*", site)
        self.assertIn("handle /apps/*", site)
        self.assertIn("uri strip_prefix /apps/korrupcio-kutatas", site)
        self.assertIn("handle /_secure-login/redeem", site)
        self.assertIn("reverse_proxy http://ai-dashboard-secure-app-dashboard-1:8000", site)
        self.assertIn("reverse_proxy http://research:8205", site)
        # Authorization must happen before cookie stripping, and before upstream.
        self.assertLess(site.index("forward_auth"), site.index("request_header -Cookie"))
        self.assertLess(site.index("request_header -Cookie"), site.index("reverse_proxy http://research:8205"))
        self.assertIn("route {", site)

    def test_new_app_cannot_claim_another_prefix(self):
        with self.assertRaises(ValueError):
            renderer.validate_row(
                ["research", "https://research.code.hu/apps/dashboard",
                 "http://research:8200", ""], "research.code.hu",
            )

    def test_loopback_cannot_be_upstream(self):
        with self.assertRaises(ValueError):
            renderer.validate_row(
                ["ai-dashboard", "https://research.code.hu",
                 "http://127.0.0.1:8380", ""], "research.code.hu",
            )

    def test_no_per_app_hostname(self):
        with self.assertRaises(ValueError):
            renderer.validate_row(
                ["ai-dashboard", "https://dashboard.code.hu",
                 "http://dashboard:8000", ""], "research.code.hu",
            )


if __name__ == "__main__":
    unittest.main()
