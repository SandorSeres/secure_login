

# 🔐 Secure Login System — FastAPI + Nginx + PostgreSQL


Ez a projekt egy **biztonságos bejelentkezési és regisztrációs rendszer**, amely modern webes stackre épül:

- [FastAPI](https://fastapi.tiangolo.com/) — backend API
- [Nginx](https://nginx.org/) — reverse proxy
- [PostgreSQL](https://www.postgresql.org/) — adatbázis
- [SQLAlchemy](https://www.sqlalchemy.org/) — ORM
- [Jinja2](https://jinja.palletsprojects.com/) — sablonmotor
- [Docker Compose](https://docs.docker.com/compose/) — konténerizált környezet

## 🧾 Fő funkciók

- ✅ Felhasználó regisztráció és bejelentkezés
- ✅ Biztonságos jelszókezelés (bcrypt hash)
- ✅ Session alapú authentikáció
- ✅ Inaktivitás alapú **session timeout**
- ✅ Felhasználói fiók szintek (Free / Pro / Enterprise)
- ✅ Password policy enforcement
- ✅ Alap rate limiting
- ✅ Alap XSS / CSRF védelem
- ✅ Kifinomult UI neon-mátrix stílusban ✨

---

## 🧍 Regisztráció folyamata

1. A felhasználó megadja a felhasználónevét és jelszavát a `/auth/register` oldalon.
2. A jelszó a backendben validálásra kerül:
   - hossza, kis/nagybetű, szám, speciális karakter.
3. A jelszót a rendszer **bcrypt** segítségével titkosítja.
4. Az új felhasználó **alapértelmezett jogosultsága**: `Free`.
5. Sikeres regisztráció után a rendszer visszairányít a login oldalra.

---

## 🔑 Bejelentkezés folyamata

1. A felhasználó bejelentkezik a `/auth/login` oldalon.
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

## 🕒 Session timeout

A rendszer minden authentikált kérésnél:

- ellenőrzi a `last_activity` mezőt,
- ha az utolsó aktivitás óta eltelt idő meghaladja a beállított limitet (pl. 30 perc),
  - a session érvénytelenné válik,
  - a felhasználót kijelentkezteti,
  - átirányítja a login oldalra.
- ha a session érvényes,
  - frissíti a `last_activity` értéket az aktuális időre.

👉 A timeout beállítása konfigurálható (`SESSION_TIMEOUT_MINUTES`).

---

## 🧰 Account szintek

Minden új user alapértelmezés szerint `Free` szintet kap.  
Admin vagy más API segítségével az account `plan` mező átállítható például:

- `Free` – alapfunkciók
- `Pro` – bővített szolgáltatások
- `Enterprise` – korlátlan hozzáférés

Az aktuális account szint megjelenik a profil oldalon sikeres login után.

---

## 🧠 Jelszókövetelmények

A backend és frontend összehangoltan ellenőrzi a jelszavakat.  
Követelmények:

- minimum **10 karakter**
- legalább **1 kisbetű** (a–z)
- legalább **1 nagybetű** (A–Z)
- legalább **1 szám**
- legalább **1 speciális karakter** (pl. `!@#$%^&*`)

**Hibás jelszó esetén** a felhasználó hibaüzenetet kap, a jelszó pedig nem kerül elmentésre.

---

## 🧯 Biztonsági intézkedések

- **HTTPS** – a rendszer Let's Encrypt tanúsítványt is támogat (Nginx certbot integráció).
- **Session cookie**:
  - `HttpOnly`
  - `Secure` (HTTPS esetén)
  - `SameSite=Strict`
- **CSRF token** minden formhoz.
- **Rate limiting** az auth végpontokon brute-force támadás ellen.
- **XSS elleni védelem**: CSP header + Jinja escaping.
- **Password hashing** bcrypt-tel.
- **Unicode normalizálás** jelszavaknál.

---

## ⏳ Inaktivitás kezelése

- Minden kéréskor a `current_user` dependency frissíti a `last_activity` mezőt.
- Ha az utolsó aktivitás ideje lejárt:
  - a session törlődik,
  - a felhasználó login oldalra kerül.
- A timeout értéke konfigurálható:
  ```python
  SESSION_TIMEOUT_MINUTES = 30

📦 Telepítés

git clone https://github.com/<YOUR_USERNAME>/secure_login.git
cd secure_login
cp .env.example .env
docker-compose up -d --build

👉 Alapértelmezett port: http://localhost:80
🧪 Tesztelés

    Regisztrálj egy új felhasználót

    Jelentkezz be

    Ellenőrizd a profil oldalon a státuszt

    Várj a timeout idejéig → automatikus kijelentkezés

    Ellenőrizd, hogy gyenge jelszóra hibát kapsz

    Állítsd át az account szintet admin eszközzel (pl. SQL, API)

🛡️ Fontos megjegyzések

    A jelszavak soha nem kerülnek tárolásra tiszta formában.

    Session lejárat után újra be kell jelentkezni.

    Az account szint logikát később bővíthetjük jogosultsági szintekkel (RBAC / ACL).

    A rendszer könnyen kiterjeszthető 2FA / WebAuthn megoldásokkal is.

🧑‍💻 Licenc

MIT License — Szabadon felhasználható és bővíthető.
✨ További fejlesztési ötletek

    2FA támogatás (TOTP / SMS / e-mail)

    Jelszó visszaállítás e-mailben

    Admin dashboard account kezeléshez

    Login próbálkozások logolása / IP tiltás

    CAPTCHA a brute-force elleni védelemhez

Készítette: San d'Or 🚀


A(z) Galaxy eszközömről küldve


