# Felhasználói regisztráció és token használat (TravellAI integráció)

Ez a leírás bemutatja, hogyan regisztrálhatsz, jelentkezhetsz be, és hogyan használhatod a rendszer által generált tokent (pl. QR-kód formájában) a TravellAI alkalmazásban.

## 1. Regisztráció folyamata

1. **Nyisd meg a webes bejelentkezési oldalt**
   - Cím: [https://<szervered>/](https://<szervered>/)

2. **Töltsd ki a regisztrációs űrlapot**
   - Felhasználónév
   - E-mail cím
   - Jelszó (minimum 10 karakter, kis- és nagybetű, szám, speciális karakter)

3. **Regisztráció után**
   - Sikeres regisztrációt követően automatikusan bejelentkezel, vagy visszairányít a bejelentkezési oldalra.

## 2. Token generálás és QR-kód

1. **Token generálása**
   - A rendszer minden felhasználónak egyedi tokent generál, amely meghatározza, milyen promptokat használhatsz a TravellAI rendszerben.
   - A token tartalmazhatja a felhasználó jogosultságait, prompt-listát, lejárati időt.

2. **QR-kód letöltése vagy megjelenítése**
   - A webes felületen lehetőség van a token QR-kód formájában történő letöltésére vagy megjelenítésére.
   - A QR-kód tartalmazza a tokent (és opcionálisan a backend URL-t).

3. **Android alkalmazás beállítása**
   - Az appban válaszd a QR-kód beolvasását.
   - Olvasd be a webes felületen kapott QR-kódot.
   - Az app automatikusan beállítja a hozzáférést a token alapján.

## 3. Jogosultság bővítés / új token igénylés

1. **E-mailes meghívó vagy jogosultság bővítés**
   - Ha a felhasználó e-mailben kap egy linket (pl. admin jóváhagyás vagy új prompt jogosultság), a linkre kattintva a rendszer új tokent generál.
   - Az új tokenhez új QR-kód tartozik, amelyet ismét be lehet olvasni az appban.

## 4. Biztonság és korlátozások

- Minden token csak a hozzá tartozó promptokhoz ad hozzáférést.
- A tokenek időkorlátosak lehetnek, vagy visszavonhatók (pl. új token generálásakor a régi érvénytelenedik).
- A konfigurált jogosultsági csoportod befolyásolhatja, hogy milyen alkalmazásokat és funkciókat használhatsz.

## 5. Példa folyamat

1. Regisztrálsz a weben → kapsz egy QR-kódot → beolvasod az appban → csak alap promptokat használhatsz.
2. Kapsz egy e-mailt új linkkel → új QR-kódot kapsz → beolvasod → bővülnek a promptjaid.

---

Ha elakadnál, keresd az adminisztrátort vagy a supportot!
