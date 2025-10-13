# 🔐 Secure Login System — FastAPI + Nginx + PostgreSQL

Ez a projekt egy **biztonságos bejelentkezési és regisztrációs rendszer**, amely modern webes stackre épül:

- [FastAPI](https://fastapi.tiangolo.com/) — backend API
- [Nginx](https://nginx.org/) — reverse proxy
- [PostgreSQL](https://www.postgresql.org/) — adatbázis
- [SQLAlchemy](https://www.sqlalchemy.org/) — ORM
- [Jinja2](https://jinja.palletsprojects.com/) — sablonmotor
- [Docker Compose](https://docs.docker.com/compose/) — konténerizált környezet

---

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
- ✅ Jelszó visszaállítás e-mailben

---

## 🧍 Regisztráció folyamata

1. A felhasználó megadja a felhasználónevét, e-mail címét és jelszavát a `/register` oldalon.
2. A jelszó a backendben validálásra kerül:
   - hossza, kis/nagybetű, szám, speciális karakter.
3. A jelszót a rendszer **bcrypt** segítségével titkosítja.
4. Az új felhasználó **alapértelmezett jogosultsága**: `Free`.
5. Sikeres regisztráció után a rendszer visszairányít a login oldalra.

---

## 🔑 Bejelentkezés folyamata

1. A felhasználó bejelentkezik a `/` oldalon.
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
- ha az utolsó aktivitás óta eltelt idő meghaladja a beállított limitet (pl. 30 perc),
- a session érvénytelenné válik,
- a felhasználót kijelentkezteti,
- átirányítja a login oldalra.

👉 A timeout beállítása konfigurálható (`SESSION_TIMEOUT_MINUTES`).

---

## 🧰 Account szintek

- `Free` – alapfunkciók
- `Pro` – bővített szolgáltatások
- `Enterprise` – korlátlan hozzáférés

---

## 🧠 Jelszókövetelmények

- minimum **10 karakter**
- legalább **1 kisbetű** (a–z)
- legalább **1 nagybetű** (A–Z)
- legalább **1 szám**
- legalább **1 speciális karakter** (pl. `!@#$%^&*`)

---

## 🧯 Biztonsági intézkedések

- **HTTPS** – Let's Encrypt támogatással (Nginx certbot integráció)
- **Session cookie**: `HttpOnly`, `Secure`, `SameSite=Strict`
- **CSRF token** minden formhoz
- **Rate limiting** brute-force támadás ellen
- **XSS védelem**: CSP header + Jinja escaping
- **Password hashing** bcrypt-tel
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
docker-compose up -d --build
```

👉 Alapértelmezett port: http://localhost:80

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
