#!/usr/bin/env python3
"""Render one shared research.code.hu gateway with a root portal and /apps/*.

The root portal (AI-Dashboard) retains its absolute /api, /static, /assets,
/ws URLs. Other applications are isolated under /apps/<id>/.
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
    if p.netloc != p.hostname + (f":{port}" if port else ""):
        raise ValueError("Noncanonical host or port")
    return p.hostname, port, p.path


def validate_row(fields: list[str], host: str) -> tuple[str, str, str, bool]:
    app, public, upstream, network = fields
    if not APP_ID.fullmatch(app):
        raise ValueError("Invalid application ID")
    external_host, external_port, external_path = parse_url(public, "https")
    if external_host != host or external_port is not None:
        raise ValueError("Public address must use shared HTTPS hostname on 443")
    is_root = external_path in ("", "/")
    if not is_root and external_path != "/apps/" + app:
        raise ValueError("App address must be https://" + host + "/apps/" + app)
    internal_host, internal_port, internal_path = parse_url(upstream, "http")
    if internal_port is None or internal_path or internal_host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        raise ValueError("Backend requires reachable Docker hostname and explicit port")
    if network and not NETWORK.fullmatch(network):
        raise ValueError("Invalid Docker network")
    return app, upstream, network, is_root


def auth_block(app: str) -> str:
    return (
        "        forward_auth api:8000 {\n"
        "            uri /_secure-login/authorize\n"
        f'            header_up X-Gateway-App "{app}"\n'
        "            header_up Host {host}\n"
        "            header_up X-Forwarded-Method {method}\n"
        "            header_up X-Original-Upgrade {header.Upgrade}\n"
        "        }\n"
    )


def redeem_handler(path: str, app: str, strip: str = "") -> str:
    lines = [f"    handle {path} {{\n"]
    if strip:
        lines.append(f"        uri strip_prefix {strip}\n")
    lines.extend([
        "        reverse_proxy api:8000 {\n",
        f'            header_up X-Gateway-App "{app}"\n',
        "            header_up Host {host}\n",
        "        }\n",
        "    }\n",
    ])
    return "".join(lines)


def render_site(host: str, rows: list[tuple[str, str, str, bool]]) -> str:
    parts = [
        f"{host} {{\n",
        "    encode zstd gzip\n",
        '    header X-Content-Type-Options "nosniff"\n',
        '    header Referrer-Policy "no-referrer"\n',
    ]
    root = next((row for row in rows if row[3]), None)
    for app, upstream, _, is_root in rows:
        if is_root:
            parts.append(redeem_handler("/_secure-login/redeem", app))
            continue
        prefix = "/apps/" + app
        parts.extend([
            f"    handle {prefix} {{\n",
            f"        redir {prefix}/ 308\n",
            "    }\n",
            redeem_handler(prefix + "/_secure-login/redeem", app, prefix),
            f"    handle {prefix}/* {{\n",
            auth_block(app),
            f"        uri strip_prefix {prefix}\n",
            "        request_header -Cookie\n",
            f"        reverse_proxy {upstream} {{\n",
            "            flush_interval -1\n",
            "        }\n",
            "    }\n",
        ])
    parts.extend([
        "    handle /apps/* {\n",
        '        respond "Application not found" 404\n',
        "    }\n",
        "    handle /_secure-login/* {\n",
        '        respond "Not found" 404\n',
        "    }\n",
    ])
    if root:
        app, upstream, _, _ = root
        parts.extend([
            "    handle {\n",
            auth_block(app),
            f"        reverse_proxy {upstream} {{\n",
            "            flush_interval -1\n",
            "        }\n",
            "    }\n",
        ])
    else:
        parts.extend(["    handle {\n", '        respond "Not found" 404\n', "    }\n"])
    parts.append("}\n")
    return "".join(parts)


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
    if sum(row[3] for row in rows) > 1:
        raise ValueError("Only one root application may own research.code.hu/")
    networks = sorted({row[2] for row in rows if row[2]})
    network_aliases = {network: f"app_network_{i}" for i, network in enumerate(networks, 1)}
    overlay = "services:\n  caddy:\n    networks:\n      - secure_net\n"
    overlay += "".join(f"      - {network_aliases[n]}\n" for n in networks)
    overlay += "networks:\n  secure_net: {}\n"
    overlay += "".join(
        f"  {network_aliases[n]}:\n    external: true\n    name: {json.dumps(n)}\n" for n in networks
    )
    OUT.mkdir(parents=True, exist_ok=True)
    if rows:
        write_atomic(OUT / "research-gateway.caddy", render_site(host, rows))
    else:
        (OUT / "research-gateway.caddy").unlink(missing_ok=True)
    for old in OUT.glob("*.caddy"):
        if old.name != "research-gateway.caddy":
            old.unlink()
    write_atomic(OVERLAY, overlay)
    print(f"Generated {len(rows)} app(s) on {host}; root apps: {sum(row[3] for row in rows)}.")
    print("No running containers or services have been changed.")


if __name__ == "__main__":
    main()
