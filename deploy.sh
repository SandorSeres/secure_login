#!/usr/bin/env bash
# deploy.sh — Cloud Run deploy a Secure Login API-hoz
# Használat:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Követelmények:
# - gcloud CLI (bejelentkezve: `gcloud auth login`)
# - egy GCP projekt (PROJECT_ID)
# - Docker
# - opcionális: .env a repo gyökerében (APP_HOST, DATABASE_URL, SMTP_*, SECRET_KEY, CSRF_KEY, stb.)

set -euo pipefail

### ─────────────────────────────────────────────────────────────────────────────
### 0) Alapkonf – igény szerint módosítható
### ─────────────────────────────────────────────────────────────────────────────

# GCP
PROJECT_ID="${PROJECT_ID:-}"                 # ha nincs exportálva, kérdezzük be
REGION="${REGION:-europe-west1}"            # pl. europe-west1
REPO_NAME="${REPO_NAME:-secure-login}"      # Artifact Registry repo
IMAGE_NAME="${IMAGE_NAME:-secure-login-api}"# Image név
SERVICE_NAME="${SERVICE_NAME:-secure-login-api}" # Cloud Run service

# Build context és Dockerfile (API)
API_DIR="${API_DIR:-api}"
API_DOCKERFILE="${API_DOCKERFILE:-api/Dockerfile}"

# Cloud Run beállítások
CPU="${CPU:-1}"                  # vCPU
MEMORY="${MEMORY:-512Mi}"        # RAM
CONCURRENCY="${CONCURRENCY:-80}" # egyidejű kérések / instance
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-4}"
ALLOW_UNAUTH="${ALLOW_UNAUTH:-true}"  # publikus? (true/false)

# Cloud SQL opció (ha használod; különben hagyd üresen)
CLOUDSQL_INSTANCE="${CLOUDSQL_INSTANCE:-}"  # pl. project:region:instance
VPC_CONNECTOR="${VPC_CONNECTOR:-}"          # pl. projects/PROJ/locations/REGION/connectors/CONNECTOR

# .env fájl helye
ENV_FILE="${ENV_FILE:-.env}"

### ─────────────────────────────────────────────────────────────────────────────
### 1) .env beolvasása (ha létezik)
### ─────────────────────────────────────────────────────────────────────────────
if [[ -f "${ENV_FILE}" ]]; then
  echo "🔎 Betöltöm a .env változókat: ${ENV_FILE}"
  # csak azokat a sorokat sourcoljuk, amik KEY=VALUE formában vannak és nem kommentek
  set -o allexport
  # shellcheck disable=SC2046
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${ENV_FILE}" | sed 's/\r$//')
  set +o allexport
else
  echo "ℹ️  Nincs .env a gyökérben — környezeti változókból dolgozom."
fi

### ─────────────────────────────────────────────────────────────────────────────
### 2) Projekt ellenőrzés / beállítás
### ─────────────────────────────────────────────────────────────────────────────
if [[ -z "${PROJECT_ID}" ]]; then
  read -r -p "Add meg a GCP PROJECT_ID-t: " PROJECT_ID
fi
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "✅ Project: ${PROJECT_ID}"
echo "✅ Régió:  ${REGION}"

### ─────────────────────────────────────────────────────────────────────────────
### 3) API-k engedélyezése
### ─────────────────────────────────────────────────────────────────────────────
echo "🛠  API-k engedélyezése (ha szükséges)..."
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  --quiet

### ─────────────────────────────────────────────────────────────────────────────
### 4) Artifact Registry repo létrehozása (ha nem létezik)
### ─────────────────────────────────────────────────────────────────────────────
if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" >/dev/null 2>&1; then
  echo "🗂  Létrehozom az Artifact Registry repót: ${REPO_NAME} (${REGION})"
  gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Secure Login images" \
    --quiet
else
  echo "🗂  Artifact Registry repo már létezik: ${REPO_NAME}"
fi

REGISTRY_HOST="${REGION}-docker.pkg.dev"
IMAGE_URI="${REGISTRY_HOST}/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:$(date +%Y%m%d-%H%M%S)"

### ─────────────────────────────────────────────────────────────────────────────
### 5) Docker build & push
### ─────────────────────────────────────────────────────────────────────────────
echo "🐳 Build: ${IMAGE_URI}"
docker build -f "${API_DOCKERFILE}" -t "${IMAGE_URI}" "${API_DIR}"
echo "📤 Push: ${IMAGE_URI}"
docker push "${IMAGE_URI}"

### ─────────────────────────────────────────────────────────────────────────────
### 6) Secretek létrehozása / frissítése (Secret Manager)
###     Itt csak a TÉNYLEG érzékeny értékeket tegyük secretbe.
### ─────────────────────────────────────────────────────────────────────────────
ensure_secret() {
  local name="$1"
  local value="$2"

  if [[ -z "${value}" ]]; then
    echo "⚠️  Secret kihagyva (üres): ${name}"
    return 0
  fi

  if ! gcloud secrets describe "${name}" >/dev/null 2>&1; then
    echo "🔐 Secret létrehozása: ${name}"
    printf "%s" "${value}" | gcloud secrets create "${name}" --data-file=-
  else
    echo "🔐 Secret frissítése: ${name}"
    printf "%s" "${value}" | gcloud secrets versions add "${name}" --data-file=-
  fi
}

# Érzékeny értékek (ha vannak a környezetben / .env-ben)
ensure_secret "secure-login-SECRET_KEY"   "${SECRET_KEY:-}"
ensure_secret "secure-login-CSRF_KEY"     "${CSRF_KEY:-}"
ensure_secret "secure-login-SMTP_PASSWORD" "${SMTP_PASSWORD:-}"

### ─────────────────────────────────────────────────────────────────────────────
### 7) Cloud Run deploy
### ─────────────────────────────────────────────────────────────────────────────

# Nem-érzékeny env változók gyűjtése (csak ha vannak definiálva)
ENV_VARS=()

add_env_if_set () {
  local key="$1"
  local val="${!1:-}"
  if [[ -n "${val}" ]]; then
    ENV_VARS+=("${key}=${val}")
  fi
}

# Alkalmazás-specifikusak
add_env_if_set "APP_HOST"
add_env_if_set "DATABASE_URL"
add_env_if_set "SMTP_HOST"
add_env_if_set "SMTP_PORT"
add_env_if_set "SMTP_USER"
add_env_if_set "MAIL_FROM"
add_env_if_set "SMTP_SSL"
add_env_if_set "SMTP_STARTTLS"
add_env_if_set "SESSION_TIMEOUT_MINUTES"
add_env_if_set "SESSION_COOKIE_NAME"
add_env_if_set "CSRF_COOKIE_NAME"
add_env_if_set "RATE_LIMIT_REDIS_URL"  # ha van dedikált Redis URL
add_env_if_set "REDIS_HOST"
add_env_if_set "REDIS_PORT"

# --set-env-vars argumentum összeállítása
SET_ENV_ARGS=()
if (( ${#ENV_VARS[@]} > 0 )); then
  IFS=, read -r -a CSV <<< "${ENV_VARS[*]}"
  SET_ENV_ARGS=( --set-env-vars "$(IFS=,; echo "${ENV_VARS[*]}")" )
fi

# --set-secrets argumentum
SET_SECRETS_ARGS=()
# csak akkor adjuk hozzá, ha tényleg létezik secret (külön ellenőrzés)
if gcloud secrets describe secure-login-SECRET_KEY >/dev/null 2>&1; then
  SET_SECRETS_ARGS+=( --set-secrets "SECRET_KEY=secure-login-SECRET_KEY:latest" )
fi
if gcloud secrets describe secure-login-CSRF_KEY >/dev/null 2>&1; then
  SET_SECRETS_ARGS+=( --set-secrets "CSRF_KEY=secure-login-CSRF_KEY:latest" )
fi
if gcloud secrets describe secure-login-SMTP_PASSWORD >/dev/null 2>&1; then
  SET_SECRETS_ARGS+=( --set-secrets "SMTP_PASSWORD=secure-login-SMTP_PASSWORD:latest" )
fi

# Auth publikusság flag
if [[ "${ALLOW_UNAUTH}" == "true" ]]; then
  AUTH_FLAG="--allow-unauthenticated"
else
  AUTH_FLAG="--no-allow-unauthenticated"
fi

# Cloud SQL és VPC opciók
SQL_ARGS=()
if [[ -n "${CLOUDSQL_INSTANCE}" ]]; then
  SQL_ARGS+=( --add-cloudsql-instances "${CLOUDSQL_INSTANCE}" )
fi
if [[ -n "${VPC_CONNECTOR}" ]]; then
  SQL_ARGS+=( --vpc-connector "${VPC_CONNECTOR}" )
  # Ha egress internetre is kell: --vpc-egress=all-traffic (opcionális)
fi

echo "🚀 Deploy Cloud Run szolgáltatás: ${SERVICE_NAME}"
gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE_URI}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --platform=managed \
  --cpu="${CPU}" \
  --memory="${MEMORY}" \
  --concurrency="${CONCURRENCY}" \
  --min-instances="${MIN_INSTANCES}" \
  --max-instances="${MAX_INSTANCES}" \
  "${AUTH_FLAG}" \
  "${SET_ENV_ARGS[@]}" \
  "${SET_SECRETS_ARGS[@]}" \
  "${SQL_ARGS[@]}" \
  --port=8000 \
  --quiet

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format='value(status.url)')"

echo ""
echo "✅ Kész! Szolgáltatás URL: ${SERVICE_URL}"
echo ""

# TIPP az APP_HOST-ra
if [[ -z "${APP_HOST:-}" ]]; then
  echo "💡 Javaslat: állíts be APP_HOST értéket, hogy a levélben lévő link teljes URL legyen."
  echo "   Példa (egyszeri futtatás után):"
  echo "     gcloud run services update ${SERVICE_NAME} --region=${REGION} \\"
  echo "       --set-env-vars APP_HOST=${SERVICE_URL}"
  echo ""
fi

echo "🎉 Deploy befejezve."

