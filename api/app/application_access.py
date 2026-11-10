"""Application-specific, single-use cross-host login and authorization.

Only the static Caddy app-site configuration supplies the application ID.
Each forwarded request rechecks the existing user_application_access grant.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from redis import Redis, RedisError
from sqlalchemy.orm import Session

from .config import settings
from .deps import current_user, get_db
from .models import ApplicationTarget, User, UserApplicationAccess

router = APIRouter()
_TICKET_TTL = 60
_SESSION_TTL = 6 * 60 * 60
_COOKIE = "__Host-secure-login-app"
_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="secure-login-app-gateway-v1")


def _has_grant(db: Session, user_id: int, app_id: str) -> bool:
    return db.query(UserApplicationAccess).filter(
        UserApplicationAccess.user_id == user_id,
        UserApplicationAccess.application_id == app_id,
    ).first() is not None


def _cache() -> Redis:
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _ticket_key(ticket: str) -> str:
    return "secure-login:app-ticket:" + hashlib.sha256(ticket.encode("ascii")).hexdigest()


def _site_matches(request: Request, target: ApplicationTarget) -> bool:
    # No trust in request-supplied Forwarded/X-Forwarded-Host.
    host = request.headers.get("host", "").lower()
    return target.public_url == "https://" + host


@router.get("/applications/{app_id}/launch")
def launch(
    app_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    target = db.get(ApplicationTarget, app_id)
    if target is None or not _has_grant(db, user.id, app_id):
        raise HTTPException(status_code=403, detail="Application not permitted")
    ticket = secrets.token_urlsafe(32)
    try:
        with _cache() as cache:
            cache.setex(
                _ticket_key(ticket), _TICKET_TTL,
                json.dumps({"user_id": user.id, "app_id": app_id}),
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
def redeem(
    request: Request,
    ticket: str,
    db: Session = Depends(get_db),
):
    app_id = request.headers.get("x-gateway-app", "")
    target = db.get(ApplicationTarget, app_id)
    if target is None or not _site_matches(request, target):
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
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=403, detail="Invalid application ticket")
    if db.get(User, user_id) is None or not _has_grant(db, user_id, app_id):
        raise HTTPException(status_code=403, detail="Application not permitted")
    value = _signer.dumps({"user_id": user_id, "app_id": app_id})
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        _COOKIE, value, max_age=_SESSION_TTL,
        secure=True, httponly=True, samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/_secure-login/authorize")
def authorize(request: Request, db: Session = Depends(get_db)):
    app_id = request.headers.get("x-gateway-app", "")
    target = db.get(ApplicationTarget, app_id)
    if target is None or not _site_matches(request, target):
        raise HTTPException(status_code=403, detail="Application not permitted")
    value = request.cookies.get(_COOKIE)
    if not value:
        raise HTTPException(status_code=401, detail="Application authentication required")
    try:
        payload = _signer.loads(value, max_age=_SESSION_TTL)
        if payload["app_id"] != app_id:
            raise ValueError("Wrong application")
        user_id = int(payload["user_id"])
    except (BadSignature, SignatureExpired, ValueError, TypeError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid application session")
    if db.get(User, user_id) is None or not _has_grant(db, user_id, app_id):
        raise HTTPException(status_code=403, detail="Application not permitted")

    # Browser writes and WebSocket handshakes must originate on the app site.
    method = request.headers.get("x-forwarded-method", "").upper()
    upgrade = request.headers.get("x-original-upgrade", "").lower()
    if method in {"POST", "PUT", "PATCH", "DELETE"} or upgrade == "websocket":
        if request.headers.get("origin") != target.public_url:
            raise HTTPException(status_code=403, detail="Application origin not permitted")
    return Response(status_code=204)
