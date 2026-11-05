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
from .managed_portals import ManagedPortal, portal_by_id
from .rem_provisioning import router as rem_provisioning_router
from .models import Base, Portal, Role, User, UserPortalAccess, engine
from .database_migrations import (
    ensure_user_role_foreign_key,
    migrate_legacy_access_schema,
    sync_portals,
    sync_roles,
)
from .schemas import RegisterIn, ResetPasswordIn
from .security import (
    hash_password,
    verify_password,
    create_session_value,
    invalidate_session_value,
    issue_csrf,
    verify_csrf,
)
from .authorization import (
    assigned_portal,
    is_admin,
    portals_for_user,
    require_portal_access,
    user_role_name,
    user_roles,
)
from .rate_limit import limiter, is_locked, record_failed_login, reset_fail
from .deps import get_db, current_user
from .token_store import create_reset_token, verify_reset_token, invalidate_reset_token
from .email_utils import send_reset_email

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(rem_provisioning_router)

# Statikus fájlok
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Jinja2 sablonok
templates = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)

# Adatbázis init
migrate_legacy_access_schema(engine)
Base.metadata.create_all(bind=engine)
sync_roles(engine, Role.__table__, User.__table__, settings.ROLES_JSON)
ensure_user_role_foreign_key(engine)
sync_portals(engine, Portal.__table__, settings.PORTALS_JSON)
# Never auto-migrate a production access table. Fail closed if opt-in is premature.
if settings.ENABLE_MULTI_PORTAL:
    from .multi_portal_migration import assert_schema_ready
    assert_schema_ready(engine)


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
        httponly=False,  # formokhoz kell
        samesite="Strict",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


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
        msg = "Érvénytelen felhasználónév, e-mail-cím vagy jelszó."
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("flash", quote(msg), max_age=6, path="/")
        return resp

    # duplikált felhasználónév vagy e-mail
    exists = (
        db.query(User)
        .filter((User.username == data.username) | (User.email == data.email))
        .first()
    )
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
        role_id=settings.default_role.id,
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
    resp = RedirectResponse("/portals", status_code=303)
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
        role_name=user_role_name(user),
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.get("/portals", response_class=HTMLResponse)
async def portals(
    request: Request,
    user: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    csrf_token = request.cookies.get(settings.CSRF_COOKIE_NAME) or issue_csrf()
    html = templates.get_template("portals.html").render(
        title="Portálok",
        username=user.username,
        role_name=user_role_name(user),
        portals=portals_for_user(user, db),
        csrf_token=csrf_token,
    )
    resp = HTMLResponse(html)
    set_csrf_cookie(resp, csrf_token, 3600)
    return resp


@app.get("/apps")
async def legacy_apps_redirect(user: User = Depends(current_user)):
    return RedirectResponse("/portals", status_code=status.HTTP_308_PERMANENT_REDIRECT)


@app.get("/portal/{portal_id}")
async def launch_portal(
    portal_id: str,
    user: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    portal = require_portal_access(user, portal_id, db)
    return RedirectResponse(str(portal.url), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/auth/verify")
async def verify_portal_access(
    portal_id: str,
    user: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    portal = require_portal_access(user, portal_id, db)
    return StarletteResponse(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "X-Authenticated-User": user.username,
            "X-Authenticated-Email": user.email,
            "X-Authenticated-Role": ",".join(sorted(user_roles(user))),
            "X-Authenticated-Portal": portal.id,
        },
    )


@app.get("/auth/resolve")
async def resolve_portal_access(
    portal_id: str | None = None,
    user: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    portal = assigned_portal(user, db, portal_id)
    return StarletteResponse(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "X-Authenticated-User": user.username,
            "X-Authenticated-Email": user.email,
            "X-Authenticated-Role": ",".join(sorted(user_roles(user))),
            "X-Authenticated-Portal": portal.id,
            "X-Authenticated-Upstream": portal.upstream,
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


@app.post("/forgot-password")
@limiter.limit("1/minute")
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
        resp.set_cookie(
            "flash", quote("Ha létezik fiók, küldtünk e-mailt."), max_age=5, path="/"
        )
        return resp

    token = create_reset_token(user.username)
    _ = send_reset_email(user.email, token)
    resp.set_cookie(
        "flash", quote("Ha létezik fiók, küldtünk e-mailt."), max_age=5, path="/"
    )
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
        return PlainTextResponse(
            "Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED
        )

    try:
        data = ResetPasswordIn(password=new_password)
    except ValidationError:
        resp = RedirectResponse(
            f"/reset-password?token={quote(token)}", status_code=303
        )
        resp.set_cookie("flash", quote("A jelszó nem elég erős."), max_age=5, path="/")
        return resp

    user = db.query(User).filter(User.username == username).first()
    if not user:
        return PlainTextResponse(
            "Unauthorized", status_code=status.HTTP_401_UNAUTHORIZED
        )

    user.password_hash = hash_password(data.password)
    db.commit()

    invalidate_reset_token(token)

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "flash",
        quote("A jelszó frissítve. Jelentkezz be az új jelszóval!"),
        max_age=5,
        path="/",
    )
    return resp


@app.post("/admin/set_role")
async def set_role(
    request: Request,
    username: str = Form(),
    role_id: str = Form(),
    administrator: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not is_admin(administrator):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )

    role = settings.role_by_id(role_id)
    if role is None:
        raise HTTPException(status_code=422, detail="Invalid role")

    target_user = db.query(User).filter(User.username == username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    target_user.role_id = role.id
    db.commit()
    return {"status": "ok", "role": role.name}


@app.post("/admin/set_portal")
async def set_portal(
    request: Request,
    username: str = Form(),
    portal_id: str = Form(default=""),
    administrator: User = Depends(current_user),
    db: SASession = Depends(get_db),
):
    form = await request.form()
    csrf_token = form.get("csrf_token")
    cookie_csrf = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not csrf_is_valid(csrf_token, cookie_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not is_admin(administrator):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )

    target_user = db.query(User).filter(User.username == username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if settings.ENABLE_MULTI_PORTAL:
        raise HTTPException(status_code=409,
                            detail="Use grant_portal/revoke_portal in multi-portal mode")
    normalized_portal_id = portal_id.strip().casefold()
    grants = (db.query(UserPortalAccess)
              .filter(UserPortalAccess.user_id == target_user.id).all())
    if len(grants) > 1:
        raise HTTPException(status_code=409, detail="Ambiguous portal grants")
    existing_access = grants[0] if grants else None

    if not normalized_portal_id:
        if existing_access:
            db.delete(existing_access)
            db.commit()
        return {"status": "ok", "portal": None}

    portal = portal_by_id(normalized_portal_id, db)
    if portal is None or not portal.enabled:
        raise HTTPException(status_code=422, detail="Invalid or disabled portal")

    current_owner = (db.query(UserPortalAccess)
                     .filter(UserPortalAccess.portal_id == portal.id).first())
    if current_owner and current_owner.user_id != target_user.id:
        raise HTTPException(status_code=409, detail="Portal already assigned")
    if existing_access:
        existing_access.portal_id = portal.id
    else:
        db.add(UserPortalAccess(user_id=target_user.id, portal_id=portal.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Portal already assigned")
    return {"status": "ok", "portal": portal.name}


@app.post("/admin/grant_portal")
async def grant_portal(request: Request, username: str = Form(), portal_id: str = Form(),
                       administrator: User = Depends(current_user),
                       db: SASession = Depends(get_db)):
    if not settings.ENABLE_MULTI_PORTAL:
        raise HTTPException(status_code=404, detail="Multi-portal mode disabled")
    form = await request.form()
    if not csrf_is_valid(form.get("csrf_token"), request.cookies.get(settings.CSRF_COOKIE_NAME)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not is_admin(administrator):
        raise HTTPException(status_code=403, detail="Admin access required")
    target = db.query(User).filter(User.username == username).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    portal = portal_by_id(portal_id, db)
    if not portal or not portal.enabled:
        raise HTTPException(status_code=422, detail="Invalid or disabled portal")
    grant = db.get(UserPortalAccess, (target.id, portal.id))
    if grant is not None:
        return {"status": "ok", "portal": portal.id}
    db.add(UserPortalAccess(user_id=target.id, portal_id=portal.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Grant conflict")
    return {"status": "ok", "portal": portal.id}


@app.post("/admin/revoke_portal")
async def revoke_portal(request: Request, username: str = Form(), portal_id: str = Form(),
                        administrator: User = Depends(current_user),
                        db: SASession = Depends(get_db)):
    if not settings.ENABLE_MULTI_PORTAL:
        raise HTTPException(status_code=404, detail="Multi-portal mode disabled")
    form = await request.form()
    if not csrf_is_valid(form.get("csrf_token"), request.cookies.get(settings.CSRF_COOKIE_NAME)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not is_admin(administrator):
        raise HTTPException(status_code=403, detail="Admin access required")
    target = db.query(User).filter(User.username == username).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    normalized = portal_id.strip().casefold()
    grant = db.get(UserPortalAccess, (target.id, normalized))
    if grant is not None:
        db.delete(grant)
        db.commit()
    return {"status": "ok", "portal": normalized}


@app.get("/health")
async def health():
    return {"status": "ok"}
