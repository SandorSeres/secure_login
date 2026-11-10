import secrets, time
from datetime import timedelta, datetime
from typing import Optional
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
from passlib.hash import argon2
from fastapi import Request, HTTPException, status
from .config import settings
from .session_store import new_jti

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

def create_session_value(username: str, jti: Optional[str] = None) -> str:
    """Sign a (username, jti) pair. The jti is what makes a session revocable."""
    payload = f"{username}|{jti or new_jti()}"
    return _session_signer.sign(payload).decode()

def verify_session_value(value: str) -> Optional[tuple[str, str]]:
    """Return (username, jti) for a structurally valid, unexpired cookie."""
    try:
        raw = _session_signer.unsign(value, max_age=SESSION_TTL).decode()
        username, jti = raw.split("|", 1)
        if not username or not jti:
            return None
        return username, jti
    except (BadSignature, SignatureExpired):
        return None

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

