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

An issued cookie carries an identifier and the id of the Secure Login
session that minted it. Both are server-side records, so a cookie can
never outlive the login that produced it: when that session is ended by
logout, a password change or an administrative action, or simply
expires, the cookie stops working even if somebody copied it. Logout
ends only the session that clicked it, so a second device stays
signed in; a password change or a level change ends every session of
that user.

Logging out of `ai.code.hu` cannot delete a cookie that lives on the
shared application hostname, so the revocation works from the records
the gateway wrote when it issued them. Revoking the application grant
remains the way to block one app for one user.

Native WebSocket connections that are already established still need a
reconnect (or connection-level revocation) before a change takes effect.

For security, the generated Caddy site removes the gateway cookie
before proxying to an upstream. Do not route applications requiring
their *own* Cookie header through this configuration without separate
integration work.

## Session storage

Every key is namespaced under `secure-login:` in Redis (`REDIS_URL`);
password-reset tokens use `reset:`. Losing Redis logs users out of the
applications, which is the intended fail-closed behavior.

    secure-login:session:<username>:<jti>        the login itself
    secure-login:session-owner:<scope_id>        "<username>:<jti>" owning it
    secure-login:app-scope:<scope_id>            a live login's binding record
    secure-login:app-cookie:<scope>:<app>:<id>   a cookie the gateway issued
    secure-login:app-cookie-revoked:<id>         denied cookie identifiers
    secure-login:app-ticket:<sha256>             a 60-second launch ticket

A `scope_id` is the handle of one login. It travels only inside the
short-lived ticket, which this service writes and only this service
reads: neither a cookie nor a header can carry it, because Caddy strips
cookies before reaching an application host and an upstream must never
learn a revocation handle. A ticket whose scope has no live record
issues an `unscoped` cookie, restable only by a revocation that ends
everything.

## Which application a request belongs to

`X-Gateway-App` names the application in both `authorize` and the
upstream request. A client must not get to choose it, and the value is
set with `request_header` *before* `forward_auth`: Caddy builds the
authorization subrequest from the incoming headers, so a value set only
inside the block would leave a client-sent one in place.

`redeem` is reached at the application host and no `forward_auth` has
run by then, so it has no header to trust. It resolves the application
from the `Host` and path the proxy actually routed, matched against the
configured public addresses. A header that disagrees with that address
resolves to nothing, so neither a client nor one application's backend
can have this host mint another application's cookie.

## WebSocket and browser writes

`forward_auth` copies the client's `Upgrade` and `Connection` headers into
its own subrequest. Secure Login serves no WebSocket, so uvicorn would
answer an upgraded subrequest with `403` and every handshake would fail
authorization. The block therefore sends `header_up -Upgrade` and
`header_up -Connection`: the authorize call is always an ordinary HTTP
request, while the real request is still proxied upgraded. The origin
check keeps working because `X-Original-Upgrade` carries what the client
asked for, and a WebSocket handshake or a `POST`/`PUT`/`PATCH`/`DELETE`
must originate on the application's own origin.

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
