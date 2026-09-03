#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  IMAGE_PREFIX=<registry>/<namespace>/<prefix> ./build_and_push.sh [options]
  ./build_and_push.sh

Options:
  --prefix <name>        Image prefix, for example sseres/secure-login.
  --namespace <name>     Backward-compatible alias for --prefix.
  --push                 Push the multi-platform image to the registry.
  --load                 Load a single-platform image into the local Docker daemon.
  --platforms <list>     Target platforms. Default: linux/amd64,linux/arm64.
  --tag <tag>            Image tag. Default: latest.
  --builder <name>       Docker buildx builder name. Default: secure-login-builder.
  --image <name>         Build only the specified image: auth. Default: auth.
  --dry-run              Print the resolved build plan without running Docker.
  -h, --help             Show this help.

Examples:
  ./build_and_push.sh
  ./build_and_push.sh --prefix sseres/secure-login --tag alpha --push
  ./build_and_push.sh --prefix ghcr.io/example/secure-login --push
  ./build_and_push.sh --prefix localhost:5000/secure-login --platforms linux/amd64 --load
EOF
}

prompt() {
  local label="$1"
  local default_value="$2"
  local value

  if [[ -n "$default_value" ]]; then
    read -r -p "$label [$default_value]: " value
    printf '%s' "${value:-$default_value}"
  else
    read -r -p "$label: " value
    printf '%s' "$value"
  fi
}

current_platform() {
  case "$(uname -m)" in
    arm64|aarch64) printf 'linux/arm64' ;;
    *) printf 'linux/amd64' ;;
  esac
}

PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
TAG="${TAG:-}"
BUILDER="${BUILDER:-secure-login-builder}"
OUTPUT_MODE=""
IMAGE_PREFIX="${IMAGE_PREFIX:-${IMAGE_NAMESPACE:-}}"
TARGET_IMAGE="${IMAGE_NAME:-auth}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix|--namespace)
      IMAGE_PREFIX="${2:?Missing value for $1}"
      shift 2
      ;;
    --image)
      TARGET_IMAGE="${2:?Missing value for $1}"
      shift 2
      ;;
    --push)
      OUTPUT_MODE="push"
      shift
      ;;
    --load)
      OUTPUT_MODE="load"
      shift
      ;;
    --platforms)
      PLATFORMS="${2:?Missing value for --platforms}"
      shift 2
      ;;
    --tag)
      TAG="${2:?Missing value for --tag}"
      shift 2
      ;;
    --builder)
      BUILDER="${2:?Missing value for --builder}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$TARGET_IMAGE" != "auth" ]]; then
  echo "Unknown --image value: $TARGET_IMAGE (expected auth)" >&2
  exit 2
fi

if [[ -z "$IMAGE_PREFIX" ]]; then
  IMAGE_PREFIX="$(prompt "Image prefix" "localhost/secure-login")"
fi

if [[ -z "$IMAGE_PREFIX" ]]; then
  echo "Image prefix is required, for example: sseres/secure-login" >&2
  exit 2
fi

IMAGE_PREFIX="${IMAGE_PREFIX%/}"
if [[ "$IMAGE_PREFIX" != */* ]]; then
  IMAGE_PREFIX="${IMAGE_PREFIX}/secure-login"
fi

if [[ -z "$OUTPUT_MODE" ]]; then
  push_answer="$(prompt "Push multi-platform image to registry? yes/no" "no")"
  case "${push_answer,,}" in
    y|yes|i|igen) OUTPUT_MODE="push" ;;
    *)
      OUTPUT_MODE="load"
      PLATFORMS="$(current_platform)"
      ;;
  esac
fi

if [[ "$OUTPUT_MODE" == "load" && "$PLATFORMS" == *,* ]]; then
  echo "--load supports a single platform only. Use --platforms linux/amd64 or --platforms linux/arm64." >&2
  exit 2
fi

TAG="${TAG:-latest}"
IMAGE_NAME="${IMAGE_PREFIX}-auth:${TAG}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Build plan:"
  echo "  output: $OUTPUT_MODE"
  echo "  platforms: $PLATFORMS"
  echo "  auth image: $IMAGE_NAME"
  exit 0
fi

if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDER" --use >/dev/null
else
  docker buildx use "$BUILDER" >/dev/null
fi

docker buildx inspect --bootstrap >/dev/null

OUTPUT_FLAG="--push"
if [[ "$OUTPUT_MODE" == "load" ]]; then
  OUTPUT_FLAG="--load"
fi

docker buildx build \
  --platform "$PLATFORMS" \
  --file Dockerfile \
  --tag "$IMAGE_NAME" \
  "$OUTPUT_FLAG" \
  .

echo "Built $IMAGE_NAME"
