"""Server-side session records so sessions can actually be revoked.

The session cookie only carries a signed (username, jti) pair. Whether that
jti is still valid is decided here, so logout, a password change and an
administrative action can invalidate live cookies immediately.

Every session also owns a scope id. The application gateway records the cookies
it issues under that scope and keeps a copy of it inside the signed cookie.
While the scope record exists a scoped cookie is accepted; when it expires with
the session, or a revocation deletes it, the cookie stops working even if
someone copied it. So an application cookie can never outlive the login that
minted it, without the identity host ever reading a cookie it cannot see.

Key layout (Redis):
  secure-login:session:<username>:<jti>          issue time of the session
  secure-login:session-owner:<scope_id>          "<username>:<jti>" owning it
  secure-login:app-scope:<scope_id>              the gateway's liveness check

Each scope is a sibling of the session key, never below it: a pattern like
secure-login:session:* must only ever match sessions.
"""
from __future__ import annotations

import re
import secrets
import time
from typing import Optional

from .config import settings
from . import redis_client

_SESSION_TTL = settings.SESSION_COOKIE_TTL_SECONDS
# An application cookie can be presented for this long, so the record that
# binds it to a login has to be able to outlive the cookie itself.
_SCOPE_TTL = _SESSION_TTL + settings.APP_SESSION_TTL_SECONDS
_OWNER_FORMAT = re.compile(r"^[^\s:]+:[^\s:]+\Z")


def _cache():
    return redis_client.client()


# Imported at the end of this module: session_cookie reads the scope records
# defined here, so the import cycle has to be broken by order, not by name.
from .session_cookie import sweep_app_cookies  # noqa: E402


def new_jti() -> str:
    return secrets.token_urlsafe(32)


def new_scope_id() -> str:
    return secrets.token_urlsafe(32)


def session_key(username: str, jti: str) -> str:
    return f"secure-login:session:{username}:{jti}"


def scope_key(scope_id: str) -> str:
    """Who owns a scope, alive for at least as long as the session."""
    return f"secure-login:session-owner:{scope_id}"


def scope_record_key(scope_id: str) -> str:
    """The record the application gateway checks before accepting a cookie."""
    return f"secure-login:app-scope:{scope_id}"


def register_session(username: str, jti: str, scope_id: str,
                     ttl: int = _SESSION_TTL) -> None:
    """Store a session and the scope its application cookies are bound to.

    The session value carries the issue time so an active session and its
    cookie can expire together, without ever extending the cookie beyond its
    Max-Age. The binding record is written with the longest lifetime any
    cookie it can ever mint has, so a cookie issued right before the session
    idles out is still honoured until its own Max-Age.
    """
    with _cache() as cache:
        cache.setex(session_key(username, jti), ttl, str(int(time.time())))
        cache.setex(scope_key(scope_id), ttl, f"{username}:{jti}")
        cache.setex(scope_record_key(scope_id), _SCOPE_TTL, "1")


def is_session_valid(username: str, jti: str) -> bool:
    with _cache() as cache:
        return bool(cache.exists(session_key(username, jti)))


def touch_session(username: str, jti: str, ttl: int = _SESSION_TTL) -> None:
    """Keep the session alive without ever outliving the cookie.

    The binding record is deliberately not refreshed. Extending it would let a
    session that never idles out keep application cookies alive forever, which
    is exactly what an absolute cookie lifetime is meant to prevent.
    """
    key = session_key(username, jti)
    with _cache() as cache:
        issued = cache.get(key)
        if issued is None:
            return
        try:
            elapsed = int(time.time()) - int(issued)
        except (TypeError, ValueError):
            elapsed = 0
        remaining = max(ttl - max(elapsed, 0), 1)
        cache.expire(key, remaining)
        for scope in _owned_scopes(cache, username, jti):
            cache.expire(scope_key(scope), remaining)


def session_scope_id(username: str, jti: str) -> Optional[str]:
    """The live scope of a session, or None when it has no usable one."""
    with _cache() as cache:
        scopes = _owned_scopes(cache, username, jti)
    return scopes[0] if scopes else None


def revoke_session(username: str, jti: str) -> None:
    """Drop one session; the application cookies it issued go with it."""
    with _cache() as cache:
        scopes = _owned_scopes(cache, username, jti)
        cache.delete(session_key(username, jti))
        for scope in scopes:
            _drop_scope(cache, scope)
    for scope in scopes:
        sweep_app_cookies(scope)


def revoke_all_sessions(username: str) -> list[str]:
    """Delete every session of a user and end their application cookies.

    With no session on record there is nothing to identify the user by, so
    this reports no scope and leaves the whole-keyspace ending to the caller,
    which knows whether the action (a level change, for instance) is broad
    enough to warrant it.
    """
    prefix = f"secure-login:session:{username}:"
    scopes: list[str] = []
    with _cache() as cache:
        for key in cache.scan_iter(match=f"{prefix}*", count=200):
            username_and_jti = key[len(prefix):]
            if username_and_jti:
                scopes += _owned_scopes(cache, username, username_and_jti)
            cache.delete(key)
        for scope in list(scopes):
            _drop_scope(cache, scope)
    for scope in scopes:
        sweep_app_cookies(scope)
    return scopes


def list_active_session_jtis(username: str) -> list[str]:
    """Active jtis for a user, reported by the administrative endpoint."""
    prefix = f"secure-login:session:{username}:"
    with _cache() as cache:
        return [
            key[len(prefix):]
            for key in cache.scan_iter(match=f"{prefix}*", count=200)
            if key[len(prefix):]
        ]


def _drop_scope(cache, scope_id: str) -> None:
    """End a scope: the binding record goes, so every cookie on it stops."""
    cache.delete(scope_key(scope_id))
    cache.delete(scope_record_key(scope_id))


def _owned_scopes(cache, username: str, jti: str) -> list[str]:
    """Live scope ids owned by a session, by reverse key.

    The owner is the value, so a reverse index would need one scan; instead a
    session is looked up by the one key that can never be confused with a
    scope, and each candidate scope is checked for its recorded owner.
    """
    owner = f"{username}:{jti}"
    found = []
    for key in cache.scan_iter(match="secure-login:session-owner:*", count=500):
        value = cache.get(key)
        if isinstance(value, bytes):
            value = value.decode()
        if isinstance(value, str) and _OWNER_FORMAT.fullmatch(value) and value == owner:
            scope_id = key.rsplit(":", 1)[-1]
            if cache.exists(scope_record_key(scope_id)):
                found.append(scope_id)
    return found
