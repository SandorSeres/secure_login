from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from .config import PortalConfig, settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from .models import User


def user_roles(user: "User") -> set[str]:
    value = str(user.role_id)
    return {value.strip().casefold()}


def user_role_name(user: "User") -> str:
    role_id = next(iter(user_roles(user)))
    role = settings.role_by_id(role_id)
    return role.name if role else str(user.role_id)


def is_admin(user: "User") -> bool:
    # Usernames are case-sensitive in registration and in the database.
    return user.username in settings.ADMIN_USERNAMES


def _grant_ids(user: "User", db: "Session") -> list[str]:
    from .models import UserPortalAccess

    rows = (db.query(UserPortalAccess.portal_id)
            .filter(UserPortalAccess.user_id == user.id)
            .order_by(UserPortalAccess.portal_id).all())
    return [row[0] for row in rows]


def assigned_portal_id(user: "User", db: "Session") -> str | None:
    """Legacy helper: never select an arbitrary portal when grants are ambiguous."""
    ids = _grant_ids(user, db)
    return ids[0] if len(ids) == 1 else None


def portals_for_user(user: "User", db: "Session") -> list[PortalConfig]:
    return [portal for id_ in _grant_ids(user, db)
            if (portal := settings.portal_by_id(id_)) is not None and portal.enabled]


def assigned_portal(user: "User", db: "Session", portal_id: str | None = None) -> PortalConfig:
    """Fail closed on ambiguous grants: require explicitly selected portal."""
    portals = portals_for_user(user, db)
    if portal_id is not None:
        normalized = portal_id.strip().casefold()
        selected = next((p for p in portals if p.id == normalized), None)
        if selected is not None:
            return selected
    elif len(portals) == 1:
        return portals[0]

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                        detail="Portal access denied")


def require_portal_access(user: "User", portal_id: str, db: "Session") -> PortalConfig:
    return assigned_portal(user, db, portal_id)
