"""Shared research-code.hu gateway rendering regressions."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = Path(__file__).resolve().parents[1] / "scripts" / "render_app_gateway.py"
spec = importlib.util.spec_from_file_location("app_gateway_renderer", MODULE)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class SharedGatewayTests(unittest.TestCase):
    # Every path the host root already belongs to. Routing one of these into
    # an /apps/ application would take it away from the root application, so
    # the site must never contain a root-level rewrite into a prefix.
    ROOT_NAMESPACES = ("static", "_app", "api", "ws", "assets", "manifest.json")

    SHARED = "research.code.hu"

    def _site(self, host, rows):
        # A site is dedicated only in relation to the shared host, so every
        # render has to say which host is shared.
        return renderer.render_site(host, rows, self.SHARED)

    def _two_app_site(self):
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
            "research.code.hu")
        other = renderer.validate_row(
            ["korrupcio-kutatas", "https://research.code.hu/apps/korrupcio-kutatas",
             "http://research:8205", ""], "research.code.hu")
        return self._site("research.code.hu", [root, other])

    def test_no_root_path_is_routed_into_a_prefixed_application(self):
        site = self._two_app_site()
        for name in self.ROOT_NAMESPACES:
            for pattern in ("/" + name, "/" + name + "/*"):
                self.assertNotIn("handle " + pattern + " {", site, pattern)
        # The only thing the site may rewrite is the bare prefix into its own
        # slash form. Rewriting a root-level path into a prefix would take that
        # name away from the root application.
        for line in site.splitlines():
            if "rewrite " in line:
                self.assertIn("rewrite /apps/", line, line)


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
        site = self._site("research.code.hu", [root, other])
        self.assertEqual(site.count("research.code.hu {"), 1)
        self.assertIn("handle /apps/korrupcio-kutatas/*", site)
        self.assertIn("handle /apps/*", site)
        self.assertIn("uri strip_prefix /apps/korrupcio-kutatas", site)
        self.assertIn("handle /_secure-login/redeem", site)
        self.assertIn("handle /apps/korrupcio-kutatas/_secure-login/redeem", site)
        self.assertIn("reverse_proxy http://ai-dashboard-secure-app-dashboard-1:8000", site)
        self.assertIn("reverse_proxy http://research:8205", site)
        # Authorization must happen before cookie stripping, and before upstream.
        self.assertLess(site.index("forward_auth"), site.index("request_header -Cookie"))
        self.assertLess(site.index("request_header -Cookie"), site.index("reverse_proxy http://research:8205"))
        self.assertIn("route {", site)

    def test_the_bare_prefix_is_authorized_instead_of_redirecting(self):
        # Open WebUI answers only at the slash form. A 308 out of the route
        # sends the browser to a location before it holds an application
        # cookie, so the launch lands on a 401 and never gets back. The block
        # has to rewrite internally and authorize like the route does.
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
            "research.code.hu")
        other = renderer.validate_row(
            ["korrupcio-kutatas", "https://research.code.hu/apps/korrupcio-kutatas",
             "http://research:8205", ""], "research.code.hu")
        site = self._site("research.code.hu", [root, other])
        # The bare-prefix block is the app_routes() call that comes second: its
        # redeem path is prefixed and precedes the prefix route.
        app_block = site[site.index("handle /apps/korrupcio-kutatas/_secure-login/redeem"):]
        block = app_block[app_block.index("    handle /apps/korrupcio-kutatas {"):]
        block = block[:block.index("handle /apps/korrupcio-kutatas/*")]
        self.assertNotIn("redir", block)
        self.assertIn("rewrite /apps/korrupcio-kutatas/ {uri}", block)
        self.assertIn("request_header X-Gateway-App korrupcio-kutatas", block)
        self.assertIn("forward_auth", block)
        # The prefix still has to reach the app as its mount point, so this
        # block rewrites rather than strips.
        self.assertNotIn("uri strip_prefix", block)

    def test_the_prefixed_redeem_route_keeps_its_prefix(self):
        # Redeem names the application from the address the proxy routed, and
        # that address is the /apps/<id> prefix plus the redeem path. A
        # strip_prefix here left the API with the root application's path, so
        # launching a prefixed application only ever answered
        # "Invalid application gateway".
        root = renderer.validate_row(
            ["ai-dashboard", "https://research.code.hu",
             "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
            "research.code.hu")
        other = renderer.validate_row(
            ["korrupcio-kutatas", "https://research.code.hu/apps/korrupcio-kutatas",
             "http://research:8205", ""], "research.code.hu")
        site = self._site("research.code.hu", [root, other])
        block = site[site.index("handle /apps/korrupcio-kutatas/_secure-login/redeem"):]
        block = block[:block.index("    }\n")]
        self.assertNotIn("uri strip_prefix", block)
        # The application route still strips its prefix: the app sees its root.
        app_route = site[site.index("handle /apps/korrupcio-kutatas/*"):]
        self.assertIn("uri strip_prefix /apps/korrupcio-kutatas", app_route)

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
        site = self._site("research.code.hu", [root])
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
        site = self._site("research.code.hu", [root, other])
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
        site = self._site("research.code.hu", [root])
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
        site = self._site("research.code.hu", [root, other])
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

    def test_an_application_may_own_a_hostname(self):
        # An app that cannot report its own mount point cannot live under
        # /apps/. Its address names a host of its own, which makes it that
        # host's root: nothing is stripped or added on its way.
        row = renderer.validate_row(
            ["openwebui", "https://openwebui.code.hu", "http://openwebui:8080",
             "openwebui_net"], "research.code.hu")
        self.assertEqual(row[1], "openwebui.code.hu")
        self.assertTrue(row[4])
        site = self._site("openwebui.code.hu", [row])
        self.assertIn("openwebui.code.hu {", site)
        self.assertIn("reverse_proxy http://openwebui:8080", site)
        self.assertIn("handle /_secure-login/redeem", site)
        self.assertEqual(site.count("handle /_secure-login/redeem"), 1)
        self.assertNotIn("/apps/", site)
        self.assertNotIn("strip_prefix", site)

    def test_a_dedicated_hostname_takes_no_path_port_or_case(self):
        for public in ("https://openwebui.code.hu/apps/openwebui",
                       "https://openwebui.code.hu:8443",
                       "https://openwebui.code.hu/?next=/x"):
            with self.subTest(public=public):
                with self.assertRaises(ValueError):
                    renderer.validate_row(
                        ["openwebui", public, "http://openwebui:8080", ""],
                        "research.code.hu")
        # A hostname is case-insensitive; the site block and the API's Host
        # comparison are not, so the address has to be folded.
        row = renderer.validate_row(
            ["openwebui", "https://OpenWebUI.Code.hu", "http://openwebui:8080", ""],
            "research.code.hu")
        self.assertEqual(row[1], "openwebui.code.hu")

    def test_one_hostname_may_not_host_two_applications(self):
        # Sharing a host would reintroduce the collision the layout avoids.
        with self.assertRaises(ValueError):
            renderer._site_rows([
                ("openwebui", "otel.code.hu", "http://a:1", "", True),
                ("korrupcio", "otel.code.hu", "http://b:2", "", True),
            ], "research.code.hu")

    def test_a_dedicated_host_root_is_its_own_and_nobody_elses(self):
        # Two root applications are fine while they name different hosts: one
        # owns the shared host root, another owns its own site.
        rows = [
            renderer.validate_row(
                ["ai-dashboard", "https://research.code.hu",
                 "http://ai-dashboard-secure-app-dashboard-1:8000", ""],
                "research.code.hu"),
            renderer.validate_row(
                ["openwebui", "https://openwebui.code.hu",
                 "http://openwebui:8080", ""], "research.code.hu"),
        ]
        sites = renderer._site_rows(rows, "research.code.hu")
        self.assertEqual(sorted(sites), ["openwebui.code.hu", "research.code.hu"])
        self.assertEqual([row[0] for row in sites["research.code.hu"]], ["ai-dashboard"])
        self.assertEqual([row[0] for row in sites["openwebui.code.hu"]], ["openwebui"])

    def test_generated_files_are_named_after_their_site(self):
        # A bare "<hostname>.caddy" is a Caddyfile placeholder source, so every
        # generated site file must carry the app- prefix. The import in the
        # Caddyfile would miss a file without it.
        caddyfile = (ROOT / "caddy" / "Caddyfile").read_text()
        self.assertIn("app-gateway-generated/app-*.caddy", caddyfile)
        source = (ROOT / "scripts" / "render_app_gateway.py").read_text()
        self.assertIn('name = "app-" + site + ".caddy"', source)


if __name__ == "__main__":
    unittest.main()
