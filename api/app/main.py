from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from fastapi import Form
from .token_store import create_reset_token, verify_reset_token, invalidate_reset_token
from .email_utils import send_reset_email
from .security import hash_password
from urllib.parse import quote


from starlette.responses import Response as StarletteResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session as SASession
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from urllib.parse import quote, unquote

from .config import settings
from .models import Base, engine, SessionLocal, User
from .application_access import router as application_access_router, require_service  # gated IAM
from .schemas import RegisterIn, ResetPasswordIn
from .security import (
    hash_password,
    verify_password,
    create_session_value,
    verify_session_value,
    issue_csrf
)
from .rate_limit import limiter, is_locked, record_failed_login, reset_fail
from .deps import get_db, current_user

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Statikus fájlok
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Jinja2 sablonok
templates = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"])
)

# Adatbázis init
Base.metadata.create_all(bind=engine)
app.include_router(application_access_router)


# Rate limit middleware (dekorátorok intézik a limiteket, itt csak továbbengedünk)
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    response = await call_next(request)
    return response


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
        secure=True,
        httponly=True,
        samesite="Strict",
        path="/"
    )


def set_csrf_cookie(resp: StarletteResponse, value: str, max_age: int):
    resp.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=value,
        max_age=max_age,
        secure=True,
        httponly=False,  # formokba be kell tudni tenni renderkor
        samesite="Strict",
        path="/"
    )


def render_template(name: str, **context):
    template = templates.get_template(name)
    return HTMLResponse(template.render(**context))


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
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", quote(msg), max_age=6, path="/")  # URL-encode a fejléc miatt
        return resp

    # duplikált felhasználónév vagy e-mail
    exists = db.query(User).filter(
        (User.username == data.username) | (User.email == data.email)
    ).first()
    if exists:
        msg = "Ez a felhasználónév vagy e-mail már foglalt."
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", quote(msg), max_age=6, path="/")
        return resp

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
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", quote("A felhasználónév vagy e-mail már foglalt."), max_age=5, path="/")
        return resp

    msg = "Sikeres regisztráció, jelentkezz be!"
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", quote(msg), max_age=6, path="/")
    return resp


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
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", quote(msg), max_age=6, path="/")
        return resp

    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        record_failed_login(username, ip)
        msg = "Hibás felhasználónév vagy jelszó."
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", quote(msg), max_age=6, path="/")
        return resp

    # sikeres autentikáció
    reset_fail(username, ip)
    session_val = create_session_value(user.username)
    resp = RedirectResponse("/profile", status_code=303)
    set_cookie(resp, settings.SESSION_COOKIE_NAME, session_val, 6 * 60 * 60)  # 6 óra

    # új CSRF a következő formokra
    new_csrf = issue_csrf()
    set_csrf_cookie(resp, new_csrf, 3600)
    return resp


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: User = Depends(current_user)):
    # csrf = issue_csrf()
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("profile.html").render(
        title="Profil",
        username=user.username,
        csrf_token=csrf_token,
        account_level=getattr(user, "account_level", "Free")
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.post("/auth/logout")
async def logout(request: Request):
    form = await request.form()
    csrf = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf or not cookie_csrf or csrf != cookie_csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    resp = RedirectResponse("/", status_code=303)
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
        print("Nincs fiók/email!")
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
        from urllib.parse import quote
        resp = RedirectResponse(f"/reset-password?token={quote(token)}", status_code=303)
        resp.set_cookie("flash", quote("A jelszó nem elég erős."), max_age=5, path="/")
        return resp

    # felhasználó és jelszó frissítés
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return PlainTextResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    user.password_hash = hash_password(data.password)
    db.commit()

    # token érvénytelenítése
    invalidate_reset_token(token)

    # vissza loginra
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", "A jelszó frissítve. Jelentkezz be az új jelszóval!", max_age=5, path="/")
    return resp


# Account szint módosítása csak belső, hitelesített szolgáltatás számára.
@app.post("/admin/set_level")
async def set_level(username: str, level: str, db: SASession = Depends(get_db), _: None = Depends(require_service)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.account_level = level
    db.commit()
    return {"status": "ok", "new_level": level}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(user: User = Depends(current_user)):
    html = templates.get_template("dashboard.html").render(
        title="Dashboard", username=user.username
    )
    return HTMLResponse(html)


@app.get("/health")
async def health():
    return {"status": "ok"}

