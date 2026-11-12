"""Application-scoped tickets and authorization on a shared application hostname.

Only the static Caddy app-site configuration supplies the application ID.
Each forwarded request rechecks the existing user_application_access grant.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from redis import RedisError
from sqlalchemy.orm import Session

from .config import settings
from . import redis_client
from .deps import current_user, get_db
from .models import ApplicationTarget, User, UserApplicationAccess
from .security import verify_session_value
from .session_cookie import (
    UNSCOPED,
    app_cookie_name,
    cookie_record_key,
    is_app_cookie_revoked,
    scope_record_key,
)
from .session_store import session_scope_id

router = APIRouter()
_TICKET_TTL = 60
# Longest a cookie can be presented; the record must outlive it.
_RECORD_TTL = settings.APP_SESSION_TTL_SECONDS * 2
_SESSION_TTL = settings.APP_SESSION_TTL_SECONDS
_SCOPE_FORMAT = re.compile(r"[A-Za-z0-9_-]{16,}\Z")
_REDEEM_PATH = "/_secure-login/redeem"
_APP_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="secure-login-app-gateway-v1")


def _has_grant(db: Session, user_id: int, app_id: str) -> bool:
    return db.query(UserApplicationAccess).filter(
        UserApplicationAccess.user_id == user_id,
        UserApplicationAccess.application_id == app_id,
    ).first() is not None


def _cache():
    # One cache accessor for the module, so the sweep and the records written
    # here always address the same Redis.
    return redis_client.client()


def _ticket_key(ticket: str) -> str:
    return "secure-login:app-ticket:" + hashlib.sha256(ticket.encode("ascii")).hexdigest()


def _ticket_scope(payload) -> str:
    """Scope carried by a ticket, or the unscoped bucket when there is none.

    The scope has to travel inside the short-lived ticket that only this
    service writes: the Secure Login session cookie cannot be the carrier,
    because redeem runs on the application host, where Caddy strips cookies
    and SameSite=Strict keeps the session cookie out of the request. That also
    means an application upstream can never learn a revocation handle.

    A ticket without a usable scope is recorded as unscoped. It then rests on
    its signature alone, so it can only be denied by a sweep that knows no
    session, never by a logout of somebody else.
    """
    scope = str(payload.get("scope") or "")
    if not _SCOPE_FORMAT.fullmatch(scope):
        return UNSCOPED
    with _cache() as cache:
        if not cache.exists(scope_record_key(scope)):
            # A session that no longer exists must not mint a cookie that
            # lives on for the rest of its own Max-Age.
            return UNSCOPED
    return scope


def _bind_app_cookie(scope: str, app_id: str, cookie_id: str) -> None:
    """Record an issued app cookie so a revocation can find and deny it."""
    try:
        with _cache() as cache:
            cache.setex(cookie_record_key(scope, app_id, cookie_id), _RECORD_TTL, "1")
    except RedisError:
        raise HTTPException(status_code=503, detail="Application gateway unavailable")


def _host(request: Request) -> str:
    # No trust in request-supplied Forwarded/X-Forwarded-Host.
    return request.headers.get("host", "").lower()


def _public_host(target: ApplicationTarget) -> str:
    """The lowercase hostname a registry row advertises, or "" if unusable.

    A hostname is case-insensitive, so this has to be folded before it meets
    the folded Host header. host, not netloc: Host carries no userinfo, and a
    row containing one must never compare equal to a Host value.
    """
    try:
        return urlsplit(target.public_url).hostname.lower()
    except (AttributeError, ValueError):
        return ""


def _site_matches(request: Request, target: ApplicationTarget) -> bool:
    # No trust in request-supplied Forwarded/X-Forwarded-Host: this compares
    # the configured address with what the proxy itself received.
    host = _host(request)
    public_host = _public_host(target)
    if not public_host:
        return False
    public_path = urlsplit(target.public_url).path.rstrip("/")
    return (public_host == host and public_path == ""
            or public_host == host and public_path == "/apps/" + target.application_id)


def _request_app_by_address(request: Request, db: Session) -> str:
    """The application the proxy routed this request to, or "" for none.

    Redeem reaches the api through a site block, so the api itself decides
    which application a request belongs to. The address the proxy received is
    the only safe input: it is the proxy's routing decision, while the gateway
    header also travels on ordinary requests, where a client or an application
    backend can put anything in it.

    A site is a configured public address, so the registry is the whole
    description of the layout. The path is empty for a root site; for a site
    mounted under a prefix the proxy strips that prefix first, so the prefix
    has to be put back. An address no row describes resolves to nothing.
    """
    host = _host(request)
    path = request.url.path
    if path.endswith(_REDEEM_PATH):
        path = path[: -len(_REDEEM_PATH)]
    prefix = path.rstrip("/")
    if prefix and not prefix.startswith("/"):
        return ""
    candidates = [target.application_id
                  for target in db.query(ApplicationTarget).all()
                  if _public_host(target) == host
                  and urlsplit(target.public_url).path.rstrip("/") == prefix]
    # One row describes an address. More than one is a broken registry, and
    # guessing between two applications is not a safe tie-break.
    return candidates[0] if len(candidates) == 1 else ""


def _gateway_app(request: Request, db: Session) -> str:
    """Resolve the application a request belongs to, or "" for none.

    Two sources exist and they must never disagree. The gateway header names
    the application the static app-site config is proxying; Host and path are
    the routing decision the proxy actually made. Redeem normally arrives with
    no header, because a client reaches it on the application host and the
    cookie-less authorize subrequest that would stamp one only runs for
    proxied application traffic.

    The header is only believed where it agrees with that address, so it adds
    nothing a client could steer: naming some other application from this host
    resolves to nothing, and no cookie can be minted for it.
    """
    by_address = _request_app_by_address(request, db)
    named = request.headers.get("x-gateway-app", "")
    if not named:
        return by_address
    if not _APP_ID.fullmatch(named):
        return ""
    # The header is believed only where it agrees with the address the proxy
    # actually received. That is the whole defence against a client, or one
    # application's backend, naming a different application from this host.
    return named if named == by_address else ""


@router.get("/applications/{app_id}/launch")
def launch(
    app_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    target = db.get(ApplicationTarget, app_id)
    if target is None or not _has_grant(db, user.id, app_id):
        raise HTTPException(status_code=403, detail="Application not permitted")
    # The scope travels inside the short-lived ticket, never in a header or a
    # cookie an upstream could ever see (see _ticket_scope). Launch runs on the
    # identity host, so the session cookie is readable here, and the scope it
    # names is the handle that will end the cookie this ticket redeems into.
    scope = ""
    verified = verify_session_value(
        request.cookies.get(settings.SESSION_COOKIE_NAME, ""))
    if verified:
        scope = session_scope_id(user.username, verified[1]) or ""
    ticket = secrets.token_urlsafe(32)
    try:
        with _cache() as cache:
            cache.setex(
                _ticket_key(ticket), _TICKET_TTL,
                json.dumps({"user_id": user.id, "app_id": app_id, "scope": scope}),
            )
    except RedisError:
        raise HTTPException(status_code=503, detail="Application gateway unavailable")
    response = RedirectResponse(
        target.public_url + "/_secure-login/redeem?ticket=" + quote(ticket, safe=""),
        status_code=303,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/_secure-login/redeem")
@router.get("/apps/{app_id}/_secure-login/redeem")
def redeem(
    request: Request,
    ticket: str,
    db: Session = Depends(get_db),
):
    app_id = _gateway_app(request, db)
    target = db.get(ApplicationTarget, app_id) if app_id else None
    if target is None:
        # Nothing configured for this host and path. The ticket, the grant and
        # the signature still have to match an application, so no cookie can
        # be minted from a host the registry does not describe.
        raise HTTPException(status_code=403, detail="Invalid application gateway")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
        raise HTTPException(status_code=403, detail="Invalid application ticket")
    try:
        with _cache() as cache:
            raw = cache.getdel(_ticket_key(ticket))
    except RedisError:
        raise HTTPException(status_code=503, detail="Application gateway unavailable")
    if raw is None:
        raise HTTPException(status_code=403, detail="Invalid or used ticket")
    try:
        payload = json.loads(raw)
        if payload["app_id"] != app_id:
            raise ValueError("Ticket target mismatch")
        user_id = int(payload["user_id"])
        # An unusable scope never becomes someone else's sweep handle.
        scope = _ticket_scope(payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=403, detail="Invalid application ticket")
    if db.get(User, user_id) is None or not _has_grant(db, user_id, app_id):
        raise HTTPException(status_code=403, detail="Application not permitted")
    # Every issued cookie gets a server-side id. A revocation denies that id,
    # so the cookie stops working before the browser would ever drop it.
    cookie_id = secrets.token_urlsafe(16)
    value = _signer.dumps({
        "user_id": user_id, "app_id": app_id,
        "cookie_id": cookie_id, "scope": scope,
    })
    _bind_app_cookie(scope, app_id, cookie_id)
    response = RedirectResponse(
        "/" if _public_host(target) == _host(request)
        and urlsplit(target.public_url).path.rstrip("/") == ""
        else "/apps/" + app_id + "/", status_code=303)
    response.set_cookie(
        app_cookie_name(app_id), value, max_age=_SESSION_TTL,
        secure=True, httponly=True, samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/_secure-login/authorize")
def authorize(request: Request, db: Session = Depends(get_db)):
    app_id = request.headers.get("x-gateway-app", "")
    target = db.get(ApplicationTarget, app_id) if app_id else None
    if target is None or not _site_matches(request, target):
        raise HTTPException(status_code=403, detail="Application not permitted")
    value = request.cookies.get(app_cookie_name(app_id))
    if not value:
        raise HTTPException(status_code=401, detail="Application authentication required")
    try:
        payload = _signer.loads(value, max_age=_SESSION_TTL)
        if payload["app_id"] != app_id:
            raise ValueError("Wrong application")
        user_id = int(payload["user_id"])
    except (BadSignature, SignatureExpired, ValueError, TypeError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid application session")
    scope = str(payload.get("scope") or "")
    # Denied, or the login that issued it is gone: an application cookie can
    # never outlive the session that minted it.
    if is_app_cookie_revoked(scope, str(payload.get("cookie_id") or "")):
        raise HTTPException(status_code=401, detail="Application authentication required")
    if db.get(User, user_id) is None or not _has_grant(db, user_id, app_id):
        raise HTTPException(status_code=403, detail="Application not permitted")

    # Browser writes and WebSocket handshakes must originate on the app site.
    # A forward_auth always sets both headers, so a client-supplied value is
    # overwritten before it can steer this check. A missing header means the
    # request did not come through a known gateway configuration at all.
    method = request.headers.get("x-forwarded-method", "").upper()
    upgrade = request.headers.get("x-original-upgrade", "").lower()
    if "x-forwarded-method" not in request.headers:
        raise HTTPException(status_code=403, detail="Application gateway not trusted")
    if method in {"POST", "PUT", "PATCH", "DELETE"} or upgrade == "websocket":
        if request.headers.get("origin") != "https://" + _host(request):
            raise HTTPException(status_code=403, detail="Application origin not permitted")

    return Response(status_code=204)
