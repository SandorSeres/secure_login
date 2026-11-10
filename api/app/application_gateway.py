"""Authenticated, application-scoped HTTP gateway.

Only configured private IP origins are accepted; never proxy arbitrary URLs.
App routes use /apps/<id>/... and therefore require prefix-compatible apps.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .deps import current_user, get_db
from .models import ApplicationTarget, User, UserApplicationAccess

router = APIRouter()
_APP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host",
    "content-length", "cookie", "authorization", "set-cookie",
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    "x-forwarded-prefix", "forwarded",
})


def validate_origin(origin: str) -> str:
    """Require an operator-allowlisted, exact upstream origin (no SSRF)."""
    if not isinstance(origin, str) or not origin or origin.strip() != origin:
        raise ValueError("Invalid application target")
    parts = urlsplit(origin)
    if (
        parts.scheme != "http" or not parts.hostname
        or parts.username or parts.password or parts.path
        or parts.query or parts.fragment or not parts.port
    ):
        raise ValueError("Application target must be http://hostname:port")
    allowed = frozenset(
        item.strip()
        for item in os.environ.get("APP_GATEWAY_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    )
    if origin not in allowed:
        raise ValueError("Application target is not allowlisted")
    return origin

def authorized_target(db: Session, user: User, app_id: str) -> str:
    if not _APP_ID.fullmatch(app_id):
        raise HTTPException(status_code=404, detail="Not found")
    target = (
        db.query(ApplicationTarget)
        .join(
            UserApplicationAccess,
            UserApplicationAccess.application_id == ApplicationTarget.application_id,
        )
        .filter(
            ApplicationTarget.application_id == app_id,
            UserApplicationAccess.user_id == user.id,
        )
        .first()
    )
    if target is None:
        raise HTTPException(status_code=403, detail="Application not permitted")
    try:
        return validate_origin(target.upstream_origin)
    except ValueError:
        raise HTTPException(status_code=503, detail="Application target unavailable")


@router.api_route(
    "/apps/{app_id}/{remainder:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@router.api_route(
    "/apps/{app_id}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def application_gateway(
    request: Request,
    app_id: str,
    remainder: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    origin = authorized_target(db, user, app_id)
    # Defend against path-based authority/redirect confusion. httpx encodes
    # query parameters verbatim; no caller-selected host can be forwarded.
    if any(segment in (".", "..") for segment in remainder.split("/")):
        raise HTTPException(status_code=400, detail="Invalid path")
    suffix = "/" + remainder if remainder else "/"
    url = origin + suffix
    if request.url.query:
        url += "?" + request.url.query
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_HEADERS
    }
    headers["X-Forwarded-Prefix"] = f"/apps/{app_id}"
    # A new upstream request is established for every authenticated call.
    # Never follow redirects to a different authority; preserve Location.
    client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(60, connect=5),
        trust_env=False,
    )
    try:
        upstream = await client.send(
            client.build_request(
                request.method, url,
                headers=headers,
                content=await request.body(),
            ),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Application unreachable")

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_HEADERS
    }
    # No cross-application cookie injection into the Secure Login origin.
    # Upstream apps must be explicitly configured for a prefixed base URL.
    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
