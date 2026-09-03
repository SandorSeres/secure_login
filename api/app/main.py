# app/main.py
from fastapi import FastAPI, Request, Depends, status, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response as StarletteResponse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session as SASession
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from urllib.parse import quote, unquote

from .config import settings
from .models import AccountLevel, Base, engine, SessionLocal, User
from .schemas import RegisterIn, ResetPasswordIn
from .security import (
    hash_password,
    verify_password,
    create_session_value,
    invalidate_session_value,
    verify_session_value,
    issue_csrf,
    verify_csrf,
)
from .authorization import (
    applications_for_user,
    is_admin,
    require_application_access,
    user_groups,
)
from .rate_limit import limiter, is_locked, record_failed_login, reset_fail
from .deps import get_db, current_user
from .token_store import create_reset_token, verify_reset_token, invalidate_reset_token
from .email_utils import send_reset_email

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Statikus fájlok
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Jinja2 sablonok
templates = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)

# Adatbázis init
Base.metadata.create_all(bind=engine)


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
        secure=settings.COOKIE_SECURE,
        httponly=True,
        samesite="Strict",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def set_csrf_cookie(resp: StarletteResponse, value: str, max_age: int):
    resp.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=value,
        max_age=max_age,
        secure=settings.COOKIE_SECURE,
        httponly=False,       # formokhoz kell
        samesite="Strict",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def render_template(name: str, **context):
    template = templates.get_template(name)
    return HTMLResponse(template.render(**context))


def csrf_is_valid(form_token: str | None, cookie_token: str | None) -> bool:
    return bool(
        form_token
        and cookie_token
        and form_token == cookie_token
        and verify_csrf(form_token)
    )


@app.get("/", response_class=HTMLResponse)
async def get_login(request: Request):
    csrf_token = issue_csrf()
    flash_msg = request.cookies.get("flash")
    if flash_msg:
        flash_msg = unquote(flash_msg)

    html = templates.get_template("login.html").render(
        title="Auth",
        csrf_token=csrf_token,
        message=flash_msg,
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
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # validálás
    try:
        data = RegisterIn(
            username=(form.get("username") or "").strip(),
            email=(form.get("email") or "").strip(),
            password=form.get("password") or "",
        )
    except ValidationError:
        msg = "A jelszó nem elég erős. Minimum 10 karakter, kis- és nagybetű, szám és speciális jel szükséges."
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", quote(msg), max_age=6, path="/")
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

    # mentés
    u = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        account_level="Free",
    )
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            "flash",
            quote("A felhasználónév vagy e-mail már foglalt."),
            max_age=5,
            path="/",
        )
        return resp

    msg = "Sikeres regisztráció, jelentkezz be!"
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", quote(msg), max_age=6, path="/")
    return resp


@app.post("/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, db: SASession = Depends(get_db)):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
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
    user.touch()
    db.commit()
    session_val = create_session_value(user.username)
    resp = RedirectResponse("/apps", status_code=303)
    set_cookie(resp, settings.SESSION_COOKIE_NAME, session_val, 6 * 60 * 60)  # 6 óra

    # új CSRF a következő formokra
    new_csrf = issue_csrf()
    set_csrf_cookie(resp, new_csrf, 3600)
    return resp


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: User = Depends(current_user)):
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("profile.html").render(
        title="Profil",
        username=user.username,
        csrf_token=csrf_token,
        account_level=getattr(user, "account_level", "Free").value,
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.get("/apps", response_class=HTMLResponse)
async def applications(request: Request, user: User = Depends(current_user)):
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("apps.html").render(
        title="Alkalmazások",
        username=user.username,
        account_level=user.account_level.value,
        applications=applications_for_user(user),
        csrf_token=csrf_token,
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.get("/launch/{application_id}")
async def launch_application(
    application_id: str,
    user: User = Depends(current_user),
):
    application = require_application_access(user, application_id)
    return RedirectResponse(str(application.url), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/auth/verify")
async def verify_application_access(
    application_id: str,
    user: User = Depends(current_user),
):
    require_application_access(user, application_id)
    return StarletteResponse(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "X-Authenticated-User": user.username,
            "X-Authenticated-Email": user.email,
            "X-Authenticated-Groups": ",".join(sorted(user_groups(user))),
        },
    )


@app.post("/auth/logout")
async def logout(request: Request):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    resp = RedirectResponse("/", status_code=303)
    session_value = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_value:
        invalidate_session_value(session_value)
    resp.delete_cookie(
        settings.SESSION_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )
    new_csrf = issue_csrf()
    set_csrf_cookie(resp, new_csrf, 3600)
    return resp


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form(request: Request):
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("forgot_password.html").render(
        title="Elfelejtett jelszó",
        csrf_token=csrf_token,
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@limiter.limit("1/minute")
@app.post("/forgot-password")
async def forgot_password(request: Request, db: SASession = Depends(get_db)):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    email = (form.get("email") or "").strip()
    user = db.query(User).filter(User.email == email).first()

    resp = RedirectResponse("/", status_code=303)
    if not user:
        resp.set_cookie("flash", quote("Ha létezik fiók, küldtünk e-mailt."), max_age=5, path="/")
        return resp

    token = create_reset_token(user.username)
    _ = send_reset_email(user.email, token)
    resp.set_cookie("flash", quote("Ha létezik fiók, küldtünk e-mailt."), max_age=5, path="/")
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
        token=token,
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, db: SASession = Depends(get_db)):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    token = (form.get("token") or "").strip()
    new_password = form.get("password") or ""

    username = verify_reset_token(token)
    if not username:
        return PlainTextResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        data = ResetPasswordIn(password=new_password)
    except ValidationError:
        resp = RedirectResponse(f"/reset-password?token={quote(token)}", status_code=303)
        resp.set_cookie("flash", quote("A jelszó nem elég erős."), max_age=5, path="/")
        return resp

    user = db.query(User).filter(User.username == username).first()
    if not user:
        return PlainTextResponse("Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)

    user.password_hash = hash_password(data.password)
    db.commit()

    invalidate_reset_token(token)

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("flash", quote("A jelszó frissítve. Jelentkezz be az új jelszóval!"), max_age=5, path="/")
    return resp


@app.post("/admin/set_level")
async def set_level(
    request: Request,
    username: str = Form(),
    level: AccountLevel = Form(),
    administrator: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not is_admin(administrator):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    target_user = db.query(User).filter(User.username == username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    target_user.account_level = level
    db.commit()
    return {"status": "ok", "new_level": level.value}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(user: User = Depends(current_user)):
    html = templates.get_template("dashboard.html").render(
        title="Dashboard",
        username=user.username,
    )
    return HTMLResponse(html)


@app.get("/health")
async def health():
    return {"status": "ok"}
