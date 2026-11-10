#!/usr/bin/env python3
"""Generate shared research.code.hu routes; no running containers modified.

Input (trusted local DB rows): app_id|public_url|internal_origin|docker_network|legacy_roots.
Legacy roots reserve root-relative /api, /static etc for one app only.
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
ROOT_ROUTE = re.compile(r"/[a-zA-Z0-9][a-zA-Z0-9_-]*\Z")
RESERVED = {"/_secure-login"}


def parse_origin(value: str, scheme: str) -> tuple[str, int | None, str]:
    parsed = urlsplit(value)
    if (
        value.strip() != value or parsed.scheme != scheme or not parsed.hostname
        or not DNS.fullmatch(parsed.hostname) or parsed.username or parsed.password
        or parsed.query or parsed.fragment
    ):
        raise ValueError("Invalid URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid port") from exc
    return parsed.hostname, port, parsed.path


def validate_row(fields: list[str], shared_host: str) -> tuple[str, str, str, list[str]]:
    app, public, upstream, network, roots = fields
    if not APP_ID.fullmatch(app):
        raise ValueError("Invalid application ID")
    host, public_port, path = parse_origin(public, "https")
    if host != shared_host or public_port is not None or path != "/" + app:
        raise ValueError("Public URL must be https://" + shared_host + "/" + app)
    internal_host, internal_port, internal_path = parse_origin(upstream, "http")
    if internal_port is None or internal_path or internal_host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        raise ValueError("Backend must be a reachable container HTTP origin and explicit port")
    if network and not NETWORK.fullmatch(network):
        raise ValueError("Invalid Docker network")
    aliases = [item.strip() for item in roots.split(",") if item.strip()]
    if len(aliases) != len(set(aliases)):
        raise ValueError("Duplicate legacy route")
    for alias in aliases:
        if not ROOT_ROUTE.fullmatch(alias) or alias in RESERVED or alias == "/" + app:
            raise ValueError("Invalid legacy route: " + alias)
    return app, upstream, network, aliases


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


def render_site(shared_host: str, rows: list[tuple[str, str, str, list[str]]]) -> str:
    # Caddy handles only one site per hostname, with mutually exclusive path routes.
    parts = [f"{shared_host} {{\n", "    encode zstd gzip\n",
             '    header X-Content-Type-Options "nosniff"\n',
             '    header Referrer-Policy "no-referrer"\n']
    for app, upstream, _, aliases in rows:
        parts.extend([
            f"    handle /{app} {{\n",
            f"        redir /{app}/ 308\n",
            "    }\n",
            f"    handle /{app}/_secure-login/redeem {{\n",
            f"        uri strip_prefix /{app}\n",
            "        reverse_proxy api:8000 {\n",
            f'            header_up X-Gateway-App "{app}"\n',
            "            header_up Host {host}\n",
            "        }\n",
            "    }\n",
            f"    handle /{app}/* {{\n",
            auth_block(app),
            f"        uri strip_prefix /{app}\n",
            f"        reverse_proxy {upstream}\n",
            "    }\n",
        ])
        for alias in aliases:
            parts.extend([
                f"    handle {alias} {{\n",
                auth_block(app),
                f"        reverse_proxy {upstream}\n",
                "    }\n",
                f"    handle {alias}/* {{\n",
                auth_block(app),
                f"        reverse_proxy {upstream}\n",
                "    }\n",
            ])
    parts.extend(['    handle {\n', '        respond "Not found" 404\n', '    }\n', '}\n'])
    return "".join(parts)


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fchmod(tmp.fileno(), 0o644)
        staged = Path(tmp.name)
    os.replace(staged, path)


def main() -> None:
    shared_host = os.environ.get("APP_GATEWAY_HOST", "research.code.hu")
    if not DNS.fullmatch(shared_host) or shared_host == os.environ.get("CADDY_DOMAIN", ""):
        raise ValueError("Invalid shared gateway hostname")
    rows = []
    for lineno, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        fields = line.rstrip("\r\n").split("|")
        if len(fields) != 5:
            raise ValueError(f"Invalid catalog row {lineno}")
        rows.append(validate_row(fields, shared_host))
    apps = [x[0] for x in rows]
    if len(apps) != len(set(apps)):
        raise ValueError("Duplicate app IDs")
    all_routes = {"/" + a for a in apps}
    for app, _, _, aliases in rows:
        for alias in aliases:
            if alias in all_routes:
                raise ValueError("Legacy route conflicts with app ID: " + alias)
            all_routes.add(alias)
    # Separate check for alias collisions, including exact + subtree.
    aliases = [a for row in rows for a in row[3]]
    if len(aliases) != len(set(aliases)):
        raise ValueError("Two apps claim the same root-relative path")
    networks = sorted({x[2] for x in rows if x[2]})
    network_aliases = {network: f"app_network_{index}" for index, network in enumerate(networks, 1)}
    overlay = "services:\n  caddy:\n    networks:\n      - secure_net\n"
    overlay += "".join(f"      - {network_aliases[n]}\n" for n in networks)
    overlay += "networks:\n  secure_net: {}\n"
    overlay += "".join(
        f"  {network_aliases[n]}:\n    external: true\n    name: {json.dumps(n)}\n"
        for n in networks
    )
    OUT.mkdir(parents=True, exist_ok=True)
    # Remove old per-host files only after complete validation.
    if rows:
        write_atomic(OUT / "research-gateway.caddy", render_site(shared_host, rows))
    else:
        (OUT / "research-gateway.caddy").unlink(missing_ok=True)
    for old in OUT.glob("*.caddy"):
        if old.name != "research-gateway.caddy":
            old.unlink()
    write_atomic(OVERLAY, overlay)
    print(f"Generated shared gateway for {len(rows)} application(s) on {shared_host}.")
    print("No running services changed.")


if __name__ == "__main__":
    main()
