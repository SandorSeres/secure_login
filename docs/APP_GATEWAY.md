# Secure Login app gateway — one research.code.hu hostname

## Routing contract

The historical domain separation is retained:

- `https://ai.code.hu` — Secure Login authentication and user accounts.
- `https://research.code.hu/` — the **single root application**, currently AI-Dashboard.
- `https://research.code.hu/apps/<app-id>/` — all other applications.
- Caddy routes to explicitly registered Docker service targets and calls Secure
  Login to enforce the existing N:M grant **before each request**.

There is **one DNS record** for `research.code.hu`, not one per application.
The current `ai.code.hu` Caddy site, existing login logic and the
`127.0.0.1:8380` host-only AI-Dashboard port remain unchanged.

## Admin menu

The additive `application_targets` table stores only
`application_id`, `public_url`, `upstream_origin`, and `docker_network`.
The existing `users`, `applications` and `user_application_access`
records are not rewritten.

In `bash admin.sh`:

- **4** registers a new application (with URL and network).
- **8** updates an existing application's URL and network **without deleting
  users or grants**.
- **5** grants an application to an existing user.
- **6** revokes an application grant.

For the existing `ai-dashboard`, use menu **8**:

```text
Alkalmazásazonosító: ai-dashboard
Nyilvános URL: https://research.code.hu
Belső cél-URL: http://ai-dashboard-secure-app-dashboard-1:8000
Docker-hálózat: ai-dashboard-secure-app_dashboard-control
```

The *host* port `8380` is preserved and is **not** the port used
by Caddy inside Docker. Caddy reaches the AI-Dashboard's container port
`8000` via the named Docker network.

For another app, e.g. `korrupcio-kutatas`, the external address must be:

```text
https://research.code.hu/apps/korrupcio-kutatas
```

Only one app may own the public root URL. A second root registration is
rejected by the catalog's unique constraint and gateway validation.

## Why the root portal matters

AI-Dashboard uses root-relative paths `/api`, `/static`, `/assets`
and `/ws`. Routing it at `research.code.hu/` preserves those paths
without changing AI-Dashboard code. Other apps may run under
`/apps/<app-id>/` if their frontend/API supports the path prefix or
the Caddy prefix stripping. Apps that use absolute `/api`, `/static`
or `/ws` **cannot be assumed to work** beneath a subpath without
app-specific testing.

The Caddy generator explicitly rejects unknown `/apps/*` paths rather than
falling back to the root portal.

## Secure Login integration

From an authenticated Secure Login session at `ai.code.hu`:

```text
https://ai.code.hu/applications/ai-dashboard/launch
```

The launch endpoint verifies the current N:M grant, then issues a
60-second single-use Redis ticket and redirects to the registered
`research.code.hu` redemption endpoint. Caddy redeems the ticket
through the Secure Login API and creates a Secure, HttpOnly, signed
cookie scoped by application identifier. Every subsequent request
requires this cookie and rechecks the N:M grant.

A revoked grant denies access on subsequent HTTP requests.
This also covers the root portal's APIs and static content.

Important known limitation: logout at `ai.code.hu` does not
revoke existing app cookies by itself; revoke the app grant
to immediately block that app. Native WebSocket connections that
are already established also require separate connection-level
revocation or reconnect to enforce changes after a handshake.

For security, the generated Caddy site removes the gateway cookie
before proxying to an upstream. Do not route applications requiring
their *own* Cookie header through this configuration without separate
integration work.

## Deployment and safeguards

The CLI only writes generated Caddy configuration and a Docker
Compose network overlay. It does **not** touch running containers.

Before activating on the Spark:
1. Back up Secure Login PostgreSQL data and configuration.
2. Check external DNS and HTTPS for `research.code.hu`.
3. Validate generated Caddy and Compose configuration.
4. Verify that Caddy can join the existing AI-Dashboard Docker network.
5. Recreate **only** the Secure Login API and Caddy with the generated overlay.
6. Test an assigned user, an unassigned user, revocation and app API paths.

No changes to AI-Dashboard images or application data are needed.
Do **not** use `docker compose down -v`.

Do not merge or deploy this PR without successful CI plus
Spark acceptance verification.
