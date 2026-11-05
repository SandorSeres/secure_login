"""Persist REM-managed portal metadata separately from configured legacy portals.

Legacy PORTALS_JSON retains ownership of its entries. Managed portals require
opt-in multi-portal mode and a server-to-server credential.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Boolean, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from .models import Base, Portal
from .config import PortalConfig, settings


class ManagedPortal(Base):
    __tablename__ = "managed_portals"
    id: Mapped[str] = mapped_column(String(63), ForeignKey("portals.id", ondelete="CASCADE"), primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    upstream: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")


def portal_by_id(portal_id: str, db: Session) -> PortalConfig | None:
    normalized = portal_id.strip().casefold()
    configured = settings.portal_by_id(normalized)
    if configured is not None:
        return configured
    if not settings.ENABLE_MULTI_PORTAL:
        return None
    row = db.get(ManagedPortal, normalized)
    portal = db.get(Portal, normalized) if row else None
    if row is None or portal is None:
        return None
    return PortalConfig(id=portal.id, name=portal.name, description=row.description,
                        url=row.url, upstream=row.upstream, enabled=portal.enabled)


def register_portal(db: Session, config: PortalConfig) -> str:
    if settings.portal_by_id(config.id) is not None:
        raise ValueError("Configured portal is not REM-managed")
    if any(p.upstream == config.upstream for p in settings.PORTALS_JSON):
        raise ValueError("Upstream belongs to a configured portal")
    existing = db.get(Portal, config.id)
    managed = db.get(ManagedPortal, config.id)
    if existing is not None and managed is None:
        raise ValueError("Portal ID belongs to another owner")
    duplicate = db.scalar(select(ManagedPortal.id).where(
        ManagedPortal.upstream == config.upstream, ManagedPortal.id != config.id))
    if duplicate is not None:
        raise ValueError("Upstream already registered")
    if existing is not None:
        if (managed.url != str(config.url) or managed.upstream != config.upstream
                or existing.name != config.name or existing.enabled != config.enabled):
            raise ValueError("Existing portal differs; explicit update not supported")
        return "already-registered"
    sort_order = len(settings.PORTALS_JSON) + db.query(ManagedPortal).count()
    db.add(Portal(id=config.id, name=config.name, enabled=config.enabled, sort_order=sort_order))
    db.add(ManagedPortal(id=config.id, description=config.description,
                         url=str(config.url), upstream=config.upstream))
    db.commit()
    return "created"
