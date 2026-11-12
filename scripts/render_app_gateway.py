#!/usr/bin/env python3
"""Render one Secure Login application gateway: a root portal and /apps/*.

The root portal (AI-Dashboard) keeps its absolute /api, /static, /assets and
/ws URLs, because the whole host root is its mount point. Applications on the
shared host are isolated under /apps/<id>/, and that prefix is theirs alone: no
route on that host maps a root-level path into an /apps/ application, so a
second app's /static or /api can never be read as the root portal's.

An application that cannot be told its own mount point cannot live under that
prefix, because it writes root-absolute asset URLs and the browser then leaves
the application namespace. Such an application gets a hostname of its own: its
public_url names that host, and this generator renders it as its own site,
where the application root and the host root are the same thing.

Rows on stdin: app_id|public_url|internal_origin|docker_network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "caddy" / "app-gateway-generated"
OVERLAY = ROOT / "compose.app-gateway.yaml"
APP_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
DNS = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,251}[a-zA-Z0-9]\Z")
NETWORK = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


def parse_url(value: str, scheme: str) -> tuple[str, int | None, str]:
    if not value or value.strip() != value:
        raise ValueError("Invalid URL")
    p = urlsplit(value)
    if (
        p.scheme != scheme or not p.hostname or not DNS.fullmatch(p.hostname)
        or p.username or p.password or p.query or p.fragment
    ):
        raise ValueError("Invalid URL")
    try:
        port = p.port
    except ValueError as exc:
        raise ValueError("Invalid port") from exc
    if p.netloc.lower() != p.hostname.lower() + (f":{port}" if port else ""):
        raise ValueError("Noncanonical host or port")
    # A hostname is case-insensitive, a Caddy site block is not, and the API
    # compares this address against request.headers["host"].lower(). Lowercase
    # once here so a registry row typed in mixed case still names its site.
    return p.hostname.lower(), port, p.path


def validate_row(fields: list[str], host: str) -> tuple[str, str, str, str, bool]:
    """One registry row, resolved to the site and mount point it belongs to.

    A row may name the shared host or a hostname of its own. On the shared
    host an application either is the root portal or owns /apps/<app>. On its
    own host it is always that host's root, so its mount prefix is empty and
    nothing has to be stripped or added on its way.
    """
    app, public, upstream, network = fields
    if not APP_ID.fullmatch(app):
        raise ValueError("Invalid application ID")
    external_host, external_port, external_path = parse_url(public, "https")
    if external_port is not None:
        raise ValueError("Public address must use HTTPS on 443")
    if external_host == host:
        is_root = external_path in ("", "/")
        if not is_root and external_path != "/apps/" + app:
            raise ValueError("App address must be https://" + host + "/apps/" + app)
    else:
        # A hostname of its own is a site, so it is that site's root. Sharing
        # one host between two applications would reintroduce the collision
        # this layout exists to avoid.
        if external_path not in ("", "/"):
            raise ValueError("A dedicated hostname must be the application root")
        is_root = True
    site = external_host
    internal_host, internal_port, internal_path = parse_url(upstream, "http")
    if (internal_port is None or internal_path
            or internal_host == "localhost"
            or internal_host.startswith("127.")
            or internal_host in {"0.0.0.0", "::", "[::]"}):
        raise ValueError("Backend requires reachable Docker hostname and explicit port")
    if network and not NETWORK.fullmatch(network):
        raise ValueError("Invalid Docker network")
    return app, site, upstream, network, is_root


def auth_block(app: str) -> str:
    # request_header, not header_up: the value has to be gone before the
    # forward_auth subrequest, which is built from the incoming headers and
    # would otherwise carry a client-sent one straight to authorize.
    return (
        "        request_header X-Gateway-App " + app + "\n"
        "        forward_auth api:8000 {\n"
        "            uri /_secure-login/authorize\n"
        "            header_up Host {host}\n"
        "            header_up X-Forwarded-Method {http.request.method}\n"
        "            header_up X-Original-Upgrade {header.Upgrade}\n"
        # The subrequest must stay an ordinary HTTP GET. Caddy copies the
        # Upgrade header, and Secure Login serves no WebSocket: uvicorn would
        # refuse the handshake with 403 and every upgraded request would fail
        # to authorize. The real request is proxied separately, still upgraded.
        "            header_up -Connection\n"
        "            header_up -Upgrade\n"
        "        }\n"
    )


def redeem_handler(path: str, app: str) -> str:
    # No uri strip_prefix here. Redeem names the application from the address
    # the proxy actually routed, and that address carries the /apps/ prefix.
    # Stripping it left the API looking at a path that belongs to the root
    # application, so a prefixed ticket could never be redeemed.
    lines = [f"    handle {path} {{\n"]
    lines.extend([
        "        reverse_proxy api:8000 {\n",
        f'            header_up X-Gateway-App "{app}"\n',
        "            header_up Host {host}\n",
        "        }\n",
        "    }\n",
    ])
    return "".join(lines)


def app_routes(app: str, upstream: str, prefix: str) -> str:
    """The routes of one application at one mount point.

    An empty prefix means the application owns the whole site, so its own root
    and the host root are the same and nothing is added or removed. A prefix is
    the application's mount point: it is stripped before the upstream, because
    the application has to see its own root, and its bare form is rewritten
    into the slash form rather than redirected. A redirect would leave the
    route before the browser holds an application cookie and land on 401.
    """
    parts = [redeem_handler(prefix + "/_secure-login/redeem", app)]
    if prefix:
        parts.extend([
            f"    handle {prefix} {{\n",
            "        route {\n",
            f"            rewrite {prefix}/ {{uri}}\n",
            auth_block(app).replace("        ", "            "),
            "        }\n",
            "    }\n",
            f"    handle {prefix}/* {{\n",
            "        route {\n",
            auth_block(app),
            f"            uri strip_prefix {prefix}\n",
            "            request_header -Cookie\n",
            f"            reverse_proxy {upstream} {{\n",
            "                flush_interval -1\n",
            "            }\n",
            "        }\n",
            "    }\n",
        ])
        return "".join(parts)
    parts.extend([
        "    handle {\n",
        "        route {\n",
        auth_block(app),
        "            request_header -Cookie\n",
        f"            reverse_proxy {upstream} {{\n",
        "                flush_interval -1\n",
        "            }\n",
        "        }\n",
        "    }\n",
    ])
    return "".join(parts)


def render_site(host: str, rows: list[tuple[str, str, str, str, bool]],
                shared_host: str) -> str:
    """One Caddy site: the shared host, or an application's own hostname.

    shared_host is what makes a site dedicated. Whether a row names this host
    says nothing about it: every row of a site names that site's host.
    """
    parts = [
        f"{host} {{\n",
        "    encode zstd gzip\n",
        '    header X-Content-Type-Options "nosniff"\n',
        '    header Referrer-Policy "no-referrer"\n',
        # A client must never decide this header, so every route overwrites
        # it with the value this route was rendered for. Dropping it here
        # would not be enough: forward_auth builds its subrequest from the
        # incoming headers, so the value would arrive at authorize unset.
    ]
    dedicated = host != shared_host
    if dedicated:
        # An application on its own hostname: no /apps/* namespace exists
        # here, and the redeem path belongs to the same host root.
        for app, _, upstream, _, _ in rows:
            parts.append(app_routes(app, upstream, ""))
        parts.append("}\n")
        return "".join(parts)
    root = next((row for row in rows if row[1] == host and row[4]), None)
    for app, site, upstream, _, is_root in rows:
        if site != host:
            continue
        parts.append(app_routes(app, upstream, "" if is_root else "/apps/" + app))
    parts.extend([
        "    handle /apps/* {\n",
        '        respond "Application not found" 404\n',
        "    }\n",
        "    handle /_secure-login/* {\n",
        '        respond "Not found" 404\n',
        "    }\n",
    ])
    if not root:
        # Without a root application the host belongs to no application, so a
        # catch-all would hand the host to some unrelated upstream.
        parts.extend(["    handle {\n", '        respond "Not found" 404\n', "    }\n"])
    parts.append("}\n")
    return "".join(parts)


def _site_rows(rows: list[tuple[str, str, str, str, bool]], host: str) -> dict[str, list]:
    """Group validated rows by the site each one belongs to.

    The shared host may hold one root application plus any number of /apps/*
    mounts. A dedicated hostname holds exactly its own application. Anything
    else would put two applications in the same namespace, which is the
    collision this layout exists to avoid.
    """
    sites: dict[str, list] = {}
    for row in rows:
        sites.setdefault(row[1], []).append(row)
    for site, group in sites.items():
        if sum(1 for other in group if other[4]) > 1:
            raise ValueError("Only one root application may own " + site + "/")
        if site != host and len(group) != 1:
            raise ValueError("A dedicated hostname must serve one application: " + site)
    return sites


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fchmod(tmp.fileno(), 0o644)
        staged = Path(tmp.name)
    os.replace(staged, path)


def main() -> None:
    host = os.environ.get("APP_GATEWAY_HOST", "research.code.hu")
    if not DNS.fullmatch(host) or host == os.environ.get("CADDY_DOMAIN", ""):
        raise ValueError("Invalid shared application hostname")
    rows = []
    for lineno, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        fields = line.rstrip("\r\n").split("|")
        if len(fields) != 4:
            raise ValueError(f"Invalid registry row {lineno}")
        rows.append(validate_row(fields, host))
    app_ids = [row[0] for row in rows]
    if len(set(app_ids)) != len(app_ids):
        raise ValueError("Duplicate application ID")
    # Grouped per site: two root applications may coexist as long as they do
    # not name the same host. The shared host always gets a site, even with no
    # row of its own, so a host cannot end up unconfigured and unlisted.
    sites = _site_rows(rows, host)
    sites.setdefault(host, [])
    networks = sorted({row[3] for row in rows if row[3]})
    network_aliases = {network: f"app_network_{i}" for i, network in enumerate(networks, 1)}
    overlay = "services:\n  caddy:\n    networks:\n      - secure_net\n"
    overlay += "".join(f"      - {network_aliases[n]}\n" for n in networks)
    overlay += "networks:\n  secure_net: {}\n"
    overlay += "".join(
        f"  {network_aliases[n]}:\n    external: true\n    name: {json.dumps(n)}\n" for n in networks
    )
    OUT.mkdir(parents=True, exist_ok=True)
    # One file per site, so a re-deploy never leaves a stale hostname behind.
    # The shared host always gets a site, even with no rows of its own.
    written = set()
    for site, site_rows in sites.items():
        # Never a bare "<hostname>.caddy": Caddy expands a file named
        # after the identity host as a placeholder across that file.
        name = "app-" + site + ".caddy"
        write_atomic(OUT / name, render_site(site, site_rows, host))
        written.add(name)
    for stale in OUT.glob("*.caddy"):
        if stale.name not in written:
            stale.unlink()
    write_atomic(OVERLAY, overlay)
    print(f"Generated {len(rows)} app(s) on {len(sites)} hostname(s); root apps: "
          f"{sum(1 for row in rows if row[4])}.")
    print("No running containers or services have been changed.")


if __name__ == "__main__":
    main()
