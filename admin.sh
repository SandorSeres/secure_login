#!/usr/bin/env bash
# Local interactive administration for the Secure Login N:M data model.
set -Eeuo pipefail
export LC_ALL=C

die() { printf 'HIBA: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }
confirm() {
    local answer
    read -r -p "$1 [y/N]: " answer || return 1
    [[ "$answer" =~ ^([yY]|yes|YES|Yes)$ ]]
}
valid_app_id() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]]; }
valid_number() { [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 )); }

# Explicitly bind to the Compose project next to THIS script. Never scan
# unrelated PostgreSQL containers on the host.
cd -- "$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")"
command -v docker >/dev/null 2>&1 || die "a docker parancs nem található."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 szükséges."
[[ -f docker-compose.yaml || -f docker-compose.yml || -f compose.yaml || -f compose.yml ]] ||
    die "nem található Compose konfiguráció a szkript könyvtárában."
[[ "$(docker compose ps --status running --services 2>/dev/null | grep -xc db || true)" == 1 ]] ||
    die "a Secure Login Compose-projekt db szolgáltatása nem fut."

# The DB container has POSTGRES_USER and POSTGRES_DB; do not source .env,
# interpolate credentials or pass user input as executable SQL.
sql() {
    docker compose exec -T db sh -c \
      'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off "$@"' \
      psql "$@"
}
scalar() { sql -qAt "$@"; }

SCHEMA_OK="$(scalar <<'SQL'
SELECT CASE WHEN
    to_regclass('public.users') IS NOT NULL AND
    to_regclass('public.applications') IS NOT NULL AND
    to_regclass('public.user_application_access') IS NOT NULL AND
    (SELECT count(*) FROM information_schema.columns
      WHERE table_schema='public' AND table_name='users'
      AND column_name IN ('id','username','email','account_level')) = 4 AND
    (SELECT count(*) FROM information_schema.columns
      WHERE table_schema='public' AND table_name='applications'
      AND column_name IN ('id','name')) = 2 AND
    (SELECT count(*) FROM information_schema.columns
      WHERE table_schema='public' AND table_name='user_application_access'
      AND column_name IN ('user_id','application_id')) = 2
THEN 'yes' ELSE 'no' END;
SQL
)" || die "adatbázis-ellenőrzés sikertelen."
[[ "$SCHEMA_OK" == yes ]] || die "ez az adatbázis nem a Secure Login N:M sémája."

list_users() {
    info ""
    info "=== Felhasználók, szintek és alkalmazások ==="
    sql <<'SQL'
SELECT u.id, u.username, u.email, u.account_level,
       COALESCE(string_agg(a.application_id, ', ' ORDER BY a.application_id), '—') AS applications
FROM users AS u
LEFT JOIN user_application_access AS a ON a.user_id = u.id
GROUP BY u.id, u.username, u.email, u.account_level
ORDER BY u.id;
SQL
}

list_apps() {
    info ""
    info "=== Alkalmazások és hozzárendelések száma ==="
    sql <<'SQL'
SELECT a.id, a.name, count(g.user_id) AS users
FROM applications AS a
LEFT JOIN user_application_access AS g ON g.application_id = a.id
GROUP BY a.id, a.name
ORDER BY a.id;
SQL
}

# SQL-safe string binding: psql :'v_name' quotes the value as a PostgreSQL
# string literal; untrusted input never becomes SQL code.
choose_user() {
    local username id
    read -r -p "Felhasználónév: " username || die "megszakítva."
    [[ -n "$username" ]] || die "a felhasználónév nem lehet üres."
    id="$(scalar -v "v_username=$username" <<'SQL'
SELECT id FROM users WHERE username = :'v_username';
SQL
)" || die "felhasználó lekérdezése sikertelen."
    [[ "$id" =~ ^[0-9]+$ ]] || die "nincs ilyen felhasználó: $username"
    SELECTED_USER_ID="$id"
    SELECTED_USERNAME="$username"
}

choose_app() {
    local app_id found
    read -r -p "Alkalmazásazonosító: " app_id || die "megszakítva."
    valid_app_id "$app_id" || die "az alkalmazásazonosító kisbetű, szám és kötőjel lehet (1–63 karakter)."
    found="$(scalar -v "v_app=$app_id" <<'SQL'
SELECT id FROM applications WHERE id = :'v_app';
SQL
)" || die "alkalmazás lekérdezése sikertelen."
    [[ "$found" == "$app_id" ]] || die "nincs ilyen alkalmazás: $app_id"
    SELECTED_APP_ID="$app_id"
}

set_level() {
    choose_user
    local current option level
    current="$(scalar -v "v_id=$SELECTED_USER_ID" <<'SQL'
SELECT account_level FROM users WHERE id = :'v_id'::integer;
SQL
)" || die "szint lekérdezése sikertelen."
    printf 'Jelenlegi szint: %s\n1) Free\n2) Pro\n3) Enterprise\n' "$current"
    read -r -p "Új szint [1-3]: " option || die "megszakítva."
    case "$option" in
      1) level=Free ;; 2) level=Pro ;; 3) level=Enterprise ;;
      *) die "érvénytelen szint." ;;
    esac
    [[ "$current" != "$level" ]] || { info "Nem történt módosítás."; return; }
    confirm "Biztosan '$level' legyen '$SELECTED_USERNAME' szintje?" || { info "Megszakítva."; return; }
    sql -v "v_id=$SELECTED_USER_ID" -v "v_level=$level" <<'SQL'
BEGIN;
UPDATE users SET account_level = :'v_level' WHERE id = :'v_id'::integer;
COMMIT;
SELECT id, username, account_level FROM users WHERE id = :'v_id'::integer;
SQL
}

create_app() {
    local app_id app_name count
    read -r -p "Új alkalmazásazonosító (kisbetű/szám/kötőjel): " app_id || die "megszakítva."
    valid_app_id "$app_id" || die "érvénytelen alkalmazásazonosító (1–63 karakter)."
    read -r -p "Alkalmazás megjelenített neve: " app_name || die "megszakítva."
    [[ -n "$app_name" && "${#app_name}" -le 100 ]] ||
        die "a név 1–100 karakter hosszú legyen."
    count="$(scalar -v "v_app=$app_id" <<'SQL'
SELECT count(*) FROM applications WHERE id = :'v_app';
SQL
)" || die "alkalmazás lekérdezése sikertelen."
    [[ "$count" == 0 ]] || die "ilyen alkalmazás már létezik."
    confirm "Létrehozod az alkalmazást: $app_id ($app_name)?" || { info "Megszakítva."; return; }
    sql -v "v_app=$app_id" -v "v_name=$app_name" <<'SQL'
BEGIN;
INSERT INTO applications (id, name) VALUES (:'v_app', :'v_name');
COMMIT;
SELECT id, name FROM applications WHERE id = :'v_app';
SQL
}

show_user_apps() {
    choose_user
    info ""
    printf '=== %s alkalmazásai ===\n' "$SELECTED_USERNAME"
    sql -v "v_id=$SELECTED_USER_ID" <<'SQL'
SELECT a.id, a.name
FROM user_application_access AS g
JOIN applications AS a ON a.id = g.application_id
WHERE g.user_id = :'v_id'::integer
ORDER BY a.id;
SQL
}

grant() {
    choose_user
    list_apps
    choose_app
    local count
    count="$(scalar -v "v_id=$SELECTED_USER_ID" -v "v_app=$SELECTED_APP_ID" <<'SQL'
SELECT count(*) FROM user_application_access
WHERE user_id = :'v_id'::integer AND application_id = :'v_app';
SQL
)" || die "hozzárendelés ellenőrzése sikertelen."
    [[ "$count" == 0 ]] || { info "Ez a hozzárendelés már létezik; nem történt módosítás."; return; }
    confirm "Hozzárendeled '$SELECTED_APP_ID' alkalmazást '$SELECTED_USERNAME' felhasználóhoz?" ||
        { info "Megszakítva."; return; }
    sql -v "v_id=$SELECTED_USER_ID" -v "v_app=$SELECTED_APP_ID" <<'SQL'
BEGIN;
INSERT INTO user_application_access (user_id, application_id)
VALUES (:'v_id'::integer, :'v_app')
ON CONFLICT DO NOTHING;
COMMIT;
SELECT user_id, application_id FROM user_application_access
WHERE user_id = :'v_id'::integer AND application_id = :'v_app';
SQL
}

revoke() {
    choose_user
    info "=== Jelenlegi hozzárendelések ==="
    sql -v "v_id=$SELECTED_USER_ID" <<'SQL'
SELECT application_id FROM user_application_access
WHERE user_id = :'v_id'::integer ORDER BY application_id;
SQL
    choose_app
    local count
    count="$(scalar -v "v_id=$SELECTED_USER_ID" -v "v_app=$SELECTED_APP_ID" <<'SQL'
SELECT count(*) FROM user_application_access
WHERE user_id = :'v_id'::integer AND application_id = :'v_app';
SQL
)" || die "hozzárendelés ellenőrzése sikertelen."
    [[ "$count" == 1 ]] || { info "Nincs ilyen hozzárendelés; nem történt módosítás."; return; }
    confirm "Visszavonod '$SELECTED_APP_ID' hozzáférést '$SELECTED_USERNAME' felhasználótól?" ||
        { info "Megszakítva."; return; }
    sql -v "v_id=$SELECTED_USER_ID" -v "v_app=$SELECTED_APP_ID" <<'SQL'
BEGIN;
DELETE FROM user_application_access
WHERE user_id = :'v_id'::integer AND application_id = :'v_app';
COMMIT;
SELECT user_id, application_id FROM user_application_access
WHERE user_id = :'v_id'::integer ORDER BY application_id;
SQL
}

info "Secure Login — helyi adminisztráció (N:M)"
info "Az adatbázis a jelenlegi Docker Compose-projekt 'db' szolgáltatása."
while true; do
    printf '\n1) Felhasználók listázása\n2) Jogosultsági szint módosítása\n3) Alkalmazások listázása\n4) Új alkalmazás felvétele\n5) Alkalmazás hozzárendelése\n6) Alkalmazás-hozzáférés visszavonása\n7) Felhasználó alkalmazásai\n0) Kilépés\n'
    read -r -p "Művelet [0-7]: " op || { info "Kilépés."; exit 0; }
    case "$op" in
      0) info "Kilépés."; exit 0 ;;
      1) list_users ;;
      2) set_level ;;
      3) list_apps ;;
      4) create_app ;;
      5) grant ;;
      6) revoke ;;
      7) show_user_apps ;;
      *) info "Érvénytelen menüpont." ;;
    esac
done
