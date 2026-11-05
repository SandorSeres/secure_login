from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any, Optional
from urllib.parse import quote


class RoleConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=100)
    default: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, role_id: Any) -> str:
        if not isinstance(role_id, str):
            raise ValueError("role id must be a string")
        return role_id.strip().casefold()

    @field_validator("name")
    @classmethod
    def strip_name(cls, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("role name cannot be blank")
        return normalized


class PortalConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    url: HttpUrl
    upstream: str
    enabled: bool = True

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, portal_id: Any) -> str:
        if not isinstance(portal_id, str):
            raise ValueError("portal id must be a string")
        return portal_id.strip().casefold()

    @field_validator("name")
    @classmethod
    def strip_name(cls, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("portal name cannot be blank")
        return normalized

    @field_validator("url")
    @classmethod
    def require_https(cls, url: HttpUrl) -> HttpUrl:
        if url.scheme != "https":
            raise ValueError("portal URL must use HTTPS")
        return url

    @field_validator("upstream")
    @classmethod
    def require_loopback_portal_upstream(cls, upstream: str) -> str:
        host, separator, port_text = upstream.strip().rpartition(":")
        if separator != ":" or host != "127.0.0.1" or not port_text.isdigit():
            raise ValueError("portal upstream must use 127.0.0.1:<port>")

        port = int(port_text)
        if not 8000 <= port <= 8999:
            raise ValueError("portal upstream port must be between 8000 and 8999")
        return f"{host}:{port}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App / security ---
    SECRET_KEY: str
    CSRF_KEY: str
    SESSION_COOKIE_NAME: str = "session"
    CSRF_COOKIE_NAME: str = "csrf_token"
    SESSION_TIMEOUT_MINUTES: int = Field(default=30, ge=1, le=720)
    COOKIE_DOMAIN: Optional[str] = None
    COOKIE_SECURE: bool = True
    APP_HOST: HttpUrl = "https://localhost"  # erre épülnek az e-mailes linkek
    ROLES_JSON: list[RoleConfig] = Field(min_length=1)
    PORTALS_JSON: list[PortalConfig] = Field(default_factory=list)
    ADMIN_USERNAMES: set[str] = Field(default_factory=set)

    # --- Database ---
    DATABASE_URL: Optional[str] = None  # ha nincs megadva, PG_* alapján képezzük
    PG_USER: Optional[str] = None
    PG_PASSWORD: Optional[str] = None
    PG_DB: Optional[str] = None
    PG_HOST: str = "db"
    PG_PORT: int = 5432

    # --- Redis (rate limit + reset token tárolás) ---
    REDIS_URL: Optional[str] = None  # pl. redis://host:port/0 vagy rediss://...
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    # --- SMTP ---
    SMTP_HOST: str = "mailhog"
    SMTP_PORT: int = 1025
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: str = "no-reply@example.com"
    SMTP_SSL: bool = False  # SMTPS (465) esetén True
    SMTP_STARTTLS: bool = False  # STARTTLS (587) esetén True

    # --- Egyebek ---
    RATE_LIMIT_ENABLED: bool = True
    # Opt-in only. Legacy 1:1 routing remains the default.
    ENABLE_MULTI_PORTAL: bool = False
    REM_PROVISIONING_TOKEN: Optional[str] = Field(default=None, min_length=32)

    @field_validator("ADMIN_USERNAMES")
    @classmethod
    def normalize_admin_usernames(cls, usernames: set[str]) -> set[str]:
        return {username.strip() for username in usernames if username.strip()}

    @field_validator("SECRET_KEY", "CSRF_KEY")
    @classmethod
    def require_strong_signing_key(cls, signing_key: str) -> str:
        if len(signing_key) < 32 or signing_key.startswith("replace-"):
            raise ValueError(
                "signing keys must be at least 32 non-placeholder characters"
            )
        return signing_key

    @field_validator("APP_HOST")
    @classmethod
    def require_https_app_host(cls, app_host: HttpUrl) -> HttpUrl:
        if app_host.scheme != "https":
            raise ValueError("APP_HOST must use HTTPS")
        return app_host

    @model_validator(mode="after")
    def validate_access_configuration(self) -> "Settings":
        if self.SECRET_KEY == self.CSRF_KEY:
            raise ValueError("SECRET_KEY and CSRF_KEY must be different")
        if not self.COOKIE_SECURE:
            raise ValueError("COOKIE_SECURE must be true for HTTPS deployment")

        role_ids = [role.id for role in self.ROLES_JSON]
        if len(role_ids) != len(set(role_ids)):
            raise ValueError("ROLES_JSON contains duplicate role ids")

        default_roles = [role for role in self.ROLES_JSON if role.default]
        if len(default_roles) != 1:
            raise ValueError("ROLES_JSON must contain exactly one default role")

        portal_ids = [portal.id for portal in self.PORTALS_JSON]
        if len(portal_ids) != len(set(portal_ids)):
            raise ValueError("PORTALS_JSON contains duplicate portal ids")

        portal_upstreams = [portal.upstream for portal in self.PORTALS_JSON]
        if len(portal_upstreams) != len(set(portal_upstreams)):
            raise ValueError("PORTALS_JSON contains duplicate portal upstreams")
        return self

    @property
    def default_role(self) -> RoleConfig:
        return next(role for role in self.ROLES_JSON if role.default)

    def role_by_id(self, role_id: str) -> RoleConfig | None:
        normalized_id = role_id.strip().casefold()
        return next(
            (role for role in self.ROLES_JSON if role.id == normalized_id),
            None,
        )

    def portal_by_id(self, portal_id: str) -> PortalConfig | None:
        normalized_id = portal_id.strip().casefold()
        return next(
            (portal for portal in self.PORTALS_JSON if portal.id == normalized_id),
            None,
        )

    # Segéd: effektív adatbázis URL
    def build_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.PG_USER and self.PG_PASSWORD and self.PG_DB:
            user = quote(self.PG_USER, safe="")
            password = quote(self.PG_PASSWORD, safe="")
            database = quote(self.PG_DB, safe="")
            return (
                f"postgresql+psycopg2://{user}:{password}"
                f"@{self.PG_HOST}:{self.PG_PORT}/{database}"
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
