# Open WebUI as a Secure Login application

Standalone stack: `/Data/containers/openwebui/compose.yaml`
(`docker compose up -d` in that folder). The host port `127.0.0.1:8080` is a
local debug port only; Caddy reaches the container over the named Docker
network `openwebui_net`, never through `127.0.0.1`.

## Register with `admin.sh`

Menu **4** — Új alkalmazás felvétele:

```text
Új alkalmazásazonosító: openwebui
Megjelenített név:      Open WebUI
Nyilvános URL:          https://openwebui.code.hu
Belső cél-URL:          http://openwebui:8080
Docker-hálózat:         openwebui_net
```

A `Nyilvános URL` a saját hostnév. A közös `https://research.code.hu/apps/openwebui`
cím is felvesző, de arra ez az alkalmazás nem való — lentebb leírva, miért.

Menu **5** then grants `openwebui` to each user who may open it.

Activating the generated config recreates the gateway. A Caddy-only re-deploy
of the generated routes:

```bash
docker compose -f docker-compose.yaml -f compose.app-gateway.yaml \
  up -d --no-deps --force-recreate caddy
```

`api` is recreated too whenever the API source changed (a prefixed redeem
route lives there, see the caveat below):

```bash
docker compose build api
docker compose -f docker-compose.yaml -f compose.app-gateway.yaml \
  up -d --no-deps --force-recreate api
```

## First start and the public URL

The admin account and the app URL come from `/Data/containers/openwebui/.env`:

```bash
WEBUI_ADMIN_EMAIL=admin@code.hu
WEBUI_ADMIN_PASSWORD=<first-start admin password>
WEBUI_PUBLIC_URL=https://openwebui.code.hu
```

`WEBUI_PUBLIC_URL` becomes the container's `WEBUI_URL`, which Open WebUI uses
to build its own links and its WebSocket URL. Left empty, the container
default wins and the UI asks for `/ws` on the wrong address, so set it before
the first `docker compose up -d` — and change it whenever the application's
public address changes.

## Why this application needs its own hostname

The gateway strips `/apps/openwebui` before the upstream, so Open WebUI sees
its own root and its routes work. Its page, however, is a prebuilt SvelteKit
bundle that writes every URL root-absolute: `/_app/immutable/...`,
`/static/...`, `/api/...`, `/ws/...`, and `base: ""` in its bootstrap. The
build takes no base-path setting — `WEBUI_URL` only rewrites links and the
WebSocket address the *backend* generates, not the bundle's asset URLs.

So the browser is handed ~92 root-level paths. They leave the application
prefix, land on the host root, and are authorized there as `ai-dashboard`,
whose cookie the user does not have. Every asset answers `404` or `401`, the
bundle never starts, and the dark splash screen is all that stays. The page
itself and the API paths under the prefix work fine, which is why the failure
looks so arbitrary.

Nothing on the gateway side fixes this honestly. A route that rewrote
`/static/*`, `/_app/*` and `/api/*` into `/apps/openwebui/...` would steal
those names from AI-Dashboard, the root application, and break it instead.
See the rule in `docs/APP_GATEWAY.md`: an application that cannot be told its
own mount point gets a hostname of its own.

On its own hostname the application root and the host root are the same thing,
so every root-absolute URL is already correct and nothing has to be rewritten.
That is the whole trick.

## Putting it on its own hostname

1. Add a DNS A record for the chosen name (here `openwebui.code.hu`) pointing
   at the same machine, **before** registering it. Caddy obtains a certificate
   per hostname, and it can only do that once the name resolves here. Check
   with `openssl s_client -connect 127.0.0.1:443 -servername openwebui.code.hu`
   if the public address is unreachable from this box.
2. Menu **8** — Alkalmazás URL módosítása. `Alkalmazásazonosító: openwebui`,
   `Nyilvános URL: https://openwebui.code.hu`. The upstream, the Docker network
   and every grant stay as they are. A mixed-case or trailing-slash form is
   folded to the canonical lowercase address; a path, port or query is refused,
   because a site is its application's root.
3. Recreate the gateway with the generated overlay:

   ```bash
   docker compose -f docker-compose.yaml -f compose.app-gateway.yaml \
     up -d --no-deps --force-recreate caddy
   ```

   The generator now writes one file per hostname into
   `caddy/app-gateway-generated/`, always named `app-<hostname>.caddy`, and
   removes the file of a hostname an application has left. Never name such a
   file `<hostname>.caddy`: Caddy expands a file named after the identity host
   as a placeholder source across that file.
4. Set `WEBUI_PUBLIC_URL=https://openwebui.code.hu` in the app `.env` and
   recreate the app container, so its generated links and its WebSocket URL
   name the same host the browser is on.

Grants, sessions and the cookie logic need no change. A second site block is
the same contract with a different `Host`: `authorize` still decides by the
registry row that names this host, and the app cookie stays
`__Host-secure-login-app-openwebui`, scoped to the login session that minted
it.

Verify from the machine itself, since NAT hairpinning makes the public address
unreachable locally:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  --connect-to openwebui.code.hu:443:127.0.0.1:443 https://openwebui.code.hu/
# 401 without an application cookie, 200 after launching from the profile page
```

## Login, and why single sign-on is not a config flag

`WEBUI_AUTH=true` with `ENABLE_SIGNUP=false` means Open WebUI authenticates its
own accounts. The site administrator is `admin@code.hu`, seeded on first start
from `WEBUI_ADMIN_EMAIL`/`WEBUI_ADMIN_PASSWORD` and stored as a bcrypt hash in
`/app/backend/data/webui.db`. It is a separate identity from the Secure Login
`sseres@code.hu` account: the gateway never forwards a password, and it
deliberately strips cookies before an upstream, so no Secure Login cookie can
arrive there either.

So when the Open WebUI login form answers *"The email or password provided is
incorrect"*, check which of the two actually happened:

```bash
# the account really works, when asked directly (expected: 200)
docker exec openwebui python3 -c "
import urllib.request, json
print(urllib.request.urlopen(urllib.request.Request(
  'http://127.0.0.1:8080/api/v1/auths/signin',
  json.dumps({'email':'admin@code.hu','password':'<from .env>'}).encode(),
  {'Content-Type':'application/json'})).getcode())"

# and through the gateway without an application cookie (expected: 401, and the
# UI reports that as a wrong password)
curl -s -X POST -H 'Content-Type: application/json'   --connect-to openwebui.code.hu:443:127.0.0.1:443   -d '{"email":"admin@code.hu","password":"..."}'   https://openwebui.code.hu/api/v1/auths/signin
```

If the first answers 200, the password is fine and the login simply never
carried an application cookie — use **Megnyitás** on the profile page, which
redeems a ticket and lands on this host with the cookie set.

### What real SSO would need

The obvious recipe is `WEBUI_AUTH=false` with
`WEBUI_AUTH_TRUSTED_EMAIL_HEADER`. It does not work with this gateway yet, and
the reason is worth writing down so nobody re-derives it: Caddy cannot carry a
`forward_auth` response header into the proxied request. Tested against the
running 2.11.9 image — `forward_auth` has no `copy_response_headers`, and the
`{http.response.source.header.*}` / `{http.handlers.forward_auth.response.header.*}`
placeholders do not exist in its binary, so a configured `request_header` leaves
them expanded literally, and the header the upstream sees stays whatever the
client sent. An identity header would therefore be forgeable by anyone who can
reach the backend.

The missing piece is an identity assertion the application can verify itself:
Secure Login signs a short-lived, application-audience value, hands it to the
browser, and the application exchanges it by calling *back* to Secure Login —
the application needs no secret, and a replayed or forged value fails
signature, audience and age at once. That endpoint does not exist yet; it needs
to be built in this project before `WEBUI_AUTH` can be turned off. Until then,
Open WebUI keeps authenticating its own users and Secure Login keeps gating the
site: two logins, one for the door and one for the room.
