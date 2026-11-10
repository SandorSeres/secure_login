# Application URL registration and protected access (opt-in)

Scope: **Secure Login only.** No changes to existing user records, login endpoints,
account levels, grants or application workloads. All routes fail closed.

## URL concepts

Register each application with:

- **Public URL:** a distinct, DNS-resolving HTTPS origin, e.g. `https://dashboard.example.com`. This is the address in the browser. It must **not** reuse the Secure Login host.
- **Internal URL:** a private HTTP origin *reachable from the Secure Login Caddy container*, e.g. `http://ai-dashboard-secure-app-dashboard-1:8000`. Do **not** enter `http://127.0.0.1:8380`: inside the Caddy container, 127.0.0.1 refers to Caddy, not the Spark host.
- **Docker network:** optional existing network to attach the Caddy container to, e.g. `ai-dashboard-secure-app_dashboard-control`.

For the separately deployed AI-Dashboard:
- Host-mapped port: `127.0.0.1:8380` (unchanged).
- Container's HTTP port: `8000`.
- Docker network: verify the exact name using
  `docker inspect ai-dashboard-secure-app-dashboard-1 --format '{{json .NetworkSettings.Networks}}'`.
- If the network is `ai-dashboard-secure-app_dashboard-control`, the internal URL may be
  `http://ai-dashboard-secure-app-dashboard-1:8000` after Caddy joins that network.
- A separate public hostname, e.g. `dashboard.example.com`, needs a DNS record and valid TLS before activation.

## Admin operations

```bash
cd /Data/containers/secure-login
bash admin.sh
```

- **4:** create a new app and enter both URLs plus optional Docker network.
- **8:** update the URLs of an existing app (including `ai-dashboard`) without recreating the app, user or grants.
- **5:** grant `ai-dashboard` to `sseres`; **6** revokes the grant.
- **3:** list apps, registered URLs and grant counts.

The CLI creates only the new `application_targets` table if absent and generates
`caddy/app-gateway-generated/*.caddy` and `compose.app-gateway.yaml`.
It **does not** restart a service automatically.

The URL configuration and user/app grant are distinct. Changing the URLs
does not change existing N:M authorizations.

## First deployment: validate before activating

Back up the Secure Login PostgreSQL database and configuration before upgrading
an existing production installation. Never remove volumes or reset users.

After fetching the tested branch / reviewed release, and configuring at least
one application URL:

```bash
cd /Data/containers/secure-login

docker compose -f docker-compose.yaml -f compose.app-gateway.yaml config --quiet
docker compose -f docker-compose.yaml -f compose.app-gateway.yaml \
  run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

The generated external Docker network(s) **must already exist** before the
network overlay can be used. Ensure public HTTPS DNS resolves to the Caddy host
and existing 80/443 routing is preserved.

Once validation passes and the existing app network is available, deploy only
the changed Secure Login **API and Caddy** services:

```bash
docker compose -f docker-compose.yaml -f compose.app-gateway.yaml \
  up -d --no-deps --build api caddy
```

This does **not** rebuild AI-Dashboard, worker, ASR, PostgreSQL or Redis.
Always include the generated overlay in subsequent Secure Login Compose
operations that recreate Caddy. Never run `docker compose down -v`.

## User access

1. Sign in to Secure Login at its existing HTTPS hostname.
2. Navigate to `https://<login-host>/applications/ai-dashboard/launch`.
3. The server checks the login session and N:M grant and issues a **single-use,
   60-second** Redis ticket, then redirects to the registered public app URL.
4. Caddy redeems the ticket; a signed, Secure/HttpOnly, app-host-only cookie
   is set (six-hour maximum).
5. **Every** subsequent HTTP request and WebSocket upgrade passes Caddy
   `forward_auth`, which verifies the cookie and **rechecks N:M access**.
   Revocation takes effect on the next request.

An unauthenticated or unassigned user cannot reach the Caddy app upstream.
The existing `127.0.0.1:8380` host binding remains for trusted local
administration; this proxy cannot revoke access to that local-only listener
from users who already have local host access.

## Security and deployment limitations

- Existing login endpoints, cookie attributes, passwords and access levels
  are unchanged.
- There is no cross-host sharing of the Secure Login session cookie. The
  one-time ticket creates a separate, app-specific cookie. Logging out of the
  login host does **not** immediately expire already issued app cookies;
  revoke the app grant to terminate app access immediately.
- Each application needs a separate public HTTPS hostname. This avoids
  modifying the application's root-relative URLs, APIs and WebSocket paths.
- Only operator-registered targets are proxied; the public cannot supply a
  destination URL. Private backends should not expose their own public bypass.
- Changes to the registered internal origin require regeneration and explicit
  Caddy reload/recreation. The CLI renders files but never silently reloads.
- Generate/validate/redeploy with the **same Compose overlay**. If a Docker
  network is renamed or removed, correct the admin target/network first.
- This is not an automatic migration to production. Test Caddy routing,
  authentication, revocation, static files, job APIs and WebSocket connections
  on the Spark before merging or considering the deployment accepted.
