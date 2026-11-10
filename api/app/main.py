from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .token_store import create_reset_token, verify_reset_token, invalidate_reset_token
from .email_utils import send_reset_email
from starlette.responses import Response as StarletteResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session as SASession
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from urllib.parse import quote, unquote

from .config import settings
from .models import Base, engine, SessionLocal, User
from .schemas import RegisterIn, ResetPasswordIn, SetLevelIn
from .security import (
    hash_password,
    verify_password,
    create_session_value,
    verify_session_value,
    issue_csrf
)
from .rate_limit import limiter, is_locked, record_failed_login, reset_fail
from .deps import get_db, current_user
from .profile_apps import applications_for_user
from .session_store import (
    new_jti,
    new_scope_id,
    register_session,
    revoke_all_sessions,
    revoke_session,
    session_scope_id,
    list_active_session_jtis,
)
from .session_cookie import sweep_app_cookies
from .timestamps import utc_now

# Engedélyezett account szintek (a README szerinti három szint).
ACCOUNT_LEVELS = ("Free", "Pro", "Enterprise")

# Valós hash-re van szükség, ha a login egy nem létező felhasználónevet kap:
# így a jelszó-ellenőrzés mindkét ágon ugyanannyi ideig tart.
DUMMY_HASH = hash_password("Placeholder1!not-a-real-password")


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Additive, opt-in application gateway endpoints only.
from .application_access import router as application_access_router
app.include_router(application_access_router)

# Statikus fájlok
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Jinja2 sablonok
templates = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"])
)

# Adatbázis init
Base.metadata.create_all(bind=engine)


# Biztonsági fejlécek
@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def set_cookie(resp: StarletteResponse, name: str, value: str, max_age: int):
    resp.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        secure=settings.COOKIE_SECURE,
        httponly=True,
        samesite="Strict",
        path="/"
    )


def set_csrf_cookie(resp: StarletteResponse, value: str, max_age: int):
    resp.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=value,
        max_age=max_age,
        secure=settings.COOKIE_SECURE,
        httponly=False,  # formokba be kell tudni tenni renderkor
        samesite="Strict",
        path="/"
    )


def render_template(name: str, **context):
    template = templates.get_template(name)
    return HTMLResponse(template.render(**context))


def flash_redirect(message: str) -> RedirectResponse:
    """303 a loginra, URL-kódolt átvezető üzenettel (fejléc-biztos)."""
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", quote(message), max_age=6, path="/")
    return resp


def is_admin(user: User) -> bool:
    allowed = {name.strip() for name in settings.ADMIN_USERNAMES.split(",") if name.strip()}
    return user.username in allowed or getattr(user, "account_level", None) == "Enterprise"


@app.get("/", response_class=HTMLResponse)
async def get_login(request: Request):
    csrf_token = issue_csrf()
    flash_msg = request.cookies.get("flash")
    if flash_msg:
        flash_msg = unquote(flash_msg)  # URL-dekód

    html = templates.get_template("login.html").render(
        title="Auth",
        csrf_token=csrf_token,
        message=flash_msg
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)

    # egyszer használatos flash törlése
    if flash_msg:
        resp.delete_cookie("flash", path="/")

    return resp


@app.post("/auth/register")
@limiter.limit("5/minute")
async def register(request: Request, db: SASession = Depends(get_db)):
    form = await request.form()
    csrf = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf or not cookie_csrf or csrf != cookie_csrf:
        # CSRF hibát nem „szépítjük”
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # validálás (Pydantic: min. 10 hossz, kis/nagybetű, szám, speciális)
    try:
        data = RegisterIn(
            username=(form.get("username") or "").strip(),
            email=(form.get("email") or "").strip(),         
            password=form.get("password") or ""
        )
    except ValidationError:
        msg = "A jelszó nem elég erős. Minimum 10 karakter, kis- és nagybetű, szám és speciális jel szükséges."
        return flash_redirect(msg)

    # duplikált felhasználónév vagy e-mail
    exists = db.query(User).filter(
        (User.username == data.username) | (User.email == data.email)
    ).first()
    if exists:
        msg = "Ez a felhasználónév vagy e-mail már foglalt."
        return flash_redirect(msg)

    # mentés (alap szint: Free)
    u = User(
        username=data.username,
        email=data.email,                                   
        password_hash=hash_password(data.password),
        account_level="Free"
    )
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        msg = "Ez a felhasználónév vagy e-mail már foglalt."
    else:
        msg = "Sikeres regisztráció, jelentkezz be!"

    # A register válasza formátumában nem különbözik a login válaszától
    # (303 + flash), így nem használható fiók-felsorolásra. Érvényes
    # regisztráció után friss CSRF jár a következő bejelentkezéshez.
    if msg.startswith("Sikeres regisztráció"):
        resp = flash_redirect(msg)
        set_csrf_cookie(resp, issue_csrf(), 3600)
        return resp
    return flash_redirect(msg)


@app.post("/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, db: SASession = Depends(get_db)):
    form = await request.form()
    csrf = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf or not cookie_csrf or csrf != cookie_csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    ip = request.client.host if request.client else "0.0.0.0"

    # lockout ellenőrzés
    if is_locked(username, ip):
        msg = "Túl sok sikertelen próbálkozás. Próbáld meg később."
        return flash_redirect(msg)

    # A jelszó-ellenőrzést névtelen blokkra is futtatjuk, hogy a válasz
    # időigénye és tartalma ne árulja el, létezik-e a felhasználónév.
    user = db.query(User).filter(User.username == username).first()
    stored_hash = user.password_hash if user else DUMMY_HASH
    if not verify_password(password, stored_hash) or user is None:
        record_failed_login(username, ip)
        msg = "Hibás felhasználónév vagy jelszó."
        return flash_redirect(msg)

    # sikeres autentikáció
    reset_fail(username, ip)
    # A friss bejelentkezés új inaktivitási periódust indít.
    user.last_activity = utc_now()
    db.commit()
    # A süti csak az aláírt (felhasználónév, jti) párt tartalmazza; a jti a
    # Redisben kap helyet, így a kilépés/jelszócsere azonnal vonhatja vissza.
    jti = new_jti()
    # A scope köti össze a sessiont az ezzel a belépéssel kiállított
    # alkalmazásgateway sütikkel.
    register_session(user.username, jti, new_scope_id(),
                     settings.SESSION_COOKIE_TTL_SECONDS)
    session_val = create_session_value(user.username, jti)
    resp = RedirectResponse("/profile", status_code=303)
    set_cookie(resp, settings.SESSION_COOKIE_NAME, session_val,
               settings.SESSION_COOKIE_TTL_SECONDS)

    # új CSRF a következő formokra
    new_csrf = issue_csrf()
    set_csrf_cookie(resp, new_csrf, 3600)
    return resp


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: User = Depends(current_user), db: SASession = Depends(get_db)):
    # csrf = issue_csrf()
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("profile.html").render(
        title="Profil",
        username=user.username,
        csrf_token=csrf_token,
        account_level=getattr(user, "account_level", "Free"),
        applications=applications_for_user(db, user.id)
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.post("/auth/logout")
async def logout(request: Request, user: User = Depends(current_user)):
    form = await request.form()
    csrf = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf or not cookie_csrf or csrf != cookie_csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    resp = RedirectResponse("/", status_code=303)
    # A süti törlése önmagában nem elég: a session és a hozzá tartozó
    # alkalmazásgateway-sütik visszavonásra kerülnek a szerveren. Csak a
    # most használt session: egy gépről kilépni nem jelenthet máshol is
    # kijelentkezést, arra a jelszócsere és a szintmódosítás való.
    verified = verify_session_value(
        request.cookies.get(settings.SESSION_COOKIE_NAME, ""))
    if verified:
        revoke_session(verified[0], verified[1])
    resp.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    new_csrf = issue_csrf()
    set_csrf_cookie(resp, new_csrf, 3600)
    return resp



@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form(request: Request):
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("forgot_password.html").render(
        title="Elfelejtett jelszó",
        csrf_token=csrf_token
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@limiter.limit("1/minute")
@app.post("/forgot-password")
async def forgot_password(request: Request, db: SASession = Depends(get_db)):
    form = await request.form()
    csrf = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf or not cookie_csrf or csrf != cookie_csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    email = (form.get("email") or "").strip()
    # keresés:
    user = db.query(User).filter(User.email == email).first()
    # minden esetben ugyanaz a válasz kint
    resp = RedirectResponse("/", status_code=303)

    if not user:
        # opcionálisan egy rövid flash (mindig ugyanez a szöveg):
        resp.set_cookie("flash", "Ha létezik fiók, küldtünk e-mailt.", max_age=5, path="/")
        return resp  
    # token gyártás és elmentés Redisbe (már megvan nálad)
    token = create_reset_token(user.username)
    ok = send_reset_email(user.email, token)  # username = email-t használsz?
    # Küldéstől függetlenül ugyanúgy visszairányítunk:
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", "Ha létezik fiók, küldtünk e-mailt.", max_age=5, path="/")
    return resp


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str):
    username = verify_reset_token(token)
    if not username:
        return HTMLResponse("A token érvénytelen vagy lejárt.", status_code=400)

    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("reset_password.html").render(
        title="Új jelszó megadása",
        csrf_token=csrf_token,
        token=token
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, db: SASession = Depends(get_db)):
    form = await request.form()
    csrf = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf or not cookie_csrf or csrf != cookie_csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    token = (form.get("token") or "").strip()
    new_password = form.get("password") or ""

    # token -> felhasználónév
    username = verify_reset_token(token)  # ahogy eddig
    if not username:
        # általános hiba, ne áruljunk el részleteket
        return PlainTextResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    # JELSZÓ VALIDÁCIÓ (reset-specifikus séma)
    try:
        data = ResetPasswordIn(password=new_password)
    except ValidationError:
        # visszaterelés a formra: flash üzenet
        resp = RedirectResponse(f"/reset-password?token={quote(token)}", status_code=303)
        resp.set_cookie("flash", quote("A jelszó nem elég erős."), max_age=5, path="/")
        return resp

    # felhasználó és jelszó frissítés
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return PlainTextResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    user.password_hash = hash_password(data.password)
    db.commit()

    # Az új jelszóval minden korábbi session és alkalmazáshoz kiállított
    # gateway süti érvényét veszti.
    revoke_all_sessions(username)

    # token érvénytelenítése
    invalidate_reset_token(token)

    # vissza loginra
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", "A jelszó frissítve. Jelentkezz be az új jelszóval!", max_age=5, path="/")
    return resp


# Admin endpoint az account szint állítására. Csak hiteles admin írhatja át,
# a szint a három engedélyezett egyike lehet, és a szintváltás levenni az
# érintett felhasználó minden élő sessionjét.
@app.post("/admin/set_level")
@limiter.limit("30/hour")
async def set_level(
    request: Request,
    username: str = Form(...),
    level: str = Form(...),
    admin: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    form = await request.form()
    csrf = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf or not cookie_csrf or csrf != cookie_csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not is_admin(admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    try:
        data = SetLevelIn(username=username.strip(), level=level)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid username or level")

    user = db.query(User).filter(User.username == data.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.account_level == data.level:
        return {"status": "ok", "new_level": data.level, "sessions_revoked": 0}

    jtis = list_active_session_jtis(user.username)
    # Az admin a saját scopejét feladja a sweepnek, hogy az ne tiltsa le az
    # ő alkalmazás sütijét is: a szintváltás rá nem vonatkozik.
    verified = verify_session_value(
        request.cookies.get(settings.SESSION_COOKIE_NAME, ""))
    admin_scope = session_scope_id(admin.username, verified[1]) if verified else None
    user.account_level = data.level
    db.commit()
    # A szintváltás jogosultság-változás, ezért a célszemély minden sessionje
    # és a velük kiállított gateway süti azonnal érvényét veszti.
    revoke_all_sessions(user.username)
    # A session nélküli sütikről nem tudható, kitől származnak.
    sweep_app_cookies(None, keep=admin_scope)
    return {"status": "ok", "new_level": data.level, "sessions_revoked": len(jtis)}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(user: User = Depends(current_user)):
    return render_template("dashboard.html", title="Dashboard", username=user.username)

@app.get("/health")
async def health():
    return {"status": "ok"}

