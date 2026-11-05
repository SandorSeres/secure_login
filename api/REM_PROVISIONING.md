# Opt-in REM application provisioning

This feature is **disabled by default** and does not migrate the legacy
`user_portal_access` schema. Deploy it only after independent N:M database
migration rehearsal and explicit operator approval.

## Configuration

- `ENABLE_MULTI_PORTAL=true`, only when schema PK is `(user_id, portal_id)`.
- `REM_PROVISIONING_TOKEN`: 32+ character high-entropy secret shared only
  with the host-side REM operator. Never check secrets into source control.
- Existing `PORTALS_JSON` continues to manage existing portal entries.
  The `managed_portals` metadata table retains REM-managed entries across
  restarts; configured portal IDs cannot be replaced using this API.

## API (host loopback only)

All requests use `Authorization: Bearer <secret>`. Without multiportal and
token, service API returns 404; invalid token returns 401.

- `POST /internal/rem/instances` with JSON PortalConfig
  (`id,name,url,upstream,enabled`), validated HTTPS public URL, loopback
  `127.0.0.1:8000..8999` upstream. Creation and exact-repeat are
  idempotent. Conflicting IDs or upstreams return 409.
- `GET /internal/rem/instances/{id}` returns registered managed metadata.
- `POST /internal/rem/instances/{id}/grants` with JSON `{"user_id": 123}`
  grants an existing user access to an enabled managed instance.
- `DELETE /internal/rem/instances/{id}/grants/{user_id}` revokes.
  No browser session or CSRF token is involved; this is an isolated
  service-to-service credential, not an admin-user bypass.

**Gateway requirement:** The Caddy public auth site must explicitly deny
`/internal/rem/*` before proxying to IAM. The REM host operator calls
`127.0.0.1:8100` directly; never expose the service credential API publicly.

This API does **not** modify Caddy, restart apps, automatically grant users,
or publish research instances. Operators review a protected Caddy route with
`/auth/verify?portal_id=<instance>`, then verify HTTP 401/403/200 and
revocation before publication.

## Regression checklist

- Legacy single portal login and `/auth/resolve` remain unchanged while the
  feature is disabled.
- Configured portal startup sync is unchanged; a REM-managed portal survives
  restarting the IAM with the same `PORTALS_JSON`.
- Unknown, disabled, or ungranted managed portals remain inaccessible.
- Service API responds 404 without opt-in; 401 without correct bearer.
- New managed users can have N:M grants and ordinary configured users retain
  their grants. Grant revocation is immediately enforced by `/auth/verify`.
- Check negative IAM/Caddy E2E; do not infer runtime success from unit tests.
