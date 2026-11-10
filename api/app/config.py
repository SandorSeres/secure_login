from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ENVIRONMENT: str = "production"
    SECRET_KEY: str
    CSRF_KEY: str
    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    SESSION_COOKIE_NAME: str = "session"
    CSRF_COOKIE_NAME: str = "csrf_token"
    # Éles telepítésen maradjon true; localhost-on futtatható HTTP-n is.
    COOKIE_SECURE: bool = True
    # Inaktivitási limit másodpercben (a README "session timeout" beállítása).
    SESSION_TIMEOUT_MINUTES: int = 10
    # A session süti abszolút élettartama.
    SESSION_COOKIE_TTL_SECONDS: int = 6 * 60 * 60
    # Az alkalmazásgateway süti élettartama.
    APP_SESSION_TTL_SECONDS: int = 6 * 60 * 60
    # --- Redis a jelszó-reset tokenhez / rate limithez ---
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    APP_HOST: str = "http://localhost"  # linkekhez
    # Pontosan megadott felhasználónevek, amelyek módosíthatják a szinteket.
    # Üres esetén csak a Enterprise szintű felhasználó érhet az admin végponthoz.
    ADMIN_USERNAMES: str = ""
    SMTP_HOST: str = "mailhog"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = False
    SMTP_FROM: str = "no-reply@example.local"
    FRONTEND_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=".env",
        # A ./.env deploy-onként több olyan kulcsot is tartalmaz (proxy képek,
        # slot-listák), amit ez az app nem olvas. Ismeretlen kulcs miatt nem
        # szabad elindulni megakadni; a HIÁYZÓ kötelező kulcs továbbra is hiba.
        extra="ignore",
    )

settings = Settings()  # reads env


