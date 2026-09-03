import json
import secrets
from datetime import timedelta
from typing import Optional
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
from passlib.hash import argon2
from fastapi import Request, HTTPException, status
from .config import settings
from .token_store import create_session, invalidate_session, verify_session

# Password hashing
def hash_password(pw: str) -> str:
    return argon2.using(rounds=2, memory_cost=102400, parallelism=8).hash(pw)

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return argon2.verify(pw, hashed)
    except Exception:
        return False

# Signed session (httpOnly cookie)
_session_signer = TimestampSigner(settings.SECRET_KEY)
CSRF_TTL = 60 * 60  # 1 hour
SESSION_TTL = 60 * 60 * 6  # 6 hours

def create_session_value(username: str) -> str:
    session_id = secrets.token_urlsafe(32)
    create_session(session_id, username, timedelta(seconds=SESSION_TTL))
    payload = json.dumps({"session_id": session_id, "username": username}, separators=(",", ":"))
    return _session_signer.sign(payload).decode()

def verify_session_value(value: str) -> Optional[str]:
    try:
        raw = _session_signer.unsign(value, max_age=SESSION_TTL).decode()
        payload = json.loads(raw)
        username = payload["username"]
        session_id = payload["session_id"]
        if not isinstance(username, str) or not isinstance(session_id, str):
            return None
        return username if verify_session(session_id, username) else None
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def invalidate_session_value(value: str) -> None:
    try:
        raw = _session_signer.unsign(value, max_age=SESSION_TTL).decode()
        session_id = json.loads(raw)["session_id"]
        if isinstance(session_id, str):
            invalidate_session(session_id)
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return

# CSRF token (double-submit cookie + hidden form field)
_csrf_signer = TimestampSigner(settings.CSRF_KEY)

def issue_csrf() -> str:
    token = secrets.token_urlsafe(32)
    return _csrf_sign(token)

def _csrf_sign(token: str) -> str:
    return _csrf_signer.sign(token).decode()

def verify_csrf(signed: str) -> bool:
    try:
        _csrf_signer.unsign(signed, max_age=CSRF_TTL)
        return True
    except (BadSignature, SignatureExpired):
        return False

def require_csrf(request: Request):
    field = request.form().get("csrf_token")  # handled in route via await
    cookie = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not field or not cookie or field != cookie or not verify_csrf(field):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
