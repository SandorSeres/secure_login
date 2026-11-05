# Spark Secure Login source recovery — isolated candidate

Reconstructed from the user-supplied ARM64 image's application directory. The
runtime source is preserved under `api/app`, with narrowly scoped changes for
an **optional** user↔portal many-to-many model. This is **not** an approved
production upgrade or a complete reproduction of the original container
layers or deployment environment. The source snapshot does not include the
original image's exact build dependencies or external deployment files.

## Safety gates

* `ENABLE_MULTI_PORTAL=false` by default, retaining legacy 1:1 admin behavior.
* Multi-portal grants require an explicit, manually reviewed PostgreSQL schema
  migration. The API never changes the grants schema on startup.
* `ENABLE_MULTI_PORTAL=true` without the verified new DB key fails closed.
* `/auth/resolve` with ambiguous portal grants returns 403, not the first
  grant. Authenticated requests can select a grant with `portal_id=<id>`;
  the **Caddy** trusted-route integration for that parameter must be built
  and E2E-tested before any multi-app exposure. Do not allow arbitrary
  `X-Authenticated-Upstream` headers from clients.
* Existing `set_portal` remains legacy-only; explicit `grant_portal` and
  `revoke_portal` admin endpoints require CSRF and admin authentication.
* No files in this archive contain the user's live `.env` values.

## Isolated test (no Docker socket or production DB)

```bash
PYTHONPATH=api python3 -m unittest discover -s tests -v
python3 -m compileall -q api/app
```

## Image build candidate

```bash
docker buildx build --platform linux/arm64 -f api/Dockerfile \
  -t sseres/secure-login:local-candidate --load api
```

The resulting image must NOT be deployed to the live auth service.
The Dockerfile and pinned dependencies are provisional until full ARM64
reproducibility and live image provenance are compared. Image publication
requires its own reviewed version tag, Docker Hub authentication, release
and integration gates. Never use `latest` or an unverified manifest digest.

## Migration outline

1. Back up the PostgreSQL database and prove restore works on a clone.
2. Rehearse startup/session, roles, registration and password reset on clone.
3. Inspect old access table constraints; verify exactly one grant per user
   and one user per portal before conversion.
4. On the clone only, run
   `python -m app.multi_portal_migration` (dry check); after approval,
   `python -m app.multi_portal_migration --execute --acknowledge-backup-and-downtime`.
5. Enable `ENABLE_MULTI_PORTAL=true` only after migration and run full
   forward-auth / Caddy path isolation E2E.
6. Test multi-user shared portals and multi-portal users, denied access,
   CSRF, cookie and session isolation, restart and backup/restore.

Caution: migration redefines unique constraints. Reverse migration is not a
safe generic DROP/CREATE operation once multiple grants exist.
