# DGX Spark secure portal – `code.hu`

Ez a csomag egyetlen publikus HTTPS belépési pont mögé teszi a DGX Sparkon
futó Secure Login portált, az AI Dashboardot és a Chat Harnesst.

## Címek és belső portok

| Publikus cím | Szerep | DGX upstream |
| --- | --- | --- |
| `https://ai.code.hu` | belépés és alkalmazáslista | `127.0.0.1:8100` |
| `https://dashboard.code.hu` | AI Dashboard | `127.0.0.1:8000` |
| `https://chat-harness.code.hu` | OpenWebUI Harness | `127.0.0.1:8001` |

A `8000`, `8001` és `8100` portok csak loopback címen figyelnek. A
`aihub.webhop.info:8000` és `aihub.webhop.info:8001` ezért közvetlenül nem
érhető el. Az egyetlen WAN felől továbbított port a TCP 443.

## DNS

A `code.hu` DNS-zónában hozd létre ezeket a rekordokat:

```dns
ai.code.hu.            CNAME  aihub.webhop.info.
dashboard.code.hu.     CNAME  aihub.webhop.info.
chat-harness.code.hu.  CNAME  aihub.webhop.info.
```

Mindhárom név ugyanarra a DynDNS-sel frissített DGX Spark címre mutat. A
tanúsítványokat a Caddy külön-külön kéri le. A konfiguráció kizárólag a
TLS-ALPN ACME-kihívást használja, amely a publikus 443-as porton működik; a
80-as portot nem kell továbbítani.

## Router és tűzfal

- A routeren csak `TCP 443 -> DGX Spark TCP 443` porttovábbítás legyen.
- A DGX tűzfalán WAN felől csak a TCP 443 legyen engedélyezve.
- Ne továbbítsd és ne engedélyezd kívülről a 80, 8000, 8001, 8100, 5432,
  6379 vagy 2019 portot.
- Az AI Dashboard és a Chat Harness host-portját kötelező
  `127.0.0.1`-hez kötni, nem `0.0.0.0`-hoz.

Példa a két alkalmazás Docker portkötésére:

```yaml
# AI Dashboard
ports:
  - "127.0.0.1:8000:8000"

# Chat Harness / OpenWebUI (a konténer belső portja jellemzően 8080)
ports:
  - "127.0.0.1:8001:8080"
```

Ha a Chat Harness saját nginx konténere jelenleg a 80/443 portokat foglalja,
kapcsold ki azt a szolgáltatást: a publikus TLS és az access control ezentúl a
csomag Caddy szolgáltatásának feladata.

## Chat Harness trusted-header beállítás

Az OpenWebUI környezetében állítsd be:

```dotenv
WEBUI_URL=https://chat-harness.code.hu
CORS_ALLOW_ORIGIN=https://chat-harness.code.hu
WEBUI_SESSION_COOKIE_SECURE=true
WEBUI_AUTH_TRUSTED_EMAIL_HEADER=X-Authenticated-Email
WEBUI_AUTH_TRUSTED_NAME_HEADER=X-Authenticated-User
WEBUI_AUTH_TRUSTED_GROUPS_HEADER=X-Authenticated-Groups
```

Az OpenWebUI upstream kizárólag loopbacken legyen elérhető. A Caddy törli a
kliens által küldött azonosító fejléceket, majd csak sikeres Secure Login
ellenőrzés után írja be a hitelesített értékeket. Az OpenWebUI-csoportokat
előre létre kell hozni, ha ott is csoportalapú jogosultságot használsz.

## Telepítés

1. A Secure Login repository gyökerében építsd és publikáld az auth image-et:

   ```bash
   ./build_and_push.sh --prefix sajat-registry/secure-login --push
   ```

2. Másold a `secure-login-dgx-spark-deploy.zip` fájlt a DGX Sparkra, majd egy
   üres könyvtárban csomagold ki.

3. Készítsd el a titkos, nem verziókezelt `.env` fájlt:

   ```bash
   cp .env.example .env
   ```

   Cseréld le az összes `replace-...` értéket. Adj meg valós `ACME_EMAIL`
   címet, erős és egymástól független `SECRET_KEY`, `CSRF_KEY` és
   `PG_PASSWORD` értékeket, a működő SMTP-adatokat, valamint a publikált
   `AUTH_IMAGE` teljes nevét. A `DATABASE_URL` jelszava egyezzen a
   `PG_PASSWORD` értékével; URL-speciális karaktereket percent-kódolni kell.

4. Indítsd el az AI Dashboardot a `127.0.0.1:8000`, a Chat Harnesst pedig a
   `127.0.0.1:8001` portkötéssel. Ellenőrizd, hogy egyik app sem foglalja a
   443-as portot.

5. Ellenőrizd és indítsd a portal stacket:

   ```bash
   cd deploy
   docker compose --env-file ../.env config --quiet
   docker compose --env-file ../.env pull
   docker compose --env-file ../.env up -d
   docker compose --env-file ../.env ps
   ```

A Caddy szándékosan host network módban fut, hogy elérje a DGX loopbackre
kötött appjait. Csak a három rögzített upstream szerepel a Caddyfile-ban; nincs
felhasználó által megadható általános host/port proxy. A PostgreSQL és a Redis
belső Docker-hálózaton marad, az auth szolgáltatás pedig csak a
`127.0.0.1:8100` host-porton jelenik meg.

## Ellenőrzés

Külső hálózatról:

```bash
curl -I https://ai.code.hu/
curl -I https://dashboard.code.hu/
curl -I https://chat-harness.code.hu/
curl --connect-timeout 5 http://aihub.webhop.info:8000/
curl --connect-timeout 5 http://aihub.webhop.info:8001/
```

Az első kérésnek HTTPS-választ kell adnia. Bejelentkezés nélkül a dashboard és
a chat-harness 303 átirányítást ad az `https://ai.code.hu/` címre. Az utolsó
két kapcsolatnak időtúllépéssel vagy visszautasítással kell meghiúsulnia.

A DGX hoston külön ellenőrizd a figyelő címeket:

```bash
ss -lntp | grep -E ':(443|8000|8001|8100)\b'
```

A `8000`, `8001` és `8100` sorok local address értéke `127.0.0.1` legyen.
Ellenőrizd a böngészőben a Pro/Enterprise és a tiltott Free felhasználói utat,
a kijelentkezést, valamint azt is, hogy közvetlen alkalmazás-URL-ről nincs
belépés login nélkül.

## Üzemeltetési megjegyzések

- Az `APPLICATIONS_JSON` jelenleg mindkét appot Pro és Enterprise csoportnak
  engedélyezi; ezt adminisztrátori döntés alapján módosíthatod.
- A Dashboard továbbra is egy közös, single-user munkatér: a proxy hozzáférést
  szabályoz, de nem teremt alkalmazáson belüli tenant-szeparációt.
- Mentsd rendszeresen a `pgdata`, `redisdata` és `caddy-data` Docker volume-ot.
- Tervezett frissítéskor előbb explicit `docker compose pull`, majd
  `docker compose up -d` fusson; ne használj automatikus, ellenőrizetlen image
  frissítést.
