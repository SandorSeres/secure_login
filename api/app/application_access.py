"""Optional multi-application IAM tables and authenticated management API.

No existing User columns or login routes are changed. Instances are registered
by a trusted provisioner and can have multiple users; access is denied by default.
"""
import hmac
import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, Session
from .models import Base, User
from .deps import get_db, current_user

APP_KEY = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
ROLE_VALUES = {"viewer", "member", "owner", "admin"}

class ApplicationInstance(Base):
    __tablename__ = "iam_application_instances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    application_type: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class UserInstanceGrant(Base):
    __tablename__ = "iam_user_instance_grants"
    __table_args__ = (UniqueConstraint("user_id", "instance_id", name="uq_iam_user_instance"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("iam_application_instances.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class InstanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_key: str = Field(min_length=1, max_length=64)
    application_type: str = Field(min_length=1, max_length=64)
    upstream: str = Field(min_length=1, max_length=255)

class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(gt=0)
    role: str = "member"

def require_service(authorization: str | None = Header(default=None)) -> None:
    """Separate strong service credential; no implicit user session or spoofable headers."""
    token_path = os.getenv("IAM_ADMIN_TOKEN_FILE")
    if not token_path:
        raise HTTPException(503, "IAM admin service is not configured")
    try:
        expected = Path(token_path).read_text(encoding="utf-8").strip()
    except OSError:
        raise HTTPException(503, "IAM admin service is unavailable") from None
    if len(expected) < 32:
        raise HTTPException(503, "IAM admin credential is invalid")
    scheme, sep, credential = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not sep or not hmac.compare_digest(credential, expected):
        raise HTTPException(401, "Not authorized", headers={"WWW-Authenticate": "Bearer"})

router = APIRouter(prefix="/iam/admin", tags=["application-access"], dependencies=[Depends(require_service)])

@router.post("/instances", status_code=201)
def register_instance(payload: InstanceIn, db: Session = Depends(get_db)):
    if not APP_KEY.fullmatch(payload.instance_key) or not APP_KEY.fullmatch(payload.application_type):
        raise HTTPException(422, "Invalid instance or application key")
    # Accept only local loopback upstreams. Publishing a gateway route is a separate reviewed action.
    import urllib.parse
    parsed = urllib.parse.urlsplit(payload.upstream)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise HTTPException(422, "Only loopback HTTP upstreams are allowed")
    item = db.query(ApplicationInstance).filter_by(instance_key=payload.instance_key).first()
    if item:
        if item.application_type != payload.application_type or item.upstream != payload.upstream:
            raise HTTPException(409, "Conflicting instance registration")
        return {"instance_key": item.instance_key, "enabled": item.enabled}
    item = ApplicationInstance(**payload.model_dump(), enabled=False)
    db.add(item)
    db.commit()
    return {"instance_key": item.instance_key, "enabled": False}

@router.put("/instances/{instance_key}/grants")
def upsert_grant(instance_key: str, payload: GrantIn, db: Session = Depends(get_db)):
    if payload.role not in ROLE_VALUES:
        raise HTTPException(422, "Invalid role")
    instance = db.query(ApplicationInstance).filter_by(instance_key=instance_key).first()
    if not instance:
        raise HTTPException(404, "Unknown instance")
    if not db.query(User).filter_by(id=payload.user_id).first():
        raise HTTPException(404, "Unknown user")
    grant = db.query(UserInstanceGrant).filter_by(instance_id=instance.id, user_id=payload.user_id).first()
    if grant:
        grant.role, grant.enabled = payload.role, True
    else:
        db.add(UserInstanceGrant(instance_id=instance.id, user_id=payload.user_id, role=payload.role, enabled=True))
    db.commit()
    return {"instance_key": instance_key, "user_id": payload.user_id, "role": payload.role, "enabled": True}

@router.delete("/instances/{instance_key}/grants/{user_id}")
def revoke_grant(instance_key: str, user_id: int, db: Session = Depends(get_db)):
    instance = db.query(ApplicationInstance).filter_by(instance_key=instance_key).first()
    if not instance:
        raise HTTPException(404, "Unknown instance")
    grant = db.query(UserInstanceGrant).filter_by(instance_id=instance.id, user_id=user_id).first()
    if grant:
        grant.enabled = False
        db.commit()
    return {"enabled": False}

@router.get("/instances/{instance_key}/access/{user_id}")
def check_access(instance_key: str, user_id: int, db: Session = Depends(get_db)):
    row = db.query(ApplicationInstance, UserInstanceGrant).join(
        UserInstanceGrant, UserInstanceGrant.instance_id == ApplicationInstance.id
    ).filter(ApplicationInstance.instance_key == instance_key,
             ApplicationInstance.enabled.is_(True),
             UserInstanceGrant.user_id == user_id,
             UserInstanceGrant.enabled.is_(True)).first()
    return {"allowed": bool(row), "role": row[1].role if row else None}

@router.put("/instances/{instance_key}/enabled")
def set_enabled(instance_key: str, enabled: bool, db: Session = Depends(get_db)):
    item = db.query(ApplicationInstance).filter_by(instance_key=instance_key).first()
    if not item:
        raise HTTPException(404, "Unknown instance")
    item.enabled = enabled
    db.commit()
    return {"instance_key": item.instance_key, "enabled": item.enabled}

# Browser session-based authorization. Intended only for Nginx auth_request.
# Crucially: never trust user-supplied identity headers or a user_id query string.
gateway_router = APIRouter(prefix="/iam/gateway", tags=["application-access"])

@gateway_router.get("/authorize/{instance_key}")
def authorize_instance(instance_key: str, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    """Return 204 for an active grant, 403 otherwise. Unauthenticated -> 401."""
    allowed = db.query(UserInstanceGrant.id).join(
        ApplicationInstance, ApplicationInstance.id == UserInstanceGrant.instance_id
    ).filter(
        ApplicationInstance.instance_key == instance_key,
        ApplicationInstance.enabled.is_(True),
        UserInstanceGrant.user_id == user.id,
        UserInstanceGrant.enabled.is_(True)
    ).first() is not None
    if not allowed:
        raise HTTPException(403, "Application access denied")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
