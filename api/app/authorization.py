from fastapi import HTTPException, status

from .config import ApplicationConfig, settings
from .models import User


def user_groups(user: User) -> set[str]:
    level = user.account_level
    value = level.value if hasattr(level, "value") else str(level)
    return {value.casefold()}


def is_admin(user: User) -> bool:
    return user.username.casefold() in settings.ADMIN_USERNAMES


def applications_for_user(user: User) -> list[ApplicationConfig]:
    groups = user_groups(user)
    return [
        application
        for application in settings.APPLICATIONS_JSON
        if application.enabled and groups.intersection(application.allowed_groups)
    ]


def require_application_access(user: User, application_id: str) -> ApplicationConfig:
    for application in applications_for_user(user):
        if application.id == application_id:
            return application

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Application access denied",
    )
