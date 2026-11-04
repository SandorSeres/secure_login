# Secure Login ↔ REM authorization gate (not enabled)

The opt-in route `GET /iam/gateway/authorize/{instance_key}` validates the **existing signed Secure Login browser session**, looks up the user by username in the DB, applies the existing inactivity timeout and checks that both the instance and the user's grant are enabled.

Results:
- `204 No Content`: valid session, enabled application and enabled grant.
- `401 Unauthorized`: absent/invalid/expired session.
- `403 Forbidden`: no grant, revoked grant, unknown/disabled instance.

The route does **not** accept a user ID, username or role from browser headers, URL query parameters or REM. Client-supplied `X-User-ID` and similar identity headers cannot grant access. The successful response contains no identity information and is marked `Cache-Control: no-store`.

This is an authorization **decision endpoint**, not yet an enforcement gateway. The existing Nginx configuration is deliberately unchanged; no new application routes are published. A reviewed opt-in example is in `docs/examples/nginx-single-app-auth.conf`. The sample is not deployed or included.

## Production constraints

1. Attach Nginx and each application to explicitly defined **private Docker networks**; a container's `127.0.0.1` is not the Spark host, and a host-loopback service cannot be reached as `127.0.0.1` from Nginx. The current IAM stored `upstream` is metadata, not yet an automatic routing instruction.
2. Each app must be published through a **statically provisioned** trusted route rather than a user-controlled proxy target.
3. Verify the app supports URL path prefixes and WebSocket traffic; use an isolated vhost if it does not. Nginx `auth_request` must also cover WebSocket handshake and all static/API/download paths.
4. Pass the browser session cookie only to the **internal** authorization subrequest; strip all incoming identity headers to the upstream app. Never let an app infer private RAG authorization solely from this edge gate.
5. Test allow/deny for two users, two apps, disabled apps, revoked grants, invalid/expired sessions and direct bypass attempts against upstream ports.
6. Confirm the gateway can actually reach the IAM decision endpoint. A `401` can be mapped to login only after the browser redirect/cookie flow is validated; do not cache authorization subrequests.
7. Do not merge or deploy before independent gateway configuration validation (`nginx -t`) and end-to-end exercises using an isolated Compose stack. Do not change the running Secure Login stack without an approved backup/rollback plan.

## Implementation scope

This branch adds only the session-bound authorization route and API-level tests. It does not modify the active Nginx configuration, alter session formats, implement OIDC/SSO to individual applications, modify REM, or automatically deploy applications.
