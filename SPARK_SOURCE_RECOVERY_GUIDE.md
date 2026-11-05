# Spark Secure Login source recovery for REM – isolated release candidate

**Status:** The publicly hosted `secure_login` main branch does not yet
contain the application source extracted from the live DGX Spark ARM64 image.
This feature branch prepares safe import. **Do not merge or publish until
the recovered source is imported and CI has passed.**

## Provenance and constraints

The Spark Docker archive `auth-image-arm64.tar` contains
`localhost/secure-login-auth:dgx`, built for `linux/arm64` on
2026-10-11. The Docker image has **no OCI source/revision labels**; no
Git commit can be verified from the image alone. The user recovered
`app/*.py` plus templates and CSS as a ZIP. The supplied configuration
has been used as a *shape* reference only: real credentials, usernames,
email addresses and application upstreams must not be committed.

The restored development bundle is a **candidate**, not bit-for-bit proof
that its Dockerfile and Python dependencies reproduce the original image.
It preserves the recovered application modules and adds a gated,
default-disabled many-to-many IAM experiment.

## Import a reviewed source bundle to this branch

The reviewer must first examine the extracted source ZIP for credentials
and destructive migrations. The SHA-256 checksum supplied separately
prevents accidental input changes. The importer requires a clean Git
working tree and this exact branch; it rejects ZIP traversal, symlinks,
unexpected paths and obvious private key blocks.

```bash
git fetch origin
git switch --track origin/feature/spark-source-recovery-multi-portal
# Or, if the local branch already exists:
# git switch feature/spark-source-recovery-multi-portal
# git pull --ff-only

python3 scripts/import_spark_candidate.py /path/to/secure-login-rem-candidate.zip \
  --expected-sha256 <SHA256>  # validation only

# After security review and explicit release-source approval:
python3 scripts/import_spark_candidate.py /path/to/secure-login-rem-candidate.zip \
  --expected-sha256 <SHA256> --apply --commit-and-push
```

No production containers, databases, Caddy routes, auth state or Docker
Hub repositories are touched by this import. `--commit-and-push` changes
only the feature branch.

## Required CI and release gates

1. Source import is visible as plaintext Git files in the feature branch.
2. Python import and regression tests pass, including registration,
   session, password reset, CSRF, roles and grants.
3. Linux ARM64 build succeeds; application boots with a fresh disposable
   PostgreSQL/Redis instance and zero configured portals.
4. Migration from the 1:1 access model is rehearsed on a *restorable copy*
   of the production schema, not the live database.
5. The authenticated Caddy `/auth/resolve` and per-instance routing
   behavior is checked. The existing common `research.code.hu` route
   cannot distinguish multiple portals by itself; it must not accidentally
   route to an arbitrary granted upstream.
6. Merge only after successful tests, then create an immutable version tag.
7. Build and explicitly publish using `scripts/build_upload.sh --version
   <reviewed-tag> --push` with Docker Hub operator authorization.
8. Inspect `sseres/secure-login@sha256:...` and record its actual registry
   digest in the REM release manifest. Publishing is distinct from
   replacing the live Secure Login image.

**No production database schema changes or live image replacement are
authorized by these steps.**
