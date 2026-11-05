"""Authenticated, opt-in REM service API. No browser session or CSRF bypass.

Deploy only with HTTPS and a private high-entropy REM_PROVISIONING_TOKEN.
"""
from __future__ import annotations

import hmac
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import PortalConfig, settings
from .deps import get_db
from .managed_portals import ManagedPortal, portal_by_id, register_portal
from .models import User, UserPortalAccess

router = APIRouter(prefix="/internal/rem", tags=["rem-provisioning"])


def require_service(authorization: str | None = Header(default=None)) -> None:
    token = settings.REM_PROVISIONING_TOKEN
    if not settings.ENABLE_MULTI_PORTAL or not token:
        raise HTTPException(status_code=404, detail="Provisioning disabled")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Invalid service credential")


class GrantRequest(BaseModel):
    user_id: int


@router.post("/instances", dependencies=[Depends(require_service)])
def create_instance(portal: PortalConfig, db: Session = Depends(get_db)):
    if not portal.enabled:
        raise HTTPException(status_code=422, detail="Disabled instances cannot be registered")
    try:
        outcome = register_portal(db, portal)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": portal.id, "result": outcome}


@router.get("/instances/{portal_id}", dependencies=[Depends(require_service)])
def get_instance(portal_id: str, db: Session = Depends(get_db)):
    portal = portal_by_id(portal_id, db)
    if portal is None or db.get(ManagedPortal, portal.id) is None:
        raise HTTPException(status_code=404, detail="Managed instance not found")
    return portal.model_dump(mode="json")


@router.post("/instances/{portal_id}/grants", dependencies=[Depends(require_service)])
def grant(portal_id: str, request: GrantRequest, db: Session = Depends(get_db)):
    portal = portal_by_id(portal_id, db)
    if portal is None or not portal.enabled or db.get(ManagedPortal, portal.id) is None:
        raise HTTPException(status_code=404, detail="Managed instance not found")
    if db.get(User, request.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if db.get(UserPortalAccess, (request.user_id, portal.id)) is None:
        db.add(UserPortalAccess(user_id=request.user_id, portal_id=portal.id))
        db.commit()
    return {"status": "ok", "portal_id": portal.id, "user_id": request.user_id}


@router.delete("/instances/{portal_id}/grants/{user_id}", dependencies=[Depends(require_service)])
def revoke(portal_id: str, user_id: int, db: Session = Depends(get_db)):
    portal = portal_by_id(portal_id, db)
    if portal is None or db.get(ManagedPortal, portal.id) is None:
        raise HTTPException(status_code=404, detail="Managed instance not found")
    existing = db.get(UserPortalAccess, (user_id, portal.id))
    if existing:
        db.delete(existing)
        db.commit()
    return {"status": "ok", "portal_id": portal.id, "user_id": user_id}
