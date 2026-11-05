# ARM64 Secure Login build candidate for REM (not production-approved)

This repository's current API can be built as a Linux ARM64 image for
isolated technical evaluation. **It has not been proven equivalent to the
live Secure Login deployed on the DGX Spark. Do not replace, migrate, or
connect it to live PostgreSQL/Redis/Caddy.**

## Why publication is blocked

The current published repository config in `api/app/config.py` expects
`DATABASE_URL` (and `SECRET_KEY`, `CSRF_KEY`), but REM's proposed
`deploy/platform/compose.yaml` supplies `PG_USER`, `PG_PASSWORD`,
`PG_DB`, `PG_HOST` and other portal/IAM settings. The source has a
`/health` route, but no verified `/auth/resolve` endpoint, `PORTALS_JSON`,
`ROLES_JSON`, or `ADMIN_USERNAMES` contract.

Do **not** infer compatibility from the repository name or a successful
Docker build. Production Secure Login may be deployed from a different,
newer tree. Determine and audit the exact live source commit without
changing the running service. Preserve existing user/session/portal
associations and define a separately tested many-to-many IAM migration.

The build/upload helper refuses `--push` if the exact Git tag's source
fails the static REM contract marker check. Passing this marker check
is **necessary, not sufficient**: an isolated database, session, proxy,
mail, IAM and zero-portal E2E test remains mandatory.

## Fetch and build a reviewed release tag

```bash
git fetch origin main --tags
git switch main
git pull --ff-only
bash -n scripts/build_upload.sh
python3 scripts/check_rem_contract.py # currently expected to FAIL
```

Once a reviewed release commit is merged and a **new** immutable version
tag is created and pushed, the default build mode can be run as:

```bash
bash scripts/build_upload.sh --version vX.Y.Z
```

The target is `sseres/secure-login:vX.Y.Z-arm64` by default.
No release tag or Docker Hub repository is created automatically by
these changes. The script builds from `git archive` of the reviewed
remote tag rather than uncommitted local files.

After compatibility review and explicit authorization:

```bash
docker login -u sseres
bash scripts/build_upload.sh --version vX.Y.Z --namespace sseres --push
```

This prints the immutable `sseres/secure-login@sha256:...` reference.
It does not publish REM, alter Caddy/IAM, launch services or write a
release manifest.

## Separate release gates

1. Identify exact live Secure Login source and compare API/config/database
   behavior to this repository.
2. Verify auth resolve contract, empty portal startup, session preservation,
   roles, admin identities and many-to-many IAM with an isolated database.
3. Confirm ARM64 build, cold start, migration and regression tests.
4. Confirm an explicit Docker Hub publication decision and digest inspection.
5. Only after these gates: update REM `AUTH_IMAGE` and run a separately
   approved zero-instance installation test.
