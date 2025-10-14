from __future__ import annotations

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # --- App / security ---
    SECRET_KEY: str
    CSRF_KEY: str
    SESSION_COOKIE_NAME: str = "session"
    CSRF_COOKIE_NAME: str = "csrf_token"
    SESSION_TIMEOUT_MINUTES: int = 30
    APP_HOST: str = "http://localhost"  # erre épülnek az e-mailes linkek

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

