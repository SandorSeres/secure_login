# Secure Login N:M application gateway — shared research.code.hu

**Routing contract (one hostname for all applications):**
- `https://ai.code.hu`: existing Secure Login and authentication.
- `https://research.code.hu/<app-id>/`: application access behind Caddy.
- The only application DNS name required is `research.code.hu`. **No per-app DNS entries.**
- Caddy owns the HTTPS endpoint and enforces the N:M grant on **every** HTTP or WebSocket request.

The existing users, passwords, sessions, grants, and `ai.code.hu` login
routes are unchanged. All application routes are **opt in** until configured.

## Admin menu

`bash admin.sh`:
- 4 — create application and its URL/network/compatibility routing.
- 5 — assign an existing app to an existing user (unchanged).
- 6 — revoke (unchanged, takes effect on the next gateway request).
- 8 — change URL/network/compatibility routes without deleting grants.
- 3 — list applications and configured targets.

For AI-Dashboard already registered as `ai-dashboard`, use **8**, not 4:

```text
Alkalmazásazonosító: ai-dashboard
Nyilvános URL: https://research.code.hu/ai-dashboard
Belső cél-URL: http://ai-dashboard-secure-app-dashboard-1:8000
Docker-hálózat: ai-dashboard-secure-app_dashboard-control
Régi gyökérútvonalak: /api,/static,/assets,/ws
```

The Spark host mapping **127.0.0.1:8380** stays unchanged. The internal
Docker service runs on **8000**. Do not use `127.0.0.1:8380` as the Caddy
container's upstream: loopback inside a container points to that container.

## Root-relative assets / APIs

Applications that natively support a path prefix need only their
`/<app-id>/` route.

AI-Dashboard currently uses root-relative URLs (`/api`, `/static`,
`/assets`, `/ws`). Its four compatibility aliases are routed
**only** to AI-Dashboard, and remain behind the same N:M authorization.
No AI-Dashboard source changes are necessary for these declared routes.

**Limitation:** two applications cannot both claim `/api`, `/static`,
`/assets`, or another identical root-relative route on the same hostname.
The generator rejects collisions instead of silently serving the wrong
application. To support multiple apps that require the same root routes,
those applications must support an application-specific base path, or
an additional path-rewriting strategy must be implemented and tested.
The current implementation does not claim universal unmodified-app support.

No other port, Docker workload, or application is changed by registering
the URL. The CLI generates the shared-host Caddy site but never reloads it.

## Security

After signing in at `ai.code.hu`, navigate to:

```text
https://ai.code.hu/applications/ai-dashboard/launch
```

The login API verifies the existing N:M grant, produces a short-lived,
single-use Redis ticket, and sends the browser to:

```text
https://research.code.hu/ai-dashboard/_secure-login/redeem?ticket=...
```

Caddy routes the ticket redemption to Secure Login. The resulting
Secure, HttpOnly, same-host, per-app cookie is used when loading
`/ai-dashboard/`, and for its reserved `/api`, `/static`,
`/assets`, and `/ws` paths. All requests recheck permissions. Revoking
the N:M grant denies new requests immediately.

Each app has its own signed cookie name; a cookie for one app does not
authorize a different app.

Note: logging out of `ai.code.hu` does not yet independently revoke
an already minted application cookie; revoking the grant does. This is a
current session-lifecycle limitation, not a new login behavior.

## Integration / activation (operator controlled)

The following are documented steps, **not executed on production**:

1. Back up the Secure Login PostgreSQL data and files; do not destroy volumes.
2. Check the `research.code.hu` DNS resolves to the Spark's public HTTPS
   endpoint (same endpoint as `ai.code.hu`).
3. Update to the reviewed Secure Login branch/release.
4. Register/adjust app URL in `admin.sh` menu **8**.
5. Check generated site `caddy/app-gateway-generated/research-gateway.caddy`.
6. Validate the Compose overlay and the Caddy config before deployment.
7. Explicitly deploy/recreate only Secure Login API and Caddy with overlay.

Example syntax checks with actual existing Compose inputs:

```bash
cd /Data/containers/secure-login
docker compose -f docker-compose.yaml -f compose.app-gateway.yaml config --quiet
docker compose -f docker-compose.yaml -f compose.app-gateway.yaml \
  run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

After backup and validation, only when ready for a maintenance window:

```bash
docker compose -f docker-compose.yaml -f compose.app-gateway.yaml \
  up -d --no-deps --build api caddy
```

No modification/rebuild of AI-Dashboard, codex-worker, ASR, database, or
Redis is planned. **Never use `docker compose down -v`**.
Ensure Caddy can join the named, already existing Dashboard Docker network.

## Acceptance checklist

- Secure Login continues to work at `https://ai.code.hu/`.
- `https://research.code.hu/ai-dashboard/` denies anonymous sessions.
- Authenticated but unassigned user denied.
- Assigned user can launch and load HTML and `/static` and `/assets`.
- `/api` GET/POST and WebSocket `/ws` work through Caddy.
- Revocation immediately denies HTML, API, asset and WebSocket requests.
- A second app claiming `/api` must be rejected.
- AI-Dashboard remains accessible at its unchanged local 8380 endpoint.

The CI suite verifies format, Caddy syntax, DB changes, N:M authorization,
ticket replay denial, and router generation. Live Spark functionality and
DNS/TLS reachability require separate tests before production deployment.
