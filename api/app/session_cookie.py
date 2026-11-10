"""Application-gateway cookie names and their server-side revocation list.

Logout on the identity host cannot delete cookies that live on the shared
application host. Rather than wait for the browser to drop them, every issued
cookie carries an id that the authorize endpoint can deny-list, and the gateway
records the session that issued it so a revocation can find it again.

Key layout (Redis):
  secure-login:app-scope:<scope_id>                    session-backed scope
  secure-login:app-cookie:<scope>:<app_id>:<cookie_id> issued cookie record
  secure-login:app-cookie-revoked:<cookie_id>          deny-list entry

<scope> is the server-side id of the identity session that redeemed the ticket,
or UNSCOPED when no session was known at redeem time. A scoped cookie is also
denied by the expiry of its scope record, so it cannot outlive the login that
minted it. See session_store for the session records behind those scopes.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .config import settings
from . import redis_client

APP_COOKIE_PREFIX = "__Host-secure-login-app-"
UNSCOPED = "unscoped"
SCOPE_RECORD_PREFIX = "secure-login:app-scope:"
_COOKIE_RECORD_PREFIX = "secure-login:app-cookie:"
_REVOCATION_TTL = settings.APP_SESSION_TTL_SECONDS
_APP_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_SCOPE_ID = re.compile(r"[A-Za-z0-9_-]{16,}\Z")


def app_cookie_name(app_id: str) -> str:
    if not _APP_ID.fullmatch(app_id):
        raise ValueError("Invalid application ID")
    return APP_COOKIE_PREFIX + app_id


def revocation_key(cookie_id: str) -> str:
    """Deny-list entry for a cookie identifier (never a key name)."""
    return "secure-login:app-cookie-revoked:" + cookie_id


def cookie_record_key(scope: str, app_id: str, cookie_id: str) -> str:
    return f"{_COOKIE_RECORD_PREFIX}{scope}:{app_id}:{cookie_id}"


def scope_record_key(scope_id: str) -> str:
    """The record that ties an application cookie to a live session.

    Owned by session_store, which writes and expires it; this module only
    reads it and drops it during a revocation.
    """
    return SCOPE_RECORD_PREFIX + scope_id


def _cache() -> Any:
    return redis_client.client()


def sweep_app_cookies(scope: Optional[str], keep: Optional[str] = None) -> None:
    """End the application cookies that one identity session issued.

    Logout and a password change happen on the identity host, which cannot
    reach cookies that live on the shared application host, so the sweep works
    from the records the gateway wrote when it issued them. With a scope id
    only that session is affected, which is what an action for one user must
    do: its record goes away, so every cookie that carried it stops working,
    and the cookies recorded under it are denied explicitly so a copy cannot
    survive on its signature alone.

    With None, for an action on somebody who may be logged in anywhere and
    whose sessions cannot be named here, every scope that ever held a cookie
    is ended, the cookies issued without a session are denied, and so are the
    records left behind by a session whose binding record has already gone:
    their cookie can no longer be attributed to anyone, so none of them may
    keep working on its signature alone.

    With keep, the scopes recorded under it survive: an administrator ending
    one user's sessions must not sign other users out of their applications.
    """
    if scope is not None and not _SCOPE_ID.fullmatch(scope):
        # An unusable scope is a lost handle, never "nothing to revoke".
        scope = None
    if scope is not None:
        _end_scope(scope)
        return
    keep = {keep} if keep and _SCOPE_ID.fullmatch(keep) else set()
    for known in _scopes_with_cookies():
        if known not in keep:
            _end_scope(known)
    _deny_records(UNSCOPED)
    _deny_orphans()


def _end_scope(scope: str) -> None:
    with _cache() as cache:
        cache.delete(scope_record_key(scope))
    _deny_records(scope)


def _deny_records(scope: str) -> None:
    """Deny-list every cookie recorded under one scope bucket."""
    pattern = f"{_COOKIE_RECORD_PREFIX}{scope}:*:*"
    with _cache() as cache:
        # Denied before the records go away, so a concurrent authorize cannot
        # slip between the delete and the deny-list.
        keys = list(cache.scan_iter(match=pattern, count=200))
        for key in keys:
            cache.setex(revocation_key(key.rsplit(":", 1)[-1]), _REVOCATION_TTL, "1")
        for key in keys:
            cache.delete(key)


def _scopes_with_cookies() -> list[str]:
    """Every scope bucket the gateway ever wrote a cookie record into."""
    with _cache() as cache:
        return sorted({
            key.split(":")[2]
            for key in cache.scan_iter(match=_COOKIE_RECORD_PREFIX + "*:*:*", count=500)
            if key.split(":")[2] != UNSCOPED
        })


def _deny_orphans() -> list[str]:
    """Deny cookies whose binding record is already gone.

    A sweep that cannot name the sessions has no scope to delete, so a cookie
    recorded under an expired binding record would survive it. Denied here so
    "end everything" really does mean everything.
    """
    with _cache() as cache:
        for key in cache.scan_iter(match=_COOKIE_RECORD_PREFIX + "*:*:*", count=500):
            if cache.exists(scope_record_key(key.split(":")[2])):
                continue
            cache.setex(revocation_key(key.rsplit(":", 1)[-1]), _REVOCATION_TTL, "1")


def is_app_cookie_revoked(scope: str, cookie_id: str) -> bool:
    """Deny-list check, plus the scope expiry that binds a cookie to a login."""
    with _cache() as cache:
        if cache.exists(revocation_key(cookie_id)):
            return True
        if not scope or scope == UNSCOPED:
            return False
        # The record lives exactly as long as the session that issued this
        # cookie; without it the cookie is a copy or an orphan.
        return not cache.exists(scope_record_key(scope))
