# Multi-application access layer for REM (feature branch)

This additive feature registers application **instances** and creates a many-to-many User ↔ ApplicationInstance grant mapping, with role and active flag. The existing `users` table and login/session code are unchanged. **All grants deny by default**: an instance starts disabled, and a user needs an enabled grant.

## APIs (all require IAM_ADMIN_TOKEN_FILE bearer credential)

- `POST /iam/admin/instances` — idempotently register disabled app instance on a loopback upstream
- `PUT /iam/admin/instances/{key}/grants` — grant an existing user a role
- `DELETE /iam/admin/instances/{key}/grants/{user_id}` — revoke grant
- `PUT /iam/admin/instances/{key}/enabled?enabled=true` — enable after health/route checks
- `GET /iam/admin/instances/{key}/access/{user_id}` — allow/deny decision for trusted gateway

The dedicated secret is an initial internal service-auth boundary. **This does not implement OIDC or integrate the portal/gateway with authorization checks.** Current sessions, browser access and existing reverse proxy routes remain unchanged. An actual production rollout requires the gateway to authenticate user sessions, resolve stable ID, call the internal access API, strip spoofable headers, and fail closed. Existing protected routes must not be implicitly opened by new grants.

## Database rollout

The IAM classes are imported before existing `Base.metadata.create_all`, which creates only missing tables. Existing `users` table columns are not modified. Before deployment on Spark: back up PostgreSQL, inspect generated SQL, and test a database copy. Prefer a reviewed Alembic migration for production schema lifecycle; automatic create_all is only bootstrap-friendly. No existing users receive grants automatically.

## Known legacy security concern

The existing `/admin/set_level` endpoint appears to lack explicit authentication. Treat this as a separate urgent hardening issue; do not expose that route publicly. It is not rewritten by this minimal additive feature.

## Acceptance

Test: missing/incorrect secret denies access, loopback upstream validation, disabled-by-default, multiple instances per user, multiple users per instance, revocation, role updates, no legacy login regressions, PostgreSQL backup/migration dry-run and proxy tests before merge/deploy.
