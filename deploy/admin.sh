#!/usr/bin/env bash
set -Eeuo pipefail

die() {
    echo "HIBA: $*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || die "a docker parancs nem található."

# ---------------------------------------------------------------------------
# PostgreSQL konténer megkeresése
# ---------------------------------------------------------------------------

mapfile -t RUNNING_CONTAINERS < <(
    docker ps --format '{{.ID}}'
)

((${#RUNNING_CONTAINERS[@]} > 0)) || die "nincs futó Docker konténer."

CANDIDATES=()

for cid in "${RUNNING_CONTAINERS[@]}"; do
    image="$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true)"

    case "$image" in
        postgres|postgres:*|*/postgres|*/postgres:*)
            ;;
        *)
            continue
            ;;
    esac

    pg_user="$(docker exec "$cid" printenv POSTGRES_USER 2>/dev/null || true)"
    pg_db="$(docker exec "$cid" printenv POSTGRES_DB 2>/dev/null || true)"

    [[ -n "$pg_user" ]] || pg_user="postgres"
    [[ -n "$pg_db" ]] || pg_db="$pg_user"

    if docker exec "$cid" \
        psql -X -qAt \
        -U "$pg_user" \
        -d "$pg_db" \
        -v ON_ERROR_STOP=1 \
        -c "
            SELECT CASE
                WHEN to_regclass('public.users') IS NOT NULL
                 AND EXISTS (
                     SELECT 1
                     FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND table_name = 'users'
                       AND column_name = 'username'
                 )
                 AND EXISTS (
                     SELECT 1
                     FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND table_name = 'users'
                       AND column_name = 'account_level'
                 )
                THEN 'yes'
                ELSE 'no'
            END;
        " 2>/dev/null | grep -qx 'yes'
    then
        CANDIDATES+=("$cid")
    fi
done

((${#CANDIDATES[@]} > 0)) || die \
"nem találok olyan futó PostgreSQL konténert, amelyben van users.username és users.account_level."

if ((${#CANDIDATES[@]} == 1)); then
    DB_CONTAINER="${CANDIDATES[0]}"
else
    echo
    echo "Több megfelelő PostgreSQL konténert találtam:"
    echo

    for i in "${!CANDIDATES[@]}"; do
        cid="${CANDIDATES[$i]}"
        name="$(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##')"
        image="$(docker inspect -f '{{.Config.Image}}' "$cid")"
        printf "  %d) %-30s %s\n" "$((i+1))" "$name" "$image"
    done

    echo
    read -r -p "Melyiket használjam? [1-${#CANDIDATES[@]}]: " CHOICE

    [[ "$CHOICE" =~ ^[0-9]+$ ]] || die "érvénytelen választás."
    ((CHOICE >= 1 && CHOICE <= ${#CANDIDATES[@]})) || die "érvénytelen választás."

    DB_CONTAINER="${CANDIDATES[$((CHOICE-1))]}"
fi

DB_NAME="$(docker inspect -f '{{.Name}}' "$DB_CONTAINER" | sed 's#^/##')"

PG_USER="$(docker exec "$DB_CONTAINER" printenv POSTGRES_USER 2>/dev/null || true)"
PG_DB="$(docker exec "$DB_CONTAINER" printenv POSTGRES_DB 2>/dev/null || true)"

[[ -n "$PG_USER" ]] || PG_USER="postgres"
[[ -n "$PG_DB" ]] || PG_DB="$PG_USER"

# ---------------------------------------------------------------------------
# Adatbázis-séma ellenőrzése
# ---------------------------------------------------------------------------

ACCOUNT_LEVEL_TYPE="$(
    docker exec "$DB_CONTAINER" \
        psql -X -qAt \
        -U "$PG_USER" \
        -d "$PG_DB" \
        -v ON_ERROR_STOP=1 \
        -c "
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'users'
              AND column_name = 'account_level';
        "
)"

case "$ACCOUNT_LEVEL_TYPE" in
    "character varying"|text)
        ;;
    *)
        die "a users.account_level mező típusa még '$ACCOUNT_LEVEL_TYPE'; előbb indítsd el az új auth image-et az enum migrációhoz."
        ;;
esac

ACCESS_GROUPS_TABLE="$(
    docker exec "$DB_CONTAINER" \
        psql -X -qAt \
        -U "$PG_USER" \
        -d "$PG_DB" \
        -v ON_ERROR_STOP=1 \
        -c "SELECT to_regclass('public.access_groups');"
)"

[[ "$ACCESS_GROUPS_TABLE" == "access_groups" ]] || die \
"az access_groups tábla nem létezik; előbb indítsd el az új auth image-et."

DEFAULT_GROUP_COUNT="$(
    docker exec "$DB_CONTAINER" \
        psql -X -qAt \
        -U "$PG_USER" \
        -d "$PG_DB" \
        -v ON_ERROR_STOP=1 \
        -c "SELECT count(*) FROM access_groups WHERE is_default;"
)"

[[ "$DEFAULT_GROUP_COUNT" == "1" ]] || die \
"az access_groups táblában pontosan egy alapértelmezett csoportnak kell lennie."

# ---------------------------------------------------------------------------
# Konfigurált csoportazonosítók beolvasása az adatbázisból
# ---------------------------------------------------------------------------

ACCOUNT_LEVEL_OUTPUT="$(
    docker exec "$DB_CONTAINER" \
        psql -X -qAt \
        -U "$PG_USER" \
        -d "$PG_DB" \
        -v ON_ERROR_STOP=1 \
        -c "SELECT id FROM access_groups ORDER BY sort_order, id;"
)" || die "nem sikerült kiolvasni a konfigurált csoportokat az adatbázisból."

[[ -n "$ACCOUNT_LEVEL_OUTPUT" ]] || die "az access_groups tábla nem tartalmaz csoportot."
mapfile -t ACCOUNT_LEVELS <<< "$ACCOUNT_LEVEL_OUTPUT"

for level in "${ACCOUNT_LEVELS[@]}"; do
    [[ "$level" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die \
        "érvénytelen csoportazonosító az access_groups táblában."
done

# ---------------------------------------------------------------------------
# Felhasználók listázása
# ---------------------------------------------------------------------------

echo
echo "Adatbázis konténer: $DB_NAME"
echo
echo "=== Felhasználók ==="

docker exec "$DB_CONTAINER" \
    psql -X \
    -U "$PG_USER" \
    -d "$PG_DB" \
    -P pager=off \
    -c "
        SELECT
            id,
            username,
            email,
            account_level,
            created_at,
            last_activity
        FROM users
        ORDER BY id;
    "

# ---------------------------------------------------------------------------
# Felhasználó bekérése
# ---------------------------------------------------------------------------

echo
read -r -p "Felhasználónév: " USERNAME
[[ -n "$USERNAME" ]] || die "a felhasználónév nem lehet üres."

CURRENT_LEVEL="$(
    docker exec -i "$DB_CONTAINER" \
        psql -X -qAt \
        -U "$PG_USER" \
        -d "$PG_DB" \
        -v ON_ERROR_STOP=1 \
        -v "v_username=$USERNAME" <<'SQL'
SELECT account_level
FROM users
WHERE username = :'v_username';
SQL
)"

[[ -n "$CURRENT_LEVEL" ]] || die "nincs ilyen felhasználó: $USERNAME"
CURRENT_LEVEL_ID="${CURRENT_LEVEL,,}"

# ---------------------------------------------------------------------------
# Account level kiválasztása számmal
# ---------------------------------------------------------------------------

echo
echo "Jelenlegi account_level: $CURRENT_LEVEL"
echo
echo "=== Választható, konfigurált account_level szintek ==="
echo

for i in "${!ACCOUNT_LEVELS[@]}"; do
    level="${ACCOUNT_LEVELS[$i]}"

    if [[ "$level" == "$CURRENT_LEVEL_ID" ]]; then
        printf "  %d) %s  [jelenlegi]\n" "$((i+1))" "$level"
    else
        printf "  %d) %s\n" "$((i+1))" "$level"
    fi
done

echo
read -r -p "Válassz szintet [1-${#ACCOUNT_LEVELS[@]}]: " LEVEL_CHOICE

[[ "$LEVEL_CHOICE" =~ ^[0-9]+$ ]] || die "csak számot adj meg."
((LEVEL_CHOICE >= 1 && LEVEL_CHOICE <= ${#ACCOUNT_LEVELS[@]})) || \
    die "érvénytelen választás."

ACCOUNT_LEVEL="${ACCOUNT_LEVELS[$((LEVEL_CHOICE-1))]}"

if [[ "$ACCOUNT_LEVEL" == "$CURRENT_LEVEL_ID" ]]; then
    echo
    echo "A kiválasztott szint megegyezik a jelenlegivel: $CURRENT_LEVEL"
    echo "Nem történt módosítás."
    exit 0
fi

# ---------------------------------------------------------------------------
# Megerősítés
# ---------------------------------------------------------------------------

echo
echo "Módosítás:"
echo "  user          : $USERNAME"
echo "  jelenlegi     : $CURRENT_LEVEL"
echo "  új szint      : $ACCOUNT_LEVEL"
echo

read -r -p "Biztosan módosítod? [y/N] " CONFIRM

case "$CONFIRM" in
    y|Y|yes|YES|Yes)
        ;;
    *)
        echo "Megszakítva. Nem történt módosítás."
        exit 0
        ;;
esac

# ---------------------------------------------------------------------------
# UPDATE + visszaellenőrzés
# ---------------------------------------------------------------------------

echo

docker exec -i "$DB_CONTAINER" \
    psql -X \
    -U "$PG_USER" \
    -d "$PG_DB" \
    -v ON_ERROR_STOP=1 \
    -v "v_username=$USERNAME" \
    -v "v_level=$ACCOUNT_LEVEL" \
    -P pager=off <<'SQL'
BEGIN;

UPDATE users
SET account_level = :'v_level'
WHERE username = :'v_username';

SELECT
    id,
    username,
    email,
    account_level
FROM users
WHERE username = :'v_username';

COMMIT;
SQL

echo
echo "Kész."
