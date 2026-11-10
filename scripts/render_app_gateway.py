#!/usr/bin/env python3
"""Render operator-managed, fail-closed Caddy sites from read-only application rows.

Input: pipe-delimited (id, public_url, upstream_origin, docker_network), one per
line, obtained from the trusted local PostgreSQL catalog. No service restart.
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


def parse_origin(value: str, *, scheme: str) -> tuple[str, int | None]:
    if not value or value.strip() != value:
        raise ValueError("Invalid origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme != scheme or parsed.username or parsed.password
        or parsed.path or parsed.query or parsed.fragment
        or not parsed.hostname or not DNS.fullmatch(parsed.hostname)
    ):
        raise ValueError("Invalid application origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid application port") from exc
    if parsed.netloc != parsed.hostname + (f":{port}" if port is not None else ""):
        raise ValueError("Noncanonical origin")
    return parsed.hostname, port


def render_site(app_id: str, public: str, upstream: str) -> str:
    if not APP_ID.fullmatch(app_id):
        raise ValueError("Invalid app id")
    host, public_port = parse_origin(public, scheme="https")
    upstream_host, upstream_port = parse_origin(upstream, scheme="http")
    if upstream_port is None:
        raise ValueError("Explicit upstream port is required")
    if upstream_host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        raise ValueError("Container upstream cannot be loopback")
    if public_port is not None:
        raise ValueError("Public HTTPS origin must use standard port 443")
    if host in {"localhost", "127.0.0.1"}:
        raise ValueError("Public host must not be loopback")
    if host == os.environ.get("CADDY_DOMAIN", "").lower():
        raise ValueError("Application host cannot equal login host")
    return (
        f"{host}{':' + str(public_port) if public_port else ''} {{\n"
        "    encode zstd gzip\n"
        "    header {\n"
        '        X-Content-Type-Options "nosniff"\n'
        '        Referrer-Policy "no-referrer"\n'
        "    }\n"
        "    handle /_secure-login/redeem {\n"
        "        reverse_proxy api:8000 {\n"
        f'            header_up X-Gateway-App "{app_id}"\n'
        "            header_up Host {host}\n"
        "        }\n"
        "    }\n"
        "    handle {\n"
        "        forward_auth api:8000 {\n"
        "            uri /_secure-login/authorize\n"
        f'            header_up X-Gateway-App "{app_id}"\n'
        "            header_up Host {host}\n"
        "            header_up X-Original-Upgrade {header.Upgrade}\n"
        "        }\n"
        f"        reverse_proxy {upstream}\n"
        "    }\n"
        "}\n"
    )


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fchmod(tmp.fileno(), 0o644)
        staged = Path(tmp.name)
    os.replace(staged, path)


def main() -> None:
    rows = []
    for lineno, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        fields = line.rstrip("\r\n").split("|")
        if len(fields) != 4:
            raise ValueError(f"Invalid registry row {lineno}")
        app_id, public, upstream, network = fields
        site = render_site(app_id, public, upstream)
        if network and not NETWORK.fullmatch(network):
            raise ValueError("Invalid Docker network name")
        rows.append((app_id, site, network))
    ids = [row[0] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate application identifiers")
    networks = sorted({row[2] for row in rows if row[2]})
    aliases = {network: f"app_network_{index}" for index, network in enumerate(networks, 1)}

    # Stage and validate all content before writing. Only this dedicated,
    # generated folder is managed; never touch custom Caddy configuration.
    overlay = "services:\n  caddy:\n    networks:\n      - secure_net\n"
    overlay += "".join(f"      - {aliases[n]}\n" for n in networks)
    overlay += "networks:\n  secure_net: {}\n"
    overlay += "".join(
        f"  {aliases[n]}:\n    external: true\n    name: {json.dumps(n)}\n"
        for n in networks
    )
    OUT.mkdir(parents=True, exist_ok=True)
    for app_id, site, _ in rows:
        atomic_write(OUT / f"{app_id}.caddy", site)
    for old in OUT.glob("*.caddy"):
        if old.stem not in ids:
            old.unlink()
    atomic_write(OVERLAY, overlay)
    print(f"Generated {len(rows)} app site(s) and {len(networks)} network(s).")
    print("No running containers or services have been changed.")


if __name__ == "__main__":
    main()
