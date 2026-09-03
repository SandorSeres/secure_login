from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings
from typing import Any, Optional


class GroupConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=100)
    default: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, group_id: Any) -> str:
        if not isinstance(group_id, str):
            raise ValueError("group id must be a string")
        return group_id.strip().casefold()

    @field_validator("name")
    @classmethod
    def strip_name(cls, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("group name cannot be blank")
        return normalized


class ApplicationConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    url: HttpUrl
    allowed_groups: set[str] = Field(min_length=1)
    enabled: bool = True

    @field_validator("allowed_groups")
    @classmethod
    def normalize_groups(cls, groups: set[str]) -> set[str]:
        normalized = {group.strip().casefold() for group in groups if group.strip()}
        if not normalized:
            raise ValueError("at least one allowed group is required")
        return normalized


class Settings(BaseSettings):
    # --- App / security ---
    SECRET_KEY: str
    CSRF_KEY: str
    SESSION_COOKIE_NAME: str = "session"
    CSRF_COOKIE_NAME: str = "csrf_token"
    SESSION_TIMEOUT_MINUTES: int = Field(default=30, ge=1, le=720)
    COOKIE_DOMAIN: Optional[str] = None
    COOKIE_SECURE: bool = True
    APP_HOST: str = "http://localhost"  # erre épülnek az e-mailes linkek
    GROUPS_JSON: list[GroupConfig] = Field(min_length=1)
    APPLICATIONS_JSON: list[ApplicationConfig] = Field(default_factory=list)
    ADMIN_USERNAMES: set[str] = Field(default_factory=set)

    # --- Database ---
    DATABASE_URL: Optional[str] = None  # ha nincs megadva, PG_* alapján képezzük
    PG_USER: Optional[str] = None
    PG_PASSWORD: Optional[str] = None
    PG_DB: Optional[str] = None
    PG_HOST: str = "db"
    PG_PORT: int = 5432

    # --- Redis (rate limit + reset token tárolás) ---
    REDIS_URL: Optional[str] = None     # pl. redis://host:port/0 vagy rediss://...
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    # --- SMTP ---
    SMTP_HOST: str = "mailhog"
    SMTP_PORT: int = 1025
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: str = "no-reply@example.com"
    SMTP_TLS: bool = False       # SMTPS (465) esetén True
    SMTP_STARTTLS: bool = False  # STARTTLS (587) esetén True

    # --- Egyebek ---
    RATE_LIMIT_ENABLED: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"

    @field_validator("ADMIN_USERNAMES")
    @classmethod
    def normalize_admin_usernames(cls, usernames: set[str]) -> set[str]:
        return {username.strip().casefold() for username in usernames if username.strip()}

    @model_validator(mode="after")
    def validate_access_configuration(self) -> "Settings":
        group_ids = [group.id for group in self.GROUPS_JSON]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("GROUPS_JSON contains duplicate group ids")

        default_groups = [group for group in self.GROUPS_JSON if group.default]
        if len(default_groups) != 1:
            raise ValueError("GROUPS_JSON must contain exactly one default group")

        application_ids = [application.id for application in self.APPLICATIONS_JSON]
        if len(application_ids) != len(set(application_ids)):
            raise ValueError("APPLICATIONS_JSON contains duplicate application ids")

        configured_group_ids = set(group_ids)
        unknown_groups = sorted(
            {
                group_id
                for application in self.APPLICATIONS_JSON
                for group_id in application.allowed_groups
                if group_id not in configured_group_ids
            }
        )
        if unknown_groups:
            raise ValueError(
                "APPLICATIONS_JSON references unknown groups: "
                + ", ".join(unknown_groups)
            )
        return self

    @property
    def default_group(self) -> GroupConfig:
        return next(group for group in self.GROUPS_JSON if group.default)

    def group_by_id(self, group_id: str) -> GroupConfig | None:
        normalized_id = group_id.strip().casefold()
        return next(
            (group for group in self.GROUPS_JSON if group.id == normalized_id),
            None,
        )

    # Segéd: effektív adatbázis URL
    def build_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.PG_USER and self.PG_PASSWORD and self.PG_DB:
            return (
                f"postgresql+psycopg2://{self.PG_USER}:{self.PG_PASSWORD}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"
            )
        # fallback lokális fejlesztéshez
        return "sqlite:///./local.db"

    # Segéd: effektív redis URL
    def build_redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"


settings = Settings()

# Biztosítsuk, hogy a régi kód, ami settings.DATABASE_URL-t vár, megkapja
if not settings.DATABASE_URL:
    settings.DATABASE_URL = settings.build_database_url()

# Ugyanez Redisre: ha nincs explicit REDIS_URL, állítsuk be az effektívet
if not settings.REDIS_URL:
    settings.REDIS_URL = settings.build_redis_url()
