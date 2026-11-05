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

## GitHub Actions: kézi ARM64 Docker Hub candidate kiadás

A `.github/workflows/secure-login-arm64-release.yml` release workflow
**csak manuálisan** indítható. A `publish` alapértéke `false`.
A `main` aktuális, ellenőrzött commitját buildeli, amelyből az
`sseres/secure-login:sha-<git-sha>` kandidátus image készül.
A teljes commit SHA megőrzi a forrásazonosságot, a telepítéshez
mindig a tényleges `@sha256:` registry digestet használd.

A Secure Login GitHub-repository saját Actions-beállításai szükségesek;
a REM repositoryban konfigurált secrets **nem osztódnak meg**
automatikusan. A közzétételhez:
- `REM_IMAGE_NAMESPACE=sseres` repository variable;
- `DOCKERHUB_PUBLISH_ENABLED=true` csak engedélyezett kiadási időablakban;
- `DOCKERHUB_USERNAME` és `DOCKERHUB_TOKEN` repository secrets;
- `dockerhub-publish` GitHub Environment, lehetőleg required reviewerrel,
  csak `main` branchről való publikálásra korlátozva.

A `release-build-only` környezet nem publikál. Első futtatásnál
`publish=false`, utána a teljes CI és a célrepository ellenőrzése
után tudatosan `publish=true`.

A sikeres publikálás `secure-login-published-image.json`
artifactot ad. Ez **candidate**, nem engedélyezett éles upgrade:
a `production_approved=false` mező szándékosan jelzi ezt.
A többportálos Caddy útválasztás és az éles PostgreSQL migráció
szándékosan nincs aktiválva.

A Docker Hubon kézzel történő feltöltés alternatívája a
`scripts/build_upload.sh`, amelyhez előzetesen létrehozott és
feltöltött Git kiadási tag kell.

**Publikálás után állítsd vissza**
`DOCKERHUB_PUBLISH_ENABLED=false` értékre.

## Egyszerű Docker Hub-feltöltés a Sparkon – Git-tag létrehozása nélkül

A `build_upload.sh --from-main` a **távoli, ellenőrzött main commit**
tartalmából épít; a helyi, nem commitolt fájlokat nem használja.
Az image neve `sseres/secure-login:sha-<TELJES_COMMIT_SHA>`.

```bash
cd /Data/containers/secure_login
git fetch origin main
git switch main
git pull --ff-only
bash -n scripts/build_upload.sh

# Csak ARM64 build, nincs feltöltés:
bash scripts/build_upload.sh --from-main

# Külön jóváhagyott kandidátus feltöltése a Docker Hubra:
bash scripts/build_upload.sh --from-main --namespace sseres --push
```

**Fontos:** ez kandidátus image-et tesz a Docker Hubra, és nem
telepíti éles rendszerként. A `--push` végrehajtásához előzetes
`docker login -u sseres` szükséges. A script a kimeneten
`PUBLISHED_REFERENCE=sseres/secure-login@sha256:...` értéket ad.
A pontos digest ellenőrzése:

```bash
docker buildx imagetools inspect 'sseres/secure-login@sha256:<VALODI_DIGEST>'
```

A `<VALODI_DIGEST>` helyőrző: a script által visszaadott konkrét
értékre cserélendő. A Docker Hub feltöltés **nem** módosítja a futó
Secure Login/Caddy/PostgreSQL/Redis szolgáltatásokat.
