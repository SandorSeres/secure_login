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

    def test_the_gateway_header_is_set_before_the_auth_subrequest(self):
        # X-Gateway-App decides which application authorize serves and which
        # one the upstream believes it is. forward_auth builds its subrequest
        # from the incoming headers, so a client-sent value would reach
        # authorize unless the route overwrites it first. header_up inside the
        # block is too late: it only applies to the subrequest's own copy.
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
            "research.code.hu")
        site = renderer.render_site("research.code.hu", [root])
        self.assertLess(site.index('request_header X-Gateway-App ai-dashboard'),
                        site.index("forward_auth"))
        self.assertNotIn("header_up X-Gateway-App", site.split("forward_auth")[1])

    def test_the_subrequest_asks_about_the_app_it_proxies(self):
        # A route must authorize the application it then serves. One site-wide
        # value would let a prefix route pass authorization as another app and
        # be proxied with the wrong identity.
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
            "research.code.hu")
        other = renderer.validate_row(
            ["korrupcio-kutatas", "https://research.code.hu/apps/korrupcio-kutatas",
             "http://research:8205", ""], "research.code.hu")
        site = renderer.render_site("research.code.hu", [root, other])
        prefix_block = site.split("handle /apps/korrupcio-kutatas/*")[1]
        self.assertIn("request_header X-Gateway-App korrupcio-kutatas",
                      prefix_block.split("forward_auth")[0])
        self.assertNotIn("request_header X-Gateway-App ai-dashboard",
                         prefix_block.split("forward_auth")[0])

    def test_the_auth_subrequest_is_never_upgraded(self):
        # Caddy copies Upgrade/Connection into the subrequest. Secure Login
        # serves no WebSocket, so uvicorn answers the handshake with 403 and
        # every upgraded request fails to authorize, even a valid cookie.
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
            "research.code.hu")
        site = renderer.render_site("research.code.hu", [root])
        subrequest = site[site.index("forward_auth"):site.index("request_header -Cookie")]
        self.assertIn("header_up -Upgrade", subrequest)
        self.assertIn("header_up -Connection", subrequest)
        # The upgrade still has to reach the application, so the gateway keeps
        # reporting it instead of hiding it.
        self.assertIn("header_up X-Original-Upgrade", subrequest)


        # Redeem runs before any forward_auth has had a chance to stamp a
        # header, so its own site block has to name the application, and no
        # route may leave a client-sent value standing.
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
            "research.code.hu")
        other = renderer.validate_row(
            ["research", "https://research.code.hu/apps/research",
             "http://research:8205", ""], "research.code.hu")
        site = renderer.render_site("research.code.hu", [root, other])
        for path, app in (("/_secure-login/redeem", "ai-dashboard"),
                          ("/apps/research/_secure-login/redeem", "research")):
            block = site[site.index("handle " + path):]
            block = block[:block.index("    }\n")]
            self.assertIn('header_up X-Gateway-App "%s"' % app, block, path)
        # No route may set the header only inside forward_auth, and none may
        # pass a client value through to an upstream.
        self.assertNotIn("header_up X-Gateway-App",
                         site[site.index("handle /apps/research/*"):])
        self.assertEqual(site.count("forward_auth"), site.count("request_header X-Gateway-App"))

    def test_new_app_cannot_claim_another_prefix(self):
        with self.assertRaises(ValueError):
            renderer.validate_row(
                ["research", "https://research.code.hu/apps/dashboard",
                 "http://research:8200", ""], "research.code.hu",
            )

    def test_loopback_cannot_be_upstream(self):
        for upstream in ("http://127.0.0.1:8380", "http://localhost:8380",
                         "http://0.0.0.0:8380"):
            with self.subTest(upstream=upstream):
                with self.assertRaises(ValueError):
                    renderer.validate_row(
                        ["ai-dashboard", "https://research.code.hu", upstream, ""],
                        "research.code.hu",
                    )

    def test_the_backend_must_name_a_docker_service(self):
        for upstream in ("http://dashboard", "http://dashboard:8000/dashboard",
                         "https://dashboard:8000"):
            with self.subTest(upstream=upstream):
                with self.assertRaises(ValueError):
                    renderer.validate_row(
                        ["ai-dashboard", "https://research.code.hu", upstream, ""],
                        "research.code.hu",
                    )

    def test_no_per_app_hostname(self):
        with self.assertRaises(ValueError):
            renderer.validate_row(
                ["ai-dashboard", "https://dashboard.code.hu",
                 "http://dashboard:8000", ""], "research.code.hu",
            )


if __name__ == "__main__":
    unittest.main()
