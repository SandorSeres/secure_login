# Új kutatás indítása a DGX Sparkon

Ez az oldal a `scripts/start-new-research.sh` indítót írja le. Egy új
kutatás mindig két dolog: egy önálló, Git-verziózott **Research Project** a
`/Data/research/<azonosito>` alatt, és hozzá tartozó **Docker Compose példány** a
`/Data/research/.evidence-research-harness/instances/<azonosito>` alatt. A projektek
csak a két Docker image-et és a helyi LLM-szolgáltatást használják közösen.

A kézi, lépésről lépésre útmutató: `/Data/containers/new_research.md`.
A script ugyanazt a menetet automatizálja.

## Gyors kezdés

```bash
cd /Data/containers
cp scripts/research-goal-template.md ~/kutatas/cel.md      # töltsd ki a célt
scripts/start-new-research.sh --goal ~/kutatas/cel.md --port 8207
```

Port nélkül az indító az első szabad portot választja a `8201-8299`, majd
`10100-10199` sávokbol. Azonosító nélkül a cél címéből képz kisbetus,
kotojeles azonosítot; foglalt esetben idobelyeges utotagot kap.

Mit csinál végig:

1. image-ek ellenorzese, szukseg eseten epites a forrasbol
2. `project init` a runtime image-bol → önálló Git repository, `main` branch
3. compose pldány telepítése: a belso Codex sandbox `danger-full-access` (a
   konténer gondoskodik azarol), web-search gateway és kulso kulcsfájlok
4. `docker compose up -d` + health polling a Workspace végponton
5. az elso evidencia-gyujto `job create --source-mode web` letrehozása és `QUEUED`
   allapotba állítása

Vegleges riportkent kiírja a projekt, a pldány, a Workspace URL-t, az elso azonositojat
es a kezelési parancsot.

## Kapcsolok

```text
--goal FILE.md        A kutatás célját leíró markdown fájl (kötelező)
--id AZONOSITO        Projektazonosító [a-z0-9][a-z0-9_-]*
--port SZAM|auto      Workspace host port (auto = első szabad)
--job-title CIM       Az első kutatási kör címe (alap: projekt címe)
--no-job              Csak projekt és Workspace, kutatási kör nélkül
--no-start            Telepítés, de konténereket nem indítja
--dry-run             Csak terv: semmit nem ír, nem generál, nem indít
--secure-login        Bekötés a Secure Login alkalmazkapuba (lent)
--activate            --secure-login + a Caddy újralétrehozása
--bind-only           Csak bekötés meglévő példányhoz (cél.md nélkül)
--restart-only        Meglévő példány újraindítása (--id kötelező)
--start-only          Meglévő példány elindítása (--id kötelező)
--status              Meglévő példány compose állapota (--id kötelező)
```

Környezeti felülírások: `RESEARCH_ROOT`, `INSTALLER_ROOT`, `RH_FRAMEWORK_SOURCE`,
`RH_GOAL_PARSER`, `IMAGE_PREFIX`, `IMAGE_TAG`, `RH_REBUILD=auto|0|1`,
`RH_CODEX_PROVIDER_BASE_URL`, `RH_CODEX_MODEL`, `RH_APPROVED_BY`, `RH_JOB_RUNNER`,
`RH_UI_PORT`, `RH_GATEWAY_NETWORK`, `RH_DOMAIN_SKILLS_DIR`,
`RH_DOMAIN_SKILLS_ALLOWLIST`, `SECURE_LOGIN_DIR`, `RH_GATEWAY_HOST`.

## A cél markdown formátuma

A szöveg lehet magyar vagy bármilyen másik nyelv; a fejezetcímeket a script
magyarul és angolul is felismeri.

```markdown
# A kutatás teljes címe
KUTATASI KERDES: Egyetlen, tartós fő kutatási kérdés?

## Célok
- Első kutatási cél

## Escopus
- Ami a kutatásba beletartozik

## Mit NEM tartalmaz a kutatás
- Ami explicit módon ki van zárva

## Döntési kritériumok
- Milyen feltétel mellett fogadod el az eredményt?

## Háttér
Szabad szöveg: kontextus, kiinduló állapot. Ez kerül az első kör promptjába.
```

Hozzárendelés a harness parametrjaihoz: `# cím` → projekt címe (és az
azonosító forrása), `## Célok` → `--objective`, `## Escopus` → `--scope-in`,
`## Mit NEM` → `--scope-out`, `## Döntési kritériumok` → `--decision-criterion`,
a szabad szöveg az első kutatási kör promptja. Fejezetek nélkül a `Célok:` jelző
utáni listák célok, a `NEM`/kivétel jelző utániak scope-out, minden más scope-in.

Több azonos kategóriájú fejezet tartalma összevonódik — a `research.yaml` nem
tárol szakaszneveket.

## Secure Login bekötés

Az új kutatás a `https://research.code.hu/apps/<azonosito>/` címen jelenik meg a
Secure Login alkalmazkapuban. A Secure Login az `application_targets` táblából
generálja a Caddy konfigurációt — a `.env` `PORTALS_JSON` listája már nem
használatos.

```bash
# indítás közben
scripts/start-new-research.sh --goal ~/kutatas/cel.md --port 8207 --secure-login --activate

# meglévő példány utólag, cél.md nélkül
scripts/start-new-research.sh --id <azonosito> --bind-only            # csak generál
scripts/start-new-research.sh --id <azonosito> --bind-only --activate # Caddy újratöltés
```

Az `--activate` csak a Caddy-t hozzza létre újra; az api, a db és a redis
érintetlen. Hozzáférést rendelhetsz a felhasználóhoz:

```bash
cd /Data/containers/secure-login && ./admin.sh   # 5) Alkalmazás hozzárendelése
```

Ellenőrzés, még jog nélkül is (a `401` a helyes válasz):

```bash
curl -sk --resolve research.code.hu:443:127.0.0.1 \
  https://research.code.hu/apps/<azonosito>/
```

Ha a Workspace nincs a gateway hálón, a Caddy nem éri a konténer nevét: a
példány `.env` fájljában a `RH_GATEWAY_NETWORK` a `evidence-research_gateway`
legyen.

## A példány kezelése

```bash
RESEARCH_ROOT/.evidence-research-harness/instances/<azonosito>/manage.sh status
... manage.sh start | stop | restart | logs workspace | logs mutation-worker
scripts/start-new-research.sh --id <azonosito> --restart-only
```

A Workspace alapértelmezés szerint csak a Spark loopback címén figyel. Ne tedd ki
közvetlenül a LAN-ra; távoli használathoz a Secure Login portált vagy SSH
tunnelt használd.

## Kutatási kör életciklusa

A projekt létrehozása nem kutatási kör. Egy módosító kör kötelező útja:

```text
REVIEW_REQUIRED → APPROVED → COMMITTED → MERGED
```

Az approval, a commit és a merge külön emberi döntés. Új módosító kört csak az
előző kör merge-e és a tiszta `main` után indíts. A read-only query kivétel:
`COMPLETED` állapotban fejeződik be, commit és merge nélkül.

## Hibakeresés

| Tünet | Teendő |
|---|---|
| `A <port> portot más szolgáltatás foglalja el` | Másik `--port`, vagy `--id <azonosito> --restart-only` |
| `A(z) <id> könyvtár nem üres és nem Research Project` | Adj meg másik `--id` értéket |
| `Nincs szabad Workspace port` | Add meg a `--port` kapcsolóval |
| Health 60 mp alatt nem igazolt | `manage.sh logs workspace` |
| A Workspace 401/üres a gateway mögött | `admin.sh` 5) hozzárendelés; `docker network inspect evidence-research_gateway` |
| A worker image-ből hiányzik a web-search MCP | `RH_REBUILD=1` az újrépítéshez |

## File-ok

```text
scripts/start-new-research.sh               Az indító (ebbe a repóban)
scripts/lib/research_goal_parse.py          A cél.md parser
scripts/research-goal-template.md           Kitölthető sablon
docs/STARTING_RESEARCH.md                   Ez az útmutató
/Data/containers/new_research.md            Kézi, lépésenkénti útmutató
/Data/research/<azonosito>                    A Research Project (Git)
/Data/research/.evidence-research-harness/instances/<azonosito>
    A példány .env (0600) és manage.sh állománya; nem része a Git repositorynak.
```
