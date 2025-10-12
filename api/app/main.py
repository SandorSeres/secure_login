from fastapi import FastAPI, Request, Depends, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response as StarletteResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session as SASession
from pydantic import ValidationError  # 👈 EZ HIÁNYZOTT
import secrets
from urllib.parse import quote, unquote

from .config import settings
from .models import Base, engine, SessionLocal, User
from .schemas import RegisterIn
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


# Rate limit middleware
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
        httponly=False,
        samesite="Strict",
        path="/"
    )


def render_template(name: str, **context):
    template = templates.get_template(name)
    return HTMLResponse(template.render(**context))


@app.get("/", response_class=HTMLResponse)
async def get_login(request: Request):
    csrf_token = issue_csrf()
    flash_msg = request.cookies.get("flash")  # hibaüzenet cookie-ból
    if flash_msg:
        flash_msg = unquote(flash_msg)   # visszafejtjük az URL-encoded üzenetet

    html = templates.get_template("login.html").render(
        title="Auth",
        csrf_token=csrf_token,
        message=flash_msg
    )

    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)

    # ha volt flash üzenet, töröljük, hogy ne maradjon bent
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    try:
        data = RegisterIn(
            username=form.get("username", "").strip(),
            password=form.get("password", "")
        )
    except ValidationError:
        # gyenge jelszó → flash üzenet + redirect
        msg = "A jelszó nem elég erős."   # ékezetes!
        encoded_msg = quote(msg)         # URL-encoding → ASCII kompatibilis
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", encoded_msg, max_age=5, path="/")
        return resp


    # új felhasználó mentése
    hashed_pw = hash_password(data.password)
    u = User(
        username=data.username,
        password_hash=hash_password(data.password),
        account_level="Free"   # 👈 itt kerül be automatikusan
    )
    db.add(u)
    db.commit()


    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", "Sikeres regisztráció, jelentkezz be!", max_age=5, path="/")
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

    rem = is_locked(username, ip)
    if rem:
        return PlainTextResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        record_failed_login(username, ip)
        return PlainTextResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    reset_fail(username, ip)
    session_val = create_session_value(user.username)
    resp = RedirectResponse("/profile", status_code=303)
    set_cookie(resp, settings.SESSION_COOKIE_NAME, session_val, 6 * 60 * 60)

    new_csrf = issue_csrf()
    set_csrf_cookie(resp, new_csrf, 3600)
    return resp


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: User = Depends(current_user)):
    csrf = issue_csrf()
    html = templates.get_template("profile.html").render(
        title="Profil",
        username=user.username,
        csrf_token=csrf,
        account_level=user.account_level  # 👈 átadjuk a sablonnak
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf, 3600)
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

@app.post("/admin/set_level")
async def set_level(username: str, level: str, db: SASession = Depends(get_db)):
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

