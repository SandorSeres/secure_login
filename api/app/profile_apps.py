"""Read-only, user-scoped application listing for Secure Login profile."""
from sqlalchemy.orm import Session

from .models import Application, ApplicationTarget, UserApplicationAccess


def applications_for_user(db: Session, user_id: int) -> list[dict[str, str | bool]]:
    """Only assigned applications; show unconfigured ones without a launch link."""
    rows = (
        db.query(Application.id, Application.name, ApplicationTarget.public_url)
        .join(UserApplicationAccess, UserApplicationAccess.application_id == Application.id)
        .outerjoin(ApplicationTarget, ApplicationTarget.application_id == Application.id)
        .filter(UserApplicationAccess.user_id == user_id)
        .order_by(Application.name, Application.id)
        .all()
    )
    return [
        {"id": app_id, "name": name, "launchable": bool(public_url)}
        for app_id, name, public_url in rows
    ]
