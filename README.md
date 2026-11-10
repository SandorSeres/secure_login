# 🔐 Secure Login System — FastAPI + Caddy + PostgreSQL

Ez a projekt egy **biztonságos bejelentkezési és regisztrációs rendszer**, amely modern webes stackre épül:

- [FastAPI](https://fastapi.tiangolo.com/) — backend API
- [Caddy](https://caddyserver.com/) — reverse proxy (automatikus HTTPS)
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
- ✅ Felhasználói fiók szintek (Free / Pro / Enterprise)
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
3. A jelszót a rendszer **Argon2** segítségével titkosítja.
4. Az új felhasználó **alapértelmezett jogosultsága**: `Free`.
5. Sikeres regisztráció után a rendszer visszairányít a login oldalra.

---

## 🔑 Bejelentkezés folyamata

1. A felhasználó bejelentkezik a `/` oldalon (a regisztráció ugyanitt van).
2. A backend ellenőrzi a megadott jelszót a hash ellen.
3. Siker esetén:
   - Session cookie kerül beállításra.
   - A `last_activity` mező frissül az aktuális időpontra.
   - A felhasználó átirányításra kerül a `profile` oldalra.
4. A `profile` oldalon megjelenik:
   - bejelentkezett felhasználónév,
   - aktuális account szint (`Free` / `Pro` / `Enterprise`),
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
- ha az utolsó aktivitás óta eltelt idő meghaladja a beállított limitet
  (alapértelmezés szerint 10 perc), a session érvénytelenné válik,
- a felhasználót kijelentkezteti és a login oldalra irányítja.

A session **a szerveren is nyilvántartott** (Redis): a kilépés, a jelszó
megváltoztatása és a szintmódosítás a már kiállított sütit azonnal
visszavonja — a süti önmagában nem tart egy munkamenetet érvényben.

A kilépés mindig **csak azt az egy sessiont** ér véget, amelyikből
kattintották, így egy másik eszköz bejelentkezve marad. A jelszó
megváltoztatása és a szintmódosítás a felhasználó **minden** sessionjét
megszünteti. Mindegyik sessionhez tartozik egy scope azonosító is: az
 ezzel a belépéssel kiállított alkalmazás sütik ezen a kötődnek, ezért
 egy alkalmazás süti sosem élheti túl a loginját, ami létrehozta.

👉 Inaktivitási limit: `SESSION_TIMEOUT_MINUTES` (perc).
👉 A süti abszolút élettartama: `SESSION_COOKIE_TTL_SECONDS` (mp).
👉 Az alkalmazás gateway süti élettartama: `APP_SESSION_TTL_SECONDS` (mp).

---

## 🧰 Account szintek

- `Free` – alapfunkciók
- `Pro` – bővített szolgáltatások
- `Enterprise` – korlátlan hozzáférés

A szintet a `POST /admin/set_level` végpont módosíthatja, de csak hiteles
adminisztrátornak: a kérés CSRF-mezőt és érvényes sessiont kér, a szint a
fenti három egyike lehet, és a változás az érintett felhasználó minden élő
sessionjét visszavonja. Adminisztrátornak a `ADMIN_USERNAMES` (pontos
felhasználónevek, vesszővel elválasztva) vagy az `Enterprise` szint számít.

---

## 🧠 Jelszókövetelmények

- minimum **10 karakter**
- legalább **1 kisbetű** (a–z)
- legalább **1 nagybetű** (A–Z)
- legalább **1 szám**
- legalább **1 speciális karakter** (pl. `!@#$%^&*`)

---

## 🧯 Biztonsági intézkedések

- **HTTPS** – automatikus Let's Encrypt tanúsítvány (Caddy)
- **Session cookie**: `HttpOnly`, `Secure`, `SameSite=Strict`
- **CSRF token** minden formhoz
- **Rate limiting** brute-force támadás ellen
- **XSS védelem**: CSP header + Jinja escaping
- **Password hashing** Argon2-vel
- **Unicode normalizálás** jelszavaknál
- **Email reset flow** redis alapú token lejárattal

---

## ⏳ Inaktivitás kezelése

- az aktivitást a rendszer legfeljebb percenként írja vissza,
- ha lejárt: a session törlődik és login redirect történik,
- az érték konfigurálható (`SESSION_TIMEOUT_MINUTES`).

---

## 📦 Telepítés

```bash
git clone <YOUR_REPO_URL> secure-login
cd secure-login
# Hozd létre a helyi, nem versionált .env fájlt: SECRET_KEY, CSRF_KEY,
# PG_USER, PG_PASSWORD, PG_DB, CADDY_DOMAIN, ACME_EMAIL, SMTP_*
# Titkos kulcsokhoz: openssl rand -hex 32
docker compose up -d --build
```

👉 A publikus portot a `CADDY_DOMAIN` DNS bejegyzés és a Caddy automatic
HTTPS állítja be (80 → 443 redirect).

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

---

## 🐳 Futtatás és tesztelés Dockerben

```bash
docker compose up -d --build            # db + redis + api + caddy
docker compose ps
docker compose logs --no-color api caddy
```

Unit és regression tesztek (elparzeló SQLite, nem nyúl az éles adatbázishoz):

```bash
docker build -t secure-login-api:test ./api
docker run --rm -v "$PWD:/work" -w /work \
  -e PYTHONPATH=/work/api -e SECRET_KEY=test-only -e CSRF_KEY=test-only-csrf \
  -e DATABASE_URL=sqlite:// secure-login-api:test \
  python -m unittest discover -s tests -p 'test_*.py' -v
```

A `./api` image a forrást az `/app/app` csomagba másolja, ezért a tesztek a
`PYTHONPATH=/work/api` beállítással az alairásodatol betöltött kódot
vizsgalják. Az éles `./.env` fájl a contenerben nincs jelen, igy a teszt
kornyezet nem ervenytelenitheti az appot.
