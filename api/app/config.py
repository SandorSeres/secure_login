from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ENVIRONMENT: str = "production"
    SECRET_KEY: str
    CSRF_KEY: str
    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    SESSION_COOKIE_NAME: str = "session"
    CSRF_COOKIE_NAME: str = "csrf_token"
    HOSTNAME: str = "localhost"

    class Config:
        env_file = ".env"

settings = Settings()  # reads env

