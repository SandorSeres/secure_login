#!/usr/bin/env bash
#
# start-new-research.sh — uj kutatás indítása cél-markdown fájlból a DGX Sparkon.
#
# Egy új, önálló Research Projectet (Git repository a /Data/research alatt) és
# hozzá tartozó Docker Compose példányt hoz létre, majd — hacsak nem --no-job —
# az első evidencia-gyűjtő kutatási kört sorba állítja.
#
#   ./start-new-research.sh --goal cel.md --id pelda-kutatas --port 8203
#   ./start-new-research.sh --goal cel.md                       # id + port auto
#   ./start-new-research.sh --id pelda-kutatas --restart-only
#   ./start-new-research.sh --id pelda-kutatas --start-only
#   ./start-new-research.sh --id pelda-kutatas --status
#
# A cél.md formátumát a --help írja le.

set -Eeuo pipefail

SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"

# --------------------------------------------------------------------------
# Helyi alapértelmezések (mind felülírható env vagy kapcsoló alapján)
# --------------------------------------------------------------------------
RESEARCH_ROOT="${RESEARCH_ROOT:-/Data/research}"
INSTALLER_ROOT="${INSTALLER_ROOT:-/Data/containers/containers/evidence-research-harness}"
FRAMEWORK_SOURCE="${RH_FRAMEWORK_SOURCE:-/Data/containers/.evidence-research-harness/framework-src}"
GOAL_PARSER="${RH_GOAL_PARSER:-/Data/containers/lib/research_goal_parse.py}"
IMAGE_PREFIX="${IMAGE_PREFIX:-sseres/evidence-research-harness}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
CODEX_VERSION="${RH_CODEX_VERSION:-0.158.0}"
RH_REBUILD="${RH_REBUILD:-auto}"
LLM_URL="${RH_CODEX_PROVIDER_BASE_URL:-http://host.docker.internal:8013/v1}"
LLM_MODEL="${RH_CODEX_MODEL:-shared-local}"
REVIEWER="${RH_APPROVED_BY:-Sandor Seress}"
JOB_RUNNER="${RH_JOB_RUNNER:-codex-cli}"
GATEWAY_NETWORK="${RH_GATEWAY_NETWORK:-evidence-research_gateway}"
RH_DOMAIN_SKILLS_DIR="${RH_DOMAIN_SKILLS_DIR:-$RESEARCH_ROOT/.evidence-research-harness/domain-skills}"
RH_DOMAIN_SKILLS_ALLOWLIST="${RH_DOMAIN_SKILLS_ALLOWLIST:-none}"
# Optional explicit source for the shared web-search provider key. When unset,
# the launcher discovers a non-empty key from an already installed instance.
RH_SERPAPI_KEY_SOURCE="${RH_SERPAPI_KEY_SOURCE:-}"
RH_EXA_KEY_SOURCE="${RH_EXA_KEY_SOURCE:-}"

RUNTIME_IMAGE="$IMAGE_PREFIX-runtime:$IMAGE_TAG"
WORKER_IMAGE="$IMAGE_PREFIX-worker:$IMAGE_TAG"

GOAL_FILE=""
RESEARCH_ID="${RESEARCH_ID:-}"
UI_PORT_INPUT="${RH_UI_PORT:-}"
JOB_TITLE="${RH_JOB_TITLE:-}"
NO_JOB=0
NO_START=0
DRY_RUN=0
RESTART_ONLY=0
START_ONLY=0
SHOW_STATUS=0
SECURE_LOGIN=0
ACTIVATE_GATEWAY=0
BIND_ONLY=0

usage() {
  cat <<'EOF'
start-new-research.sh — uj kutatas inditasa cel-markdown fajlbol

Kotelezo:
  --goal FILE.md          A kutatas celjat leiro markdown fajl (formatum: lent)

Vlaszthato:
  --id AZONOSITO          Projektazonosito, [a-z0-9][a-z0-9_-]*. Ha hianyzik es
                          a cel.md cimebol kepzett azono foglalt, szamozott
                          utotagot kap (-2, -3, ...).
  --port SZAM | auto      Workspace host port. auto vagy hianyzo caseben az elso
                          szabad port a 8201-8299, majd 10100-10199 savokbol.
                          Mar telepített pldanynal a peldany .env-jen rogzitett
                          port van hasznalatban.
  --job-title CIM         Az elso kutatasi kor cime (alapertelmezett: projekt cime)
  --no-job                Csak projekt es Workspace: kutatasi kor nelkul
  --no-start              Telepites, de a kontenereket nem inditja el
  --dry-run               Csak terv: semmit nem ir, nem general, nem indit
  --restart-only          Meglevo pldany ujrainditasa (--id kotelezo)
  --start-only            Meglevo pldany elinditasa (--id kotelezo)
  --status                Meglevo pldany compose allapota (--id kotelezo)
  --secure-login          Bekotes a Secure Login alkalmazkapuiba (lasd: lent)
  --activate              --secure-login mellé a Caddy újralétrehozása is
  --bind-only             Csak Secure Login bekötés (--id kotelezo; cel.md nelkul)

Secure Login bekotes (--secure-login | --activate | --bind-only):
  Az uj kutatas a https://research.code.hu/apps/<azonosito>/ cimen jelenik meg
  a Secure Login alkalmazkapujaban. A script:
    1. felveszi az alkalmazast es a celadatot az application_targets tablaba
       (a Workspace kontener Docker-neve es a gateway halo),
    2. elallitja a generalt Caddy site-ot es a compose overlay-t,
    3. az --activate csak a Caddy-t hozza ujra letre (api, db, redis erintese nelkul).
  A hozzarendelest (felhasznalo es alkalmazas kozott) az admin.sh 5. pontja vegzi:
    cd /Data/containers/secure-login && ./admin.sh   ->   5)
  Meglévő pldány utólag beköthető cél.md nélkül:
    ./start-new-research.sh --id <azonosito> --bind-only            (újraindítás nélkül)
    ./start-new-research.sh --id <azonosito> --bind-only --activate (Caddy ujrainditas)

  Jog nelkul a https://<gateway>/apps/<azonosito>/ lapon 401 a helyes valasz.
  Ha a Workspace nincs rajta a gateway halon, a Caddy nem eri a kontener nevet:
  a pldany .env RH_GATEWAY_NETWORK erteke legyen a gateway halo neve.

  Kornyezet: SECURE_LOGIN_DIR (alap: /Data/containers/secure-login)
             RH_GATEWAY_HOST (alap: research.code.hu)

Pelda:
  ./start-new-research.sh --goal ~/kutatas/cel.md --port 8207
  ./start-new-research.sh --goal ~/kutatas/cel.md --no-job
  ./start-new-research.sh --id ai-native-it-transformation --restart-only
EOF
}

log() { printf '>> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

# --------------------------------------------------------------------------
# 1. Kapcsolok
# --------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --goal) GOAL_FILE="${2:?Hiányzó érték: --goal}"; shift 2 ;;
    --id) RESEARCH_ID="${2:?Hiányzó érték: --id}"; shift 2 ;;
    --port) UI_PORT_INPUT="${2:?Hiányzó érték: --port}"; shift 2 ;;
    --job-title) JOB_TITLE="${2:?Hiányzó érték: --job-title}"; shift 2 ;;
    --no-job) NO_JOB=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --restart-only) RESTART_ONLY=1; shift ;;
    --start-only) START_ONLY=1; shift ;;
    --status) SHOW_STATUS=1; shift ;;
    --secure-login) SECURE_LOGIN=1; shift ;;
    --activate) SECURE_LOGIN=1; ACTIVATE_GATEWAY=1; shift ;;
    --bind-only) SECURE_LOGIN=1; BIND_ONLY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; fail "Ismeretlen kapcsoló: $1" ;;
  esac
done

# --------------------------------------------------------------------------
# 2. Seggdfggvények
# --------------------------------------------------------------------------
is_valid_id() { [[ "$1" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]]; }

derive_id_from_goal() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/á/a/g; s/é/e/g; s/í/i/g; s/ö/o/g; s/ő/o/g; s/ú/u/g; s/ü/u/g; s/ű/u/g' \
    | tr -c 'a-z0-9' '-' \
    | sed -e 's/-\{2,\}/-/g' -e 's/^-*//' \
    | cut -c1-46 \
    | sed -e 's/-*$//'
}

# "Cel: szoveg" vagy "Cel - szoveg" -> "szoveg"; egyebkent errol el
strip_list_label() {
  local s="$1" key rest
  if [[ "$s" == *:* ]]; then
    key="${s%%:*}"
    rest="$(strip_front_back "${s#*:}")"
    if [[ -n "$rest" && ${#key} -le 40 && "$key" != *' '* ]]; then
      printf '%s' "$rest"
      return
    fi
  fi
  printf '%s' "$s"
}

strip_front_back() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# A shared web-search provider key discovery. Never prints key material.
# Preference order: explicit env source, then any existing non-empty instance
# secret, then the ai-dashboard gateway secret when it is readable. An empty
# file remains a valid state: Scholar then reports not_configured and PubMed
# stays anonymous.
discover_provider_key() {
  local name="$1" explicit candidate
  explicit="$(printf '%s' "${!2:-}")"
  if [[ -n "$explicit" ]]; then
    [[ -s "$explicit" ]] && { printf '%s' "$explicit"; return 0; }
    return 1
  fi
  for candidate in "$FRAMEWORK_SOURCE"/secrets/web-search/"$name" \
                   /Data/containers/ai-dashboard-app/secrets/web-search/"$name" \
                   "$RESEARCH_ROOT/.evidence-research-harness/instances/"*/secrets/"$name"; do
    [[ -f "$candidate" && -s "$candidate" ]] || continue
    head -c 1 "$candidate" >/dev/null 2>&1 || continue
    printf '%s' "$candidate"
    return 0
  done
  return 1
}

port_is_listening() {
  local port="$1"
  command -v ss >/dev/null 2>&1 || { (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && return 0; return 1; }
  ss -ltn 2>/dev/null | awk -v needle=":$port" '$4 ~ needle"$" { found = 1 } END { exit !found }'
}

port_is_available() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] || return 1
  (( port >= 1024 && port <= 65535 )) || return 1
  ! port_is_listening "$port"
}

# A adott porton figyelo kontener hozzank-e tartozik (compose projekt nevenk)
this_instance_is_listening_on() {
  local port="$1" names
  names="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
    | awk -F'\t' -v needle=":$port->" 'index($2, needle) { print $1 }')"
  [[ -n "$names" ]] || return 1
  printf '%s\n' "$names" | grep -Eq "^${RESEARCH_ID}-"
}

find_used_instance_ports() {
  awk '
    BEGIN { OFS = "\n" }
    index($0, "RH_UI_PORT=") == 1 {
      port = substr($0, length("RH_UI_PORT=") + 1)
      if (port ~ /^[0-9]+$/) print port
    }
  ' "$RESEARCH_ROOT/.evidence-research-harness/instances/"*/.env 2>/dev/null | sort -n -u
}

pick_free_port() {
  local candidate
  while read -r candidate; do
    if port_is_available "$candidate"; then
      UI_PORT="$candidate"
      return 0
    fi
  done < <(find_used_instance_ports)
  for candidate in $(seq 8201 8299) $(seq 10100 10199); do
    if port_is_available "$candidate"; then
      UI_PORT="$candidate"
      return 0
    fi
  done
  return 1
}

# Harness parancs futtatasa a projekt konyvtaraban, kepatlan hitelesitessel
rh_in() {
  local project_dir="$1"
  shift
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env "RH_HARNESS_IMAGE=$RUNTIME_IMAGE" \
    --read-only \
    --tmpfs /tmp:size=64m,mode=1777 \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --volume "$project_dir:/workspace" \
    --workdir /workspace \
    "$RUNTIME_IMAGE" "$@"
}

compose_dir() {
  local dir="$1"
  shift
  docker compose --env-file "$dir/.env" -f "$dir/docker-compose.yml" -p "$RESEARCH_ID" "$@"
}

set_env_key() {
  local dir="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    index($0, key "=") == 1 { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$dir/.env" >"$tmp"
  cat "$tmp" >"$dir/.env"
  chmod 600 "$dir/.env"
  rm -f "$tmp"
}

bind_secure_login_gateway() {
  # psql hívás a Secure Login adatbázisába: a credentials a kontében van,
  # nem a .env-ből; a felhasználói értéket mindig -v kötött változó viszi.
  SL_SQL() {
    ( cd "$SECURE_LOGIN_DIR" && docker compose "${COMPOSE_FILES[@]}" exec -T db sh -c \
        'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off "$@"' psql "$@" )
  }

  SECURE_LOGIN_DIR="${SECURE_LOGIN_DIR:-/Data/containers/secure-login}"
  GATEWAY_HOST="${RH_GATEWAY_HOST:-research.code.hu}"
  COMPOSE_FILES=(--file "$SECURE_LOGIN_DIR/docker-compose.yaml")
  [[ -f "$SECURE_LOGIN_DIR/compose.app-gateway.yaml" ]] &&
    COMPOSE_FILES+=(--file "$SECURE_LOGIN_DIR/compose.app-gateway.yaml")

  [[ -d "$SECURE_LOGIN_DIR" ]] || fail "Secure Login: a könyvtár nem található: $SECURE_LOGIN_DIR"
  APP_SLUG="$RESEARCH_ID"
  while [[ "$APP_SLUG" == *_* ]]; do APP_SLUG="${APP_SLUG/_/-}"; done
  [[ "$APP_SLUG" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] ||
    fail "A Secure Login alkalmazásazonosító csak kis betű, szám és kötőjel lehet: $APP_SLUG"

  # Az applications.name mező varchar(100): a címet utótagjával együtt vágjuk.
  APP_DISPLAY_NAME="$PROJECT_TITLE"
  (( ${#APP_DISPLAY_NAME} <= 100 )) || APP_DISPLAY_NAME="${APP_DISPLAY_NAME:0:96}…"

  CONTAINER_NAME="$(docker ps \
    --filter "label=com.docker.compose.project=$RESEARCH_ID" \
    --filter "label=com.docker.compose.service=workspace" \
    --format '{{.Names}}' | head -1)"
  [[ -n "$CONTAINER_NAME" ]] || fail "Nem fut a Workspace konténer a(z) $RESEARCH_ID példányhoz."

  SL_DB_RUNNING="$(cd "$SECURE_LOGIN_DIR" && docker compose "${COMPOSE_FILES[@]}" ps --status running --services 2>/dev/null | grep -cx db || true)"
  (( SL_DB_RUNNING == 1 )) || fail "A Secure Login 'db' szolgáltatása nem fut: $SECURE_LOGIN_DIR"

  SL_SQL <<'SQL' >/dev/null || fail "Az application_targets séma ellenőrzése sikertelen."
CREATE TABLE IF NOT EXISTS application_targets (
    application_id varchar(63) PRIMARY KEY REFERENCES applications(id) ON DELETE CASCADE,
    public_url varchar(253) NOT NULL UNIQUE,
    upstream_origin varchar(255) NOT NULL,
    docker_network varchar(128)
);
SQL

  log "Alkalmazás és elérési cél rögzítése: $APP_SLUG"
  SL_SQL -v "v_app=$APP_SLUG" -v "v_name=$APP_DISPLAY_NAME" \
    -v "v_public=https://$GATEWAY_HOST/apps/$APP_SLUG" \
    -v "v_internal=http://$CONTAINER_NAME:9999" \
    -v "v_net=$GATEWAY_NETWORK" <<'SQL' || fail "Az alkalmazás-cél mentése sikertelen — a futó konfiguráció nem módosult."
BEGIN;
INSERT INTO applications (id, name)
VALUES (:'v_app', :'v_name')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;
INSERT INTO application_targets (application_id, public_url, upstream_origin, docker_network)
VALUES (:'v_app', :'v_public', :'v_internal', NULLIF(:'v_net',''))
ON CONFLICT (application_id) DO UPDATE
SET public_url = EXCLUDED.public_url,
    upstream_origin = EXCLUDED.upstream_origin,
    docker_network = EXCLUDED.docker_network;
COMMIT;
SELECT application_id, public_url, upstream_origin, COALESCE(docker_network, '')
FROM application_targets WHERE application_id = :'v_app';
SQL

  log "Gateway konfiguráció előállítása (Caddy site + Compose overlay)."
  SL_ROWS="$(SL_SQL -qAt -F '|' <<'SQL'
SELECT application_id, public_url, upstream_origin, COALESCE(docker_network, '')
FROM application_targets ORDER BY application_id;
SQL
)" || fail "Az alkalmazás-célok lekérdezése sikertelen — a futó konfiguráció nem módosult."
  printf '%s\n' "$SL_ROWS" | ( cd "$SECURE_LOGIN_DIR" && python3 scripts/render_app_gateway.py ) ||
    fail "A gateway konfiguráció generálása sikertelen."

  if (( ! ACTIVATE_GATEWAY )); then
    log "A generált konfiguráció elkészült, de nincs aktiválva. Aktiválás: --activate"
    return 0
  fi

  log "Caddy újralétrehozása a generált overlay-jel (api, db és redis érintése nélkül)."
  ( cd "$SECURE_LOGIN_DIR" && docker compose "${COMPOSE_FILES[@]}" up -d --no-deps --force-recreate caddy )
  for _ in $(seq 1 40); do
    if timeout 5 curl -sk --resolve "$GATEWAY_HOST:443:127.0.0.1" \
        "https://$GATEWAY_HOST/apps/$APP_SLUG/" 2>/dev/null | grep -q .; then
      break
    fi
    sleep 2
  done

  printf '\n'
  printf 'Secure Login alkalmazás: %s\n' "$APP_SLUG"
  printf 'Elérés:                 https://%s/apps/%s/\n' "$GATEWAY_HOST" "$APP_SLUG"
  printf 'Indítólink:             https://ai.code.hu/applications/%s/launch\n' "$APP_SLUG"
  cat <<EOF

Hozzáférés hozzárendelése (admin.sh, a Secure Login adatbázisában):
  cd $SECURE_LOGIN_DIR && ./admin.sh   →   5) Alkalmazás hozzárendelése
     felhasználónév, majd: $APP_SLUG
Ellenőrzés:
  cd $SECURE_LOGIN_DIR && ./admin.sh   →   3) Alkalmazások listázása
  curl -sk --resolve $GATEWAY_HOST:443:127.0.0.1 https://$GATEWAY_HOST/apps/$APP_SLUG/  (401 = bekötve, jog nélkül)
EOF
  return 0
}

# --------------------------------------------------------------------------
# 3. Konnyu muveletek: status / start / restart
# --------------------------------------------------------------------------
if (( RESTART_ONLY || START_ONLY || SHOW_STATUS || BIND_ONLY )); then
  [[ -n "$RESEARCH_ID" ]] || fail "--id kötelező ehhez a művelethez."
  is_valid_id "$RESEARCH_ID" || fail "--id formátum: [a-z0-9][a-z0-9_-]{0,62}"
  INSTALL_DIR="$RESEARCH_ROOT/.evidence-research-harness/instances/$RESEARCH_ID"
  [[ -f "$INSTALL_DIR/docker-compose.yml" && -f "$INSTALL_DIR/.env" ]] || \
    fail "Nincs telepített példány: $INSTALL_DIR"
  if (( BIND_ONLY )); then
    PROJECT_DIR="$(awk -F= '/^RH_PROJECT_DIR=/{print $2; exit}' "$INSTALL_DIR/.env")"
    [[ -f "$PROJECT_DIR/research.yaml" ]] || fail "Nincs Research Project itt: $PROJECT_DIR"
    PROJECT_TITLE="$(awk '/^title:/{sub(/^title: */,""); print; exit}' "$PROJECT_DIR/research.yaml")"
    [[ -n "$PROJECT_TITLE" ]] || PROJECT_TITLE="$RESEARCH_ID"
    UI_PORT="$(awk -F= '/^RH_UI_PORT=/{print $2; exit}' "$INSTALL_DIR/.env")"
    [[ -n "$UI_PORT" ]] || UI_PORT="ismeretlen"
    GATEWAY_READY=1
    if [[ ! -f "$INSTALLER_ROOT/install.sh" ]]; then
      GATEWAY_READY=0
    else
      for required in docker git awk python3; do
        command -v "$required" >/dev/null 2>&1 || GATEWAY_READY=0
      done
    fi
    (( GATEWAY_READY )) || fail "Hiányzó parancs vagy installer: docker, git, awk, python3 + $INSTALLER_ROOT/install.sh"
    docker info >/dev/null 2>&1 || fail "A Docker daemon nem érhető el."
    docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin szükséges."
    log "Secure Login bekötés meglévő pldányhoz: $RESEARCH_ID"
    bind_secure_login_gateway
    exit 0
  fi
  command -v docker >/dev/null 2>&1 || fail "Hiányzó parancs: docker"
  if (( SHOW_STATUS )); then
    compose_dir "$INSTALL_DIR" ps --all
    exit 0
  fi
  if (( RESTART_ONLY )); then
    compose_dir "$INSTALL_DIR" up -d --pull never --force-recreate
    exit 0
  fi
  compose_dir "$INSTALL_DIR" up -d --pull never
  exit 0
fi

# --------------------------------------------------------------------------
# 4. Elofeltetelek
# --------------------------------------------------------------------------
[[ -n "$GOAL_FILE" ]] || { usage >&2; fail "--goal FILE.md kötelező. Meglévő példányhoz: --restart-only / --start-only / --status"; }
[[ -f "$GOAL_FILE" ]] || fail "A cél markdown fájl nem található: $GOAL_FILE"
[[ -z "$RESEARCH_ID" ]] || is_valid_id "$RESEARCH_ID" || fail "--id formátum: [a-z0-9][a-z0-9_-]{0,62}"

for required in docker git awk python3; do
  command -v "$required" >/dev/null 2>&1 || fail "Hiányzó parancs: $required"
done
docker info >/dev/null 2>&1 || fail "A Docker daemon nem érhető el."
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin szükséges."
[[ -x "$INSTALLER_ROOT/install.sh" ]] || fail "Nincs telepítő forrás: $INSTALLER_ROOT/install.sh"
[[ -f "$FRAMEWORK_SOURCE/deploy/docker-compose.yml" ]] || fail "Hiányzik: $FRAMEWORK_SOURCE/deploy/docker-compose.yml"
[[ -f "$GOAL_PARSER" ]] || fail "Hiányzik a cél-parser: $GOAL_PARSER"

# --------------------------------------------------------------------------
# 5. Image-ek biztosítása
# --------------------------------------------------------------------------
ensure_images() {
  local runtime_exists=0 worker_exists=0 worker_has_web_mcp=0
  docker image inspect "$RUNTIME_IMAGE" >/dev/null 2>&1 && runtime_exists=1
  docker image inspect "$WORKER_IMAGE" >/dev/null 2>&1 && worker_exists=1
  if (( worker_exists )); then
    if docker run --rm --entrypoint sh "$WORKER_IMAGE" -c '
      test -f /opt/research-harness-skills/web-search/scripts/web_search_mcp.py &&
      test -f /opt/research-harness-skills/web-search/scripts/exa_gateway.py &&
      grep -q "mcp_servers" /opt/codex-home/config.toml
    ' >/dev/null 2>&1; then
      worker_has_web_mcp=1
    fi
  fi

  case "$RH_REBUILD" in
    auto) ;;
    0 | false | no) RH_REBUILD=0 ;;
    1 | true | yes) RH_REBUILD=1 ;;
    *) fail "RH_REBUILD értéke auto, 0 vagy 1 lehet." ;;
  esac

  if [[ "$RH_REBUILD" == "auto" ]]; then
    if (( runtime_exists && worker_exists && worker_has_web_mcp )); then
      log "Megévő image-ek: $RUNTIME_IMAGE"
      return
    fi
    (( runtime_exists && worker_exists )) && \
      log "A worker image-ből hiányzik a web-search MCP runtime → újraépítem."
    RH_REBUILD=1
  elif [[ "$RH_REBUILD" == "0" ]]; then
    (( runtime_exists )) || fail "Nincs runtime image: $RUNTIME_IMAGE"
    (( worker_exists )) || fail "Nincs worker image: $WORKER_IMAGE"
    log "Megévő image-ek: $RUNTIME_IMAGE"
    return
  fi

  [[ -f "$FRAMEWORK_SOURCE/Dockerfile" ]] || fail "Hiányzik: $FRAMEWORK_SOURCE/Dockerfile"
  [[ -f "$FRAMEWORK_SOURCE/Dockerfile.worker" ]] || fail "Hiányzik: $FRAMEWORK_SOURCE/Dockerfile.worker"
  log "Image-ek építése forrásból: $FRAMEWORK_SOURCE"
  docker build --tag "$RUNTIME_IMAGE" "$FRAMEWORK_SOURCE"
  docker build --file "$FRAMEWORK_SOURCE/Dockerfile.worker" \
    --build-arg "CODEX_VERSION=$CODEX_VERSION" --tag "$WORKER_IMAGE" "$FRAMEWORK_SOURCE"
}
ensure_images

# --------------------------------------------------------------------------
# 6. Cel.md feldolgozasa
# --------------------------------------------------------------------------
GOAL_JSON="$(python3 "$GOAL_PARSER" "$GOAL_FILE")"
[[ -n "$GOAL_JSON" ]] || fail "A cél markdown feldolgozása nem sikerült."

json_get() {
  python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); v=d.get(sys.argv[1]); print(v if isinstance(v,str) else json.dumps(v, ensure_ascii=False))' \
    "$1" <<<"$GOAL_JSON"
}
json_list() {
  python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); [print(x) for x in d.get(sys.argv[1], [])]' \
    "$1" <<<"$GOAL_JSON"
}

PROJECT_TITLE="$(json_get title)"
RESEARCH_QUESTION="$(json_get question)"
mapfile -t OBJECTIVES < <(json_list objectives)
mapfile -t SCOPE_IN < <(json_list scope_in)
mapfile -t SCOPE_OUT < <(json_list scope_out)
mapfile -t CRITERIA < <(json_list criteria)
mapfile -t FREEFORM < <(json_list freeform)

[[ -n "$PROJECT_TITLE" ]] || fail "A cél.md fájlból nem sikerült címet kinyerni (legalább egy # cím szükséges)."
[[ -n "$RESEARCH_QUESTION" ]] || RESEARCH_QUESTION="$PROJECT_TITLE"
[[ -n "$JOB_TITLE" ]] || JOB_TITLE="$PROJECT_TITLE"

# --------------------------------------------------------------------------
# 7. Projektazonosito es konyvtarak
# --------------------------------------------------------------------------
ID_WAS_AUTO=0
if [[ -z "$RESEARCH_ID" ]]; then
  RESEARCH_ID="$(derive_id_from_goal "$PROJECT_TITLE")"
  is_valid_id "$RESEARCH_ID" || RESEARCH_ID="research-$(date +%Y%m%d-%H%M%S)"
  ID_WAS_AUTO=1
fi

PROJECT_DIR="$RESEARCH_ROOT/$RESEARCH_ID"
INSTALL_DIR="$RESEARCH_ROOT/.evidence-research-harness/instances/$RESEARCH_ID"
REUSE_EXISTING=0
if [[ -f "$PROJECT_DIR/.rh/project.yaml" ]]; then
  REUSE_EXISTING=1
elif (( ID_WAS_AUTO )) && [[ -n "$(find "$PROJECT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
  base="$RESEARCH_ID"
  n=2
  while [[ -d "$RESEARCH_ROOT/$RESEARCH_ID" ]] && \
    { [[ -f "$RESEARCH_ROOT/$RESEARCH_ID/.rh/project.yaml" ]] ||
      [[ -n "$(find "$RESEARCH_ROOT/$RESEARCH_ID" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; }; do
    RESEARCH_ID="${base}-${n}"
    n=$((n + 1))
  done
  log "A(z) $base azonosító foglalt → új azonosító: $RESEARCH_ID"
  PROJECT_DIR="$RESEARCH_ROOT/$RESEARCH_ID"
  INSTALL_DIR="$RESEARCH_ROOT/.evidence-research-harness/instances/$RESEARCH_ID"
fi

if [[ -e "$PROJECT_DIR" && ! -d "$PROJECT_DIR" ]]; then
  fail "Az projektútvonal létezik, de nem könyvtár: $PROJECT_DIR"
elif [[ -d "$PROJECT_DIR" && ! -f "$PROJECT_DIR/.rh/project.yaml" ]] && \
     [[ -n "$(find "$PROJECT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  fail "A könyvtár nem üres és nem Research Project: $PROJECT_DIR"
fi

# --------------------------------------------------------------------------
# 8. Workspace port
# --------------------------------------------------------------------------
UI_PORT=""
if [[ -n "$UI_PORT_INPUT" && "$UI_PORT_INPUT" != "auto" ]]; then
  [[ "$UI_PORT_INPUT" =~ ^[0-9]+$ ]] || fail "--port értéke szám vagy auto lehet: $UI_PORT_INPUT"
  UI_PORT="$UI_PORT_INPUT"
elif [[ $REUSE_EXISTING -eq 1 && -f "$INSTALL_DIR/.env" ]] && \
     grep -Eq '^RH_UI_PORT=[0-9]+$' "$INSTALL_DIR/.env"; then
  INSTALLED_PORT="$(awk -F= '/^RH_UI_PORT=/{print $2; exit}' "$INSTALL_DIR/.env")"
  if port_is_available "$INSTALLED_PORT" || this_instance_is_listening_on "$INSTALLED_PORT"; then
    UI_PORT="$INSTALLED_PORT"
    log "A példány .env fájljában rögzített portot használom: $UI_PORT"
  else
    log "A példány $INSTALLED_PORT portját most más szolgáltatás foglalja → szabad portot keresek."
  fi
fi

if [[ -z "$UI_PORT" ]]; then
  if (( DRY_RUN )); then
    UI_PORT="auto"
  else
    pick_free_port || fail "Nincs szabad Workspace port. Add meg a --port kapcsolóval."
  fi
fi

if [[ "$UI_PORT" != "auto" ]] && ! port_is_available "$UI_PORT" && ! this_instance_is_listening_on "$UI_PORT"; then
  fail "A $UI_PORT portot más szolgáltatás foglalja el. Adj meg másik --port értéket, vagy indítsd újra ezt a példányt: $SCRIPT_PATH --id $RESEARCH_ID --restart-only"
fi

log "Projekt azonosító : $RESEARCH_ID"
log "Projekt címe      : $PROJECT_TITLE"
log "Fő kutatási kérdés: $(printf '%.140s' "$RESEARCH_QUESTION")"
log "Célok / scope-in / scope-out / kritériumok: ${#OBJECTIVES[@]} / ${#SCOPE_IN[@]} / ${#SCOPE_OUT[@]} / ${#CRITERIA[@]}"
log "Workspace port    : $UI_PORT"
log "Projekt könyvtár  : $PROJECT_DIR"
log "Példány könyvtár  : $INSTALL_DIR"

if (( DRY_RUN )); then
  cat <<EOF

DRY-RUN — a következők történnének:
  1. project init a $RUNTIME_IMAGE image-ből: --id $RESEARCH_ID --title ...
     (--objective x${#OBJECTIVES[@]}, --scope-in x${#SCOPE_IN[@]}, --scope-out x${#SCOPE_OUT[@]}, --decision-criterion x${#CRITERIA[@]})
  2. launcher image-nevek igazítása, ha kellenek
  3. $INSTALLER_ROOT/install.sh --offline --instance $RESEARCH_ID \\
       --install-dir $INSTALL_DIR --project-dir $PROJECT_DIR --ui-port <szabad port> --no-start
  4. compose overlay: danger-full-access + web-search gateway + secret fájlok
     (a SerpApi kulcsot meglévő telepítésből veszi át, csöndben)
  5. docker compose -p $RESEARCH_ID up -d
  6. rh job create --source-mode web + QUEUED$([[ $NO_JOB -eq 1 ]] && printf ' (kihagyva: --no-job)')
Kezelés: $INSTALL_DIR/manage.sh status
EOF
  exit 0
fi

# --------------------------------------------------------------------------
# 9. Research Project inicializalasa
# --------------------------------------------------------------------------
mkdir -p "$RESEARCH_ROOT"
if (( REUSE_EXISTING )); then
  log "Megévő Research Project — a tartalmát nem írom felül: $RESEARCH_ID"
else
  log "Research Project inicializálása önálló Git repositoryval."
  INIT_ARGS=(project init "$RESEARCH_ID" --id "$RESEARCH_ID"
    --title "$PROJECT_TITLE" --research-question "$RESEARCH_QUESTION")
  (( ${#OBJECTIVES[@]} )) || INIT_ARGS+=(--objective "A cél.md-ban leírtak feltérképezése és dokumentálása bizonyítható forrás-lánccal")
  for item in ${OBJECTIVES[@]+"${OBJECTIVES[@]}"}; do INIT_ARGS+=(--objective "$(strip_list_label "$item")"); done
  for item in ${SCOPE_IN[@]+"${SCOPE_IN[@]}"}; do INIT_ARGS+=(--scope-in "$(strip_list_label "$item")"); done
  for item in ${SCOPE_OUT[@]+"${SCOPE_OUT[@]}"}; do INIT_ARGS+=(--scope-out "$(strip_list_label "$item")"); done
  for item in ${CRITERIA[@]+"${CRITERIA[@]}"}; do INIT_ARGS+=(--decision-criterion "$(strip_list_label "$item")"); done
  (
    cd "$RESEARCH_ROOT"
    docker run --rm \
      --user "$(id -u):$(id -g)" --env HOME=/tmp \
      --env "RH_HARNESS_IMAGE=$RUNTIME_IMAGE" \
      --read-only --tmpfs /tmp:size=64m,mode=1777 \
      --cap-drop ALL --security-opt no-new-privileges:true \
      --network none \
      --volume "$RESEARCH_ROOT:/projects" --workdir /projects \
      "$RUNTIME_IMAGE" "${INIT_ARGS[@]}"
  )
  [[ -f "$PROJECT_DIR/.rh/project.yaml" ]] || fail "Az inicializálás nem hozta létre a .rh/project.yaml fájlt."
fi

# A projekt saját launchere a helyben deployolt image-eket használja
configure_git_identity() {
  git -C "$PROJECT_DIR" config user.name >/dev/null 2>&1 ||
    git -C "$PROJECT_DIR" config user.name "Research Launcher"
  git -C "$PROJECT_DIR" config user.email >/dev/null 2>&1 ||
    git -C "$PROJECT_DIR" config user.email "research-launcher@invalid"
}

if [[ -f "$PROJECT_DIR/rh" ]] && grep -q 'RH_IMAGE:-evidence-research-harness:' "$PROJECT_DIR/rh"; then
  log "A projekt launcher image-neveinek igazítása a helyi image-ekhez."
  sed -i \
    -e "s|IMAGE=\"\${RH_IMAGE:-evidence-research-harness:[^\"]*}\"|IMAGE=\"\${RH_IMAGE:-$RUNTIME_IMAGE}\"|" \
    -e "s|WORKER_IMAGE=\"\${RH_WORKER_IMAGE:-evidence-research-harness-worker:[^\"]*}\"|WORKER_IMAGE=\"\${RH_WORKER_IMAGE:-$WORKER_IMAGE}\"|" \
    "$PROJECT_DIR/rh"
  configure_git_identity
  if git -C "$PROJECT_DIR" status --porcelain -- rh | grep -q .; then
    git -C "$PROJECT_DIR" add rh
    git -C "$PROJECT_DIR" commit -q -m "Use deployed Research Harness images"
  fi
fi

# A fő kutatási kérdés szinkronja a reviewed main branchre
if [[ "$(git -C "$PROJECT_DIR" branch --show-current)" == "main" ]] &&
   ! grep -qF -- "$RESEARCH_QUESTION" "$PROJECT_DIR/research.yaml" 2>/dev/null; then
  configure_git_identity
  if git -C "$PROJECT_DIR" diff --quiet && git -C "$PROJECT_DIR" diff --cached --quiet; then
    log "A verziózott projektdefíció frissítése a reviewed main branchen."
    CFG=(project configure --research-question "$RESEARCH_QUESTION" --approved-by "$REVIEWER")
    for item in ${OBJECTIVES[@]+"${OBJECTIVES[@]}"}; do CFG+=(--objective "$(strip_list_label "$item")"); done
    for item in ${SCOPE_IN[@]+"${SCOPE_IN[@]}"}; do CFG+=(--scope-in "$(strip_list_label "$item")"); done
    for item in ${SCOPE_OUT[@]+"${SCOPE_OUT[@]}"}; do CFG+=(--scope-out "$(strip_list_label "$item")"); done
    for item in ${CRITERIA[@]+"${CRITERIA[@]}"}; do CFG+=(--decision-criterion "$(strip_list_label "$item")"); done
    rh_in "$PROJECT_DIR" "${CFG[@]}"
  else
    log "A working tree nem tiszta — a projektdefíció szinkronját kihagyom."
  fi
fi

rh_in "$PROJECT_DIR" validate >/dev/null || fail "A projekt validációja sikertelen: $PROJECT_DIR"

# --------------------------------------------------------------------------
# 10. Compose peldany telepese
# --------------------------------------------------------------------------
STAGE_DIR="$INSTALL_DIR.new"

deploy_compose_overlay() {
  local dir="$1" tmp tmp2 token secret
  cp -f "$FRAMEWORK_SOURCE/deploy/docker-compose.yml" "$dir/docker-compose.yml"
  tmp="$(mktemp)"
  awk '
    /^RH_CODEX_SANDBOX_MODE=/ { print "RH_CODEX_SANDBOX_MODE=danger-full-access"; next }
    /^RH_CODEX_SANDBOX_MODE_MUTATION=/ { print "RH_CODEX_SANDBOX_MODE_MUTATION=danger-full-access"; found_m = 1; next }
    /^RH_CODEX_SANDBOX_MODE_QUERY=/ { print "RH_CODEX_SANDBOX_MODE_QUERY=danger-full-access"; found_q = 1; next }
    { print }
    END {
      if (!found_m) print "RH_CODEX_SANDBOX_MODE_MUTATION=danger-full-access"
      if (!found_q) print "RH_CODEX_SANDBOX_MODE_QUERY=danger-full-access"
    }
  ' "$dir/.env" >"$tmp"
  cat "$tmp" >"$dir/.env"
  chmod 600 "$dir/.env"
  rm -f "$tmp"

  if ! grep -Eq '^RH_QUERY_AGENT_TOKEN=.+' "$dir/.env"; then
    token="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    tmp2="$(mktemp)"
    awk -v token="$token" '
      /^RH_QUERY_AGENT_TOKEN=/ { print "RH_QUERY_AGENT_TOKEN=" token; found = 1; next }
      { print }
      END { if (!found) print "RH_QUERY_AGENT_TOKEN=" token }
    ' "$dir/.env" >"$tmp2"
    cat "$tmp2" >"$dir/.env"
    chmod 600 "$dir/.env"
    rm -f "$tmp2"
  fi

  mkdir -p "$dir/query-exchange" "$dir/secrets"
  chmod 700 "$dir/query-exchange" "$dir/secrets"

  # A gateway kulcsai always provision on creation. A shared SerpApi key is
  # inherited from an existing installation so every new Research Workspace gets
  # Google and Google Scholar without any manual copy. Exa has no key on this
  # host and stays empty, which means anonymous access. An existing non-empty
  # per-instance file is never overwritten.
  local secret source
  for secret in exa_api_key serpapi_api_key pubmed_api_key; do
    if [[ -f "$dir/secrets/$secret" && ! -s "$dir/secrets/$secret" ]]; then
      case "$secret" in
        serpapi_api_key) source="$(discover_provider_key serpapi_api_key RH_SERPAPI_KEY_SOURCE || true)" ;;
        exa_api_key) source="$(discover_provider_key exa_api_key RH_EXA_KEY_SOURCE || true)" ;;
        *) source="" ;;
      esac
      if [[ -n "$source" ]]; then
        (umask 177 && cat "$source" >"$dir/secrets/$secret") &&
          log "Web-search key átvéve: $secret" ||
          log "Web-search key átvétele sikertelen: $secret — üres marad"
      fi
    elif [[ ! -e "$dir/secrets/$secret" ]]; then
      (umask 177 && : >"$dir/secrets/$secret")
    fi
    chmod 600 "$dir/secrets/$secret"
  done

  set_env_key "$dir" RH_QUERY_EXCHANGE_DIR "$dir/query-exchange"
  set_env_key "$dir" RH_EXA_API_KEY_FILE "$dir/secrets/exa_api_key"
  set_env_key "$dir" RH_SERPAPI_API_KEY_FILE "$dir/secrets/serpapi_api_key"
  set_env_key "$dir" RH_DOMAIN_SKILLS_DIR "$RH_DOMAIN_SKILLS_DIR"
  set_env_key "$dir" RH_DOMAIN_SKILLS_ALLOWLIST "$RH_DOMAIN_SKILLS_ALLOWLIST"
  set_env_key "$dir" RH_RECOVERY_REQUIRE_AUTH "${RH_RECOVERY_REQUIRE_AUTH:-0}"
  set_env_key "$dir" RH_AUTH_USER_HEADER "${RH_AUTH_USER_HEADER:-X-Remote-User}"
  compose_dir "$dir" config --quiet
}

install_instance() {
  local stage="$1"
  (
    cd "$PROJECT_DIR"
    RH_OFFLINE=1 \
    RH_INSTANCE="$RESEARCH_ID" \
    RH_INSTALL_DIR="$stage" \
    RH_PROJECT_DIR="$PROJECT_DIR" \
    RH_UI_PORT="$UI_PORT" \
    RH_CODEX_SANDBOX_MODE_MUTATION=danger-full-access \
    RH_CODEX_SANDBOX_MODE_QUERY=danger-full-access \
    RH_CODEX_PROVIDER_BASE_URL="$LLM_URL" \
    RH_CODEX_MODEL="$LLM_MODEL" \
    RH_IMAGE="$RUNTIME_IMAGE" \
    RH_WORKER_IMAGE="$WORKER_IMAGE" \
    "$INSTALLER_ROOT/install.sh" \
      --instance "$RESEARCH_ID" \
      --install-dir "$stage" \
      --project-dir "$PROJECT_DIR" \
      --prefix "$IMAGE_PREFIX" \
      --tag "$IMAGE_TAG" \
      --no-start
  )
}

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  log "Compose példány telepítése: $INSTALL_DIR"
  if [[ -e "$STAGE_DIR" ]]; then
    log "Félbemaradt staging könyvtár áthelyezése: $STAGE_DIR"
    mv "$STAGE_DIR" "$INSTALL_DIR.failed-$(date +%Y%m%d-%H%M%S)"
  fi
  mkdir -p "$STAGE_DIR"
  install_instance "$STAGE_DIR"
  log "Példány konfigurálása: belső Codex sandbox ki, web-search gateway be."
  deploy_compose_overlay "$STAGE_DIR"
  mv "$STAGE_DIR" "$INSTALL_DIR"
elif ! compose_dir "$INSTALL_DIR" config --quiet >/dev/null 2>&1; then
  log "A telepített példány layout frissítése."
  deploy_compose_overlay "$INSTALL_DIR"
fi

if (( NO_START )); then
  log "Telepítés kész, indítás nélkül. Indítás: $INSTALL_DIR/manage.sh start"
  exit 0
fi

# --------------------------------------------------------------------------
# 11. Kontenerek elinditasa es health check
# --------------------------------------------------------------------------
docker network inspect "$GATEWAY_NETWORK" >/dev/null 2>&1 || docker network create "$GATEWAY_NETWORK" >/dev/null

log "Research Workspace indítása / újraindítása."
compose_dir "$INSTALL_DIR" up -d --pull never

HEALTH_OK=0
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 2 "http://127.0.0.1:$UI_PORT/health" >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 1
done
if (( HEALTH_OK )); then
  log "A Workspace health igazolt: http://127.0.0.1:$UI_PORT"
else
  log "FIGYELEM: a health végpont 60 másodperc alatt nem igazolt. Napló: $INSTALL_DIR/manage.sh logs workspace"
fi

# --------------------------------------------------------------------------
# 12. Elso kutatasi kor
# --------------------------------------------------------------------------
JOB_ID="kihagyva (--no-job)"
JOB_MARKER="$INSTALL_DIR/first-cycle-job-id"

if (( NO_JOB )); then
  log "--no-job: az első kutatási kört nem állítom sorba."
else
  job_state_has_any_job() {
    rh_in "$PROJECT_DIR" job list --limit 50 2>/dev/null | grep -q '"id"'
  }

  if [[ ! -s "$JOB_MARKER" ]] && job_state_has_any_job; then
    log "A job store már tartalmaz munkákat → nem sorozok be új kört."
    : >"$JOB_MARKER"
    printf 'existing-jobs\n' >>"$JOB_MARKER"
  fi

  if [[ ! -s "$JOB_MARKER" ]]; then
    SOURCE_MODE_ARGS=()
    if rh_in "$PROJECT_DIR" job create --help 2>&1 | grep -q -- '--source-mode'; then
      SOURCE_MODE_ARGS=(--source-mode web)
    fi

    PROMPT_FILE="$(mktemp)"
    {
      printf 'Indíts egy evidencia-alapú kutatási kört az alábbi cél mentén.\n\n'
      printf 'FŐ KUTATÁSI KÉRDÉS: %s\n\n' "$RESEARCH_QUESTION"
      (( ${#OBJECTIVES[@]} )) && { printf 'CÉLOK:\n'; for item in "${OBJECTIVES[@]}"; do printf -- '- %s\n' "$(strip_list_label "$item")"; done; printf '\n'; }
      (( ${#SCOPE_IN[@]} )) && { printf 'A KUTATÁS HOZZÁSZÓL:\n'; for item in "${SCOPE_IN[@]}"; do printf -- '- %s\n' "$(strip_list_label "$item")"; done; printf '\n'; }
      (( ${#SCOPE_OUT[@]} )) && { printf 'A KUTATÁS NEM SZEREPE:\n'; for item in "${SCOPE_OUT[@]}"; do printf -- '- %s\n' "$(strip_list_label "$item")"; done; printf '\n'; }
      (( ${#CRITERIA[@]} )) && { printf 'DÖNTÉSI / ÉRTÉKELÉSI KRITÉRIUMOK:\n'; for item in "${CRITERIA[@]}"; do printf -- '- %s\n' "$(strip_list_label "$item")"; done; printf '\n'; }
      (( ${#FREEFORM[@]} )) && { printf 'A CÉLFAJL EGYÉB MEGJEGYZÉSEI:\n'; printf '%s\n' "${FREEFORM[@]}"; printf '\n'; }
      cat <<'METHOD'
MÓDSZERTAN:
- Végezz szisztematikus, reprodukálható webes és publikus forráskeresést több nyelven
  (magyar, angol): peer-reviewed cikkek, hivatalos szabványok és irányítási keretek,
  dokumentált esettanulmányok, nyilvános auditok, nyílt forráskódú projektek,
  iparági jelentések.
- Gyűjts pozitív, negatív és részben sikeres eseteket is; jelöld a kudarcokat, a korai
  és késői hatásokat, valamint a replikálhatóság korlátait.
- Forrásonként rögzítsd: kiindulóállapot, bevezetett intézkedések, technológiai és
  irányítási megoldások, szerepkörök, mutatók, mért hatások, kontextus, korlátok.
- Készíts összehasonlító táblázatot, téma szerinti szintézist és ellentmondás-elemzést;
  jelöld külön a bizonyított, részben bizonyított és bizonyítatlan állításokat.
- Ne használj marketingállításokat bizonyíték nélkül; a hiányzó evidenciát ne pótold
  sejtéssel.
- A kutatási térben SOURCE, EVIDENCE és CLAIM objektumok valamint auditálható láncok
  jöjjenek létre; az eredmény a reviewed main branchre kerüljön commit és merge
  workflow-val.
METHOD
    } >"$PROMPT_FILE"

    SUBQUESTION_ARGS=()
    if (( ${#OBJECTIVES[@]} )); then
      for item in "${OBJECTIVES[@]}"; do
        SUBQUESTION_ARGS+=(--subquestion "Milyen bizonyíték támasztja alá és valósítható meg: $(strip_list_label "$item")")
      done
    else
      SUBQUESTION_ARGS=(
        --subquestion "Milyen dokumentált, másolható megoldások és eredmények vannak a kutatási célban leírtakra?"
        --subquestion "Milyen előfeltételek, kockázatok és korlátok mellett működnek ezek a megoldások?"
        --subquestion "Milyen mutatókkal igazolhatók a mért hatások, és hogyan validálhatók?"
      )
    fi

    CRITERION_ARGS=()
    for item in ${CRITERIA[@]+"${CRITERIA[@]}"}; do
      CRITERION_ARGS+=(--completion-criterion "Ellenőrizhetővé teszi: $(strip_list_label "$item")")
    done
    CRITERION_ARGS+=(
      --completion-criterion "Legalább tíz ellenőrizhető, dokumentált forrás kerül begyűjtésre, pozitív és negatív esetekkel."
      --completion-criterion "Minden forrásról rögzítésre kerülnek a kontextus, intézkedések, mutatók, hatások és korlátok."
      --completion-criterion "Minden lényeges következtetés mögött ellenőrizhető SOURCE-EVIDENCE-CLAIM lánc áll, az ellentmondások külön jelölve."
      --completion-criterion "Készül összehasonlító elemzés és javasolt keret vagy végrehajtási terv a mérési korlátok feltárásával."
    )

    log "Az első evidencia-gyűjtő kutatási kör létrehozása."
    JOB_JSON="$(
      cd "$PROJECT_DIR"
      docker run --rm \
        --user "$(id -u):$(id -g)" --env HOME=/tmp \
        --env "RH_HARNESS_IMAGE=$RUNTIME_IMAGE" \
        --read-only --tmpfs /tmp:size=64m,mode=1777 \
        --cap-drop ALL --security-opt no-new-privileges:true \
        --volume "$PROJECT_DIR:/workspace" --workdir /workspace \
        --volume "$PROMPT_FILE:/tmp/goal-prompt.md:ro" \
        "$RUNTIME_IMAGE" job create "${SOURCE_MODE_ARGS[@]}" \
          --title "$JOB_TITLE" \
          --runner "$JOB_RUNNER" \
          --mode new \
          --prompt-file /tmp/goal-prompt.md \
          --perspective "Szervezeti, folyamat és szerepkör szempont" \
          --perspective "Technológiai, adat és governance szempont" \
          --perspective "Mérés, KPI, ROI és validálás szempontja" \
          --perspective "Kockázat, megfelelés és változáskezelés szempontja" \
          "${SUBQUESTION_ARGS[@]}" \
          "${CRITERION_ARGS[@]}"
    )"
    rm -f "$PROMPT_FILE"
    JOB_ID="$(printf '%s' "$JOB_JSON" | awk -F'"' '/"id"[[:space:]]*:/ { print $4; exit }')"
    [[ -n "$JOB_ID" ]] || fail "A job létrejött, de az azonosító nem olvasható ki: $(printf '%.300s' "$JOB_JSON")"
    printf '%s\n' "$JOB_ID" >"$JOB_MARKER"
    rh_in "$PROJECT_DIR" job transition "$JOB_ID" QUEUED \
      --actor new-research-launcher \
      --note "Első evidencia-gyűjtő kört sorba állította a start-new-research.sh" >/dev/null
    log "Az első kutatási kör sorba állítva: $JOB_ID"
  else
    JOB_ID="$(tr -d '\r\n' <"$JOB_MARKER")"
    [[ "$JOB_ID" == "existing-jobs" ]] && JOB_ID="meglévő job store → $INSTALL_DIR/manage.sh status vagy rh job list"
    log "A job store már tartalmaz munkákat → nem sorozok be új kört."
  fi
fi

# --------------------------------------------------------------------------
# 13. Zaro riport
# --------------------------------------------------------------------------
printf '\n'
printf 'Projekt:      %s\n' "$PROJECT_DIR"
printf 'Példány:      %s\n' "$INSTALL_DIR"
printf 'Workspace:    http://127.0.0.1:%s\n' "$UI_PORT"
printf 'Health:       curl -fsS http://127.0.0.1:%s/health\n' "$UI_PORT"
printf 'Első job:     %s\n' "$JOB_ID"
printf 'Kezelés:      %s/manage.sh status\n' "$INSTALL_DIR"
printf 'Újraindítás:  %s --id %s --restart-only\n' "$SCRIPT_PATH" "$RESEARCH_ID"
if (( SECURE_LOGIN )); then
  printf '\n'
  printf 'Secure Login: a bekötést a script végrehajtja (a generált Caddy overlay-jel).\n'
fi

if (( SECURE_LOGIN && ! BIND_ONLY )); then
  log "Secure Login bekötés."
  bind_secure_login_gateway
fi
