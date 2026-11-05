#!/usr/bin/env bash
# Build a reviewed Secure Login release tag as a isolated Secure Login linux/arm64 candidate Docker image.
# Local default is BUILD ONLY. Publishing requires an explicit --push.
# Never modifies Docker containers, Compose stacks, Caddy, IAM, or live data.
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: bash scripts/build_upload.sh (--version vX.Y.Z[-rc.N] | --from-main) [--namespace sseres] [--push]
Examples:
  bash scripts/build_upload.sh --version v0.1.0-rc.1
  bash scripts/build_upload.sh --version v0.1.0-rc.1 --namespace sseres --push
  bash scripts/build_upload.sh --from-main --namespace sseres --push

Default: build only (no registry push). --push requires prior docker login,
a reviewed version tag or exact current remote main commit, and
publishes <namespace>/secure-login:<version>-arm64. Only Secure Login is built.
EOF
}
VERSION=""
FROM_MAIN=false
NAMESPACE="${IMAGE_NAMESPACE:-sseres}"
PUSH=false
while (($#)); do
  case "$1" in
    --version) (($# >= 2)) || { usage >&2; exit 2; }; VERSION="$2"; shift 2 ;;
    --namespace) (($# >= 2)) || { usage >&2; exit 2; }; NAMESPACE="$2"; shift 2 ;;
    --from-main) FROM_MAIN=true; shift ;;
    --push) PUSH=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ "$FROM_MAIN" == true ]]; then
  [[ -z "$VERSION" ]] || { echo "ERROR: choose --version or --from-main, not both" >&2; exit 2; }
else
  [[ "$VERSION" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+(-[a-z0-9.-]+)?$ ]] || {
    echo "ERROR: supply --version vX.Y.Z[-rc.N] or --from-main" >&2; exit 2;
  }
fi
[[ "$NAMESPACE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*$ ]] || {
  echo "ERROR: namespace must be a Docker Hub account or organization" >&2; exit 2;
}
for cmd in git docker tar python3 mktemp; do
  command -v "$cmd" >/dev/null || { echo "ERROR: $cmd is required" >&2; exit 1; }
done
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"
git rev-parse --is-inside-work-tree >/dev/null
if [[ "$FROM_MAIN" == true ]]; then
  # Candidate-only: bind the image to the current, reviewed remote main SHA.
  # No mutable local working tree or unpushed feature branch is built.
  remote_commit="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
  [[ "$remote_commit" =~ ^[0-9a-f]{40}$ ]] || {
    echo "ERROR: cannot verify remote main commit" >&2; exit 1;
  }
  git fetch --no-tags origin main >/dev/null
  commit="$(git rev-parse refs/remotes/origin/main^{commit})"
  [[ "$commit" == "$remote_commit" ]] || {
    echo "ERROR: origin/main changed while being fetched; retry after review" >&2; exit 1;
  }
  VERSION="sha-$commit"
else
  tag="refs/tags/$VERSION"
  git show-ref --verify --quiet "$tag" || {
    echo "ERROR: local release tag $VERSION not found. Fetch tags first." >&2; exit 1;
  }
  commit="$(git rev-parse "$tag^{commit}")"
  # Ensure tag exists on origin, points to the same commit, and its commit is
  # already part of reviewed main. Never build arbitrary working-tree contents.
  remote_commit="$(git ls-remote origin "$tag^{}" | awk '{print $1}')"
  if [[ -z "$remote_commit" ]]; then
    remote_commit="$(git ls-remote origin "$tag" | awk '{print $1}')"
    # Annotated refs point at a tag object, not a commit: dereference locally
    # only if the remote tag object's SHA matches our local tag object.
    [[ "$remote_commit" == "$(git rev-parse "$tag")" ]] || {
      echo "ERROR: remote tag is missing or differs from local tag" >&2; exit 1;
    }
  else
    [[ "$remote_commit" == "$commit" ]] || {
      echo "ERROR: remote release tag points to another commit" >&2; exit 1;
    }
  fi
fi
git fetch --no-tags origin main >/dev/null
git merge-base --is-ancestor "$commit" refs/remotes/origin/main || {
  echo "ERROR: release tag commit is not merged into origin/main" >&2; exit 1;
}
if [[ "$FROM_MAIN" == true ]]; then
  image="$NAMESPACE/secure-login:$VERSION"
else
  image="$NAMESPACE/secure-login:$VERSION-arm64"
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/context"
git archive "$commit" | tar -x -C "$tmp/context"
[[ -f "$tmp/context/api/Dockerfile" ]] || {
  echo "ERROR: tagged commit has no Secure Login API Dockerfile" >&2; exit 1;
}
echo "Source commit: $commit"
echo "Image: $image"
echo "Platform: linux/arm64"
if [[ "$PUSH" == true ]]; then
  # Inspect the exact tagged source, never the (possibly modified) checkout.
  # Current public Secure Login revision is not equivalent to the production
  # gateway/IAM contract. Publishing is blocked until the contract is present.
  python3 "$tmp/context/scripts/check_rem_contract.py" --source-root "$tmp/context"
  echo "Publishing requested explicitly. Docker Hub credentials must already be configured."
  docker buildx build \
    --platform linux/arm64 \
    --file "$tmp/context/api/Dockerfile" \
    --tag "$image" \
    --label "org.opencontainers.image.revision=$commit" \
    --label "org.opencontainers.image.version=$VERSION" \
    --metadata-file "$tmp/build-metadata.json" \
    --provenance=mode=max --sbom=true \
    --push "$tmp/context/api"
  python3 - "$tmp/build-metadata.json" "$NAMESPACE" "$VERSION" "$commit" <<'PY'
import json, pathlib, re, sys
metadata = json.loads(pathlib.Path(sys.argv[1]).read_text())
digest = metadata.get("containerimage.digest")
if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise SystemExit("ERROR: Docker Buildx did not report a valid registry digest")
ref = sys.argv[2] + "/secure-login@" + digest
print("PUBLISHED_REFERENCE=" + ref)
print("Inspect with: docker buildx imagetools inspect " + ref)
PY
else
  docker buildx build \
    --platform linux/arm64 \
    --file "$tmp/context/api/Dockerfile" \
    --tag "$image" \
    --load "$tmp/context/api"
  echo "BUILD ONLY: $image (not uploaded to Docker Hub)"
fi
