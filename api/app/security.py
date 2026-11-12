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

HEXADECIMAL = "0123456789abcdef"

def escape_username(username: str) -> str:
    """Render a username as cookie-safe ASCII.

    Starlette encodes a Set-Cookie header as latin-1, so a username holding a
    character outside that range used to crash login. Every byte outside
    printable ASCII, and both the backslash and the field separator, becomes a
    \\xNN escape, which keeps the payload ASCII and fully reversible.
    """
    escaped = []
    for character in username:
        codepoint = ord(character)
        if character in "\\|" or codepoint < 0x20 or codepoint > 0x7E:
            escaped.append("".join("\\x%02x" % byte for byte in character.encode("utf-8")))
        else:
            escaped.append(character)
    return "".join(escaped)

def unescape_username(escaped: str) -> str:
    """Reverse escape_username; a malformed escape passes through unchanged."""
    raw = bytearray()
    index = 0
    length = len(escaped)
    while index < length:
        character = escaped[index]
        if (character == "\\" and escaped[index + 1:index + 2] == "x"
                and index + 4 <= length):
            hexidecimal = escaped[index + 2:index + 4]
            if len(hexidecimal) == 2 and all(d in HEXADECIMAL for d in hexidecimal):
                raw.append(int(hexidecimal, 16))
                index += 4
                continue
        raw.extend(character.encode("utf-8"))
        index += 1
    return raw.decode("utf-8", "replace")

def create_session_value(username: str, jti: Optional[str] = None) -> str:
    """Sign a (username, jti) pair. The jti is what makes a session revocable.

    The signed payload is ASCII-only, so the cookie holding it is a valid
    response header for any username a registration form accepted.
    """
    payload = f"{escape_username(username)}|{jti or new_jti()}"
    return _session_signer.sign(payload).decode()

def verify_session_value(value: str) -> Optional[tuple[str, str]]:
    """Return (username, jti) for a structurally valid, unexpired cookie."""
    try:
        raw = _session_signer.unsign(value, max_age=SESSION_TTL).decode()
        escaped, separator, jti = raw.partition("|")
        if not separator or not escaped or not jti:
            return None
        return unescape_username(escaped), jti
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

