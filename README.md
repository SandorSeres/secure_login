# 🔐 Secure Login Portal — FastAPI + Caddy + PostgreSQL

> **DGX Spark deployment:** This project can act as a group-gated access portal
> for internal applications such as AI Dashboard. See
> [`docs/DGX_SPARK_PORTAL.md`](docs/DGX_SPARK_PORTAL.md) for the production
> architecture and deployment instructions.

Ez a projekt egy **biztonságos bejelentkezési és regisztrációs rendszer**, amely modern webes stackre épül:

- [FastAPI](https://fastapi.tiangolo.com/) — backend API
- [Caddy](https://caddyserver.com/) — HTTPS reverse proxy production deploymenthez
- [PostgreSQL](https://www.postgresql.org/) — adatbázis
- [SQLAlchemy](https://www.sqlalchemy.org/) — ORM
- [Jinja2](https://jinja.palletsprojects.com/) — sablonmotor
- [Docker Compose](https://docs.docker.com/compose/) — konténerizált környezet

---

## 🧾 Fő funkciók

- ✅ Felhasználó regisztráció és bejelentkezés
- ✅ Biztonságos jelszókezelés (Argon2 hash)
- ✅ Session alapú authentikáció
- ✅ Inaktivitás alapú **session timeout**
- ✅ Konfigurálható felhasználói csoportok / fiókszintek
- ✅ Konfigurált, csoportalapú alkalmazásportál
- ✅ Password policy enforcement
- ✅ Alap rate limiting
- ✅ Alap XSS / CSRF védelem
- ✅ Kifinomult UI neon-mátrix stílusban ✨
- ✅ Jelszó visszaállítás e-mailben

---

## 🧍 Regisztráció folyamata

1. A felhasználó megadja a felhasználónevét, e-mail címét és jelszavát a `/register` oldalon.
2. A jelszó a backendben validálásra kerül:
   - hossza, kis/nagybetű, szám, speciális karakter.
3. A jelszót a rendszer **Argon2** segítségével hasheli.
4. Az új felhasználó a `GROUPS_JSON` bejegyzései közül az
   `default: true` jelölésű csoportba kerül.
5. Sikeres regisztráció után a rendszer visszairányít a login oldalra.

---

## 🧭 Alkalmazásportál és csoportos hozzáférés

Az `APPLICATIONS_JSON` konfiguráció minden alkalmazáshoz megad egy azonosítót,
megjelenített nevet, HTTPS URL-t és engedélyezett csoportokat. A belépett
felhasználó csak a saját, `GROUPS_JSON`-ban definiált csoportjához rendelt
alkalmazásokat látja a `/apps` oldalon. Minden `allowed_groups` értéknek
létező csoportazonosítóra kell hivatkoznia; hibás konfigurációval a portál
nem indul el.

Példa:

```dotenv
GROUPS_JSON=[{"id":"free","name":"Free","default":true},{"id":"pro","name":"Pro"},{"id":"enterprise","name":"Enterprise"}]
APPLICATIONS_JSON=[{"id":"ai-dashboard","name":"AI Dashboard","url":"https://dashboard.code.hu","allowed_groups":["pro","enterprise"],"enabled":true}]
```

Az első, ezt a funkciót tartalmazó image induláskor a korábbi PostgreSQL
`account_level_enum` oszlopot szöveges oszloppá alakítja. A művelethez az
adatbázis-felhasználónak `ALTER TABLE` jogosultság kell. Ezután egy új csoport
felvétele nem igényel image buildet. A `.env` módosítása után a konténert
újra kell létrehozni, mert a sima `restart` nem alkalmazza az új környezeti
változókat:

```bash
cd deploy
docker compose --env-file ../.env config --quiet
docker compose --env-file ../.env up -d --no-deps --force-recreate auth
```

Az auth szolgáltatás a validált csoportlistát induláskor az
`access_groups` adatbázis-táblába szinkronizálja. A `deploy/admin.sh` ebből a
táblából olvas, ezért a script futtatási könyvtárában nincs szükség `.env`
fájlra.

A portál UI-ja nem biztonsági határ: a reverse proxy minden alkalmazásdomain
kérésénél a belső `/auth/verify?application_id=<id>` végpontot hívja. Így a
közvetlen URL megnyitása sem kerüli meg a csoport-ellenőrzést. A teljes DGX
Spark konfigurációért lásd [`docs/DGX_SPARK_PORTAL.md`](docs/DGX_SPARK_PORTAL.md).

---

## 🔑 Bejelentkezés folyamata

1. A felhasználó bejelentkezik a `/` oldalon.
2. A backend ellenőrzi a megadott jelszót a hash ellen.
3. Siker esetén:
   - Session cookie kerül beállításra.
   - A `last_activity` mező frissül az aktuális időpontra.
   - A felhasználó átirányításra kerül az alkalmazásportálra.
4. Az alkalmazásportálon megjelenik:
   - bejelentkezett felhasználónév,
   - aktuális, konfigurált account szint,
   - egyéb státuszinformációk.

---

## 🔁 Elfelejtett jelszó és jelszó-visszaállítás

1. A felhasználó a `/forgot-password` oldalon megadja az e-mail címét.
2. Ha létezik ilyen fiók:
   - a rendszer jelszó-visszaállító tokent generál,
   - e-mailben elküldi a visszaállító linket.
3. A felhasználó a linkre kattintva a `/reset-password` oldalra jut.
4. Itt megadhat új jelszót, amely a validáció után bekerül az adatbázisba.
5. A folyamat végén automatikusan visszairányítjuk a login oldalra.

🛡️ Biztonság:
- a gomb elküldés után deaktiválódik (dupla kattintás védelem),
- a válasz mindig egységes („Ha létezik fiók…”),
- a token ideiglenesen tárolódik Redisben, automatikusan lejár.

---

## 🕒 Session timeout

A rendszer minden authentikált kérésnél:

- ellenőrzi a `last_activity` mezőt,
- ha az utolsó aktivitás óta eltelt idő meghaladja a beállított limitet (pl. 30 perc),
- a session érvénytelenné válik,
- a felhasználót kijelentkezteti,
- átirányítja a login oldalra.

👉 A timeout beállítása konfigurálható (`SESSION_TIMEOUT_MINUTES`).

---

## 🧰 Account szintek

Az account szinteket a `GROUPS_JSON` definiálja. A csoport `id` mezője stabil,
kisbetűs azonosító; a `name` a felületen megjelenő név. Pontosan egy
csoporton kell `default: true` értéket megadni.

---

## 🧠 Jelszókövetelmények

- minimum **10 karakter**
- legalább **1 kisbetű** (a–z)
- legalább **1 nagybetű** (A–Z)
- legalább **1 szám**
- legalább **1 speciális karakter** (pl. `!@#$%^&*`)

---

## 🧯 Biztonsági intézkedések

- **HTTPS** – automatikus Let's Encrypt támogatással (Caddy)
- **Session cookie**: `HttpOnly`, `Secure`, `SameSite=Strict`
- **CSRF token** minden formhoz
- **Rate limiting** brute-force támadás ellen
- **XSS védelem**: CSP header + Jinja escaping
- **Password hashing** Argon2-vel
- **Unicode normalizálás** jelszavaknál
- **Email reset flow** redis alapú token lejárattal

---

## ⏳ Inaktivitás kezelése

- minden kérés frissíti a `last_activity` mezőt,
- ha lejárt: session törlés és login redirect,
- timeout érték konfigurálható.

---

## 📦 Telepítés

```bash
git clone https://github.com/<YOUR_USERNAME>/secure_login.git
cd secure_login
cp .env.example .env
cd deploy
docker compose --env-file ../.env pull
docker compose --env-file ../.env up -d
```

👉 Részletes Sparkos üzemeltetési útmutató:
[`docs/DGX_SPARK_PORTAL.md`](docs/DGX_SPARK_PORTAL.md).

---

## 🐳 Image build és registry push

A `build_and_push.sh` a Secure Login auth image-et építi. A Caddy a hivatalos
`caddy` image-ként fut, ezért nem része a buildnek.

```bash
chmod +x build_and_push.sh
./build_and_push.sh --prefix sseres/secure-login --push
```

Helyi, az aktuális gép architektúrájára történő buildhez:

```bash
./build_and_push.sh --prefix localhost/secure-login --load
```

---

## 🧪 Tesztelés

- Regisztrálj egy új felhasználót
- Jelentkezz be
- Ellenőrizd a profil oldalon a státuszt
- Várj a timeout idejéig → automatikus kijelentkezés
- Ellenőrizd, hogy gyenge jelszóra hibát kapsz
- Használd a jelszó-visszaállítás funkciót (`/forgot-password`)
- Ellenőrizd az e-mailt és a reset link működését

---

## 🛡️ Fontos megjegyzések

- A jelszavak soha nem kerülnek tárolásra tiszta formában.
- Session lejárat után újra be kell jelentkezni.
- A rendszer könnyen kiterjeszthető 2FA / WebAuthn megoldásokkal.

---

## 🧑‍💻 Licenc

MIT License — Szabadon felhasználható és bővíthető.

---

## ✨ További fejlesztési ötletek

- 2FA támogatás (TOTP / SMS / e-mail)
- Admin dashboard account kezeléshez
- Login próbálkozások logolása / IP tiltás
- CAPTCHA brute-force elleni védelemhez
- További biztonsági header integrációk

---

Készítette: **San d'Or** 🚀
