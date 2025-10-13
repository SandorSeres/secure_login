from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ENVIRONMENT: str = "production"
    SECRET_KEY: str
    CSRF_KEY: str
    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    SESSION_COOKIE_NAME: str = "session"
    CSRF_COOKIE_NAME: str = "csrf_token"
    # --- Redis a jelszó-reset tokenhez / rate limithez ---
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    HOSTNAME: str = "localhost"
    BASE_URL: str = "http://localhost"  # linkekhez
    SMTP_HOST: str = "mailhog"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = False
    SMTP_FROM: str = "no-reply@example.local"
    FRONTEND_URL: str = "http://localhost:8000"

    class Config:
        env_file = ".env"

settings = Settings()  # reads env


