# Secure Login — Caddy + automatic HTTPS

The Docker Compose reverse proxy is **Caddy**, replacing Nginx + Certbot.
The FastAPI application, existing routes, PostgreSQL/Redis settings, and
user-to-application data model are unchanged. Caddy is not an identity
provider and does not authorize application access.

## Prerequisites

- A real domain, e.g. `auth.example.com`, pointing via DNS A/AAAA to
  the public address of the host or its upstream router.
- TCP ports **80 and 443** routed to the Docker host; UDP 443 is optional
  for HTTP/3. If an AAAA record exists, IPv6 routing must also work.
- No other service binding those host ports.
- The DGX Spark must be able to reach the public ACME certificate authority.
- Persistent Docker volume `caddy_data`; **do not remove this volume**
  during upgrades or backups. Caddy stores TLS certificate material there.

Set the required variables in a local, untracked `.env` file. Example:

```dotenv
CADDY_DOMAIN=auth.example.com
ACME_EMAIL=admin@example.com
APP_HOST=https://auth.example.com
# Keep all other existing PostgreSQL, signing and SMTP variables unchanged.
```

Never use the example domain or password placeholders in a real deployment.

## Validate and launch

```bash
docker compose config --quiet
docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose up -d --build
docker compose ps
docker compose logs --no-color caddy
curl -fsS https://auth.example.com/health
```

Check the public certificate and HTTPS from a different network. Caddy
requests and automatically renews public certificates. The domain must be
publicly reachable for the default HTTP-01/TLS-ALPN-01 challenges.
For a private-only installation, use a separate DNS-01 ACME challenge
setup with the appropriate DNS provider module; this compose does not
include DNS-01 plugins. A local IP / `.local` hostname does not receive
a publicly trusted Let's Encrypt certificate via this configuration.

## Scope and safety

- Does **not** install changes onto a running host.
- Caddy forwards all requests to the existing `api:8000` application.
- HTTPS termination and automatic redirects happen in Caddy.
- `caddy_data` and `caddy_config` are persisted named volumes.
- No dynamic Caddy routes, forward-auth enforcement, app grants, or
  certificate DNS-01 plugin are added.
- Do not run `docker compose down -v` against persistent deployment data.

## HTTP/3 (QUIC) is disabled

`servers :443 { protocols h1 h2 }` in the Caddyfile turns HTTP/3 off for the
whole deployment. Caddy otherwise adds an HTTP/3 listener to every automatic
HTTPS site. The research workspace upstream is uvicorn, which speaks plain
HTTP/1.1 to Caddy; behind an HTTP/3 client connection the proxied response
could not be completed and Firefox saw a repeated `network error` while the
same request over HTTP/2 succeeded. HTTP/2 and HTTP/1.1 remain fully enabled,
so WebSockets and streaming are unaffected.

To test without a live domain, use a disposable environment; automatic
public certificate issuance cannot be verified solely by parsing the config.
