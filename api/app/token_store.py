import secrets
from datetime import timedelta
from .config import settings
from . import redis_client


def get_redis():
    """Shared client; kept as a name for existing callers and tests."""
    return redis_client.client()

def create_reset_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    r = get_redis()
    r.setex(f"reset:{token}", timedelta(minutes=15), username)
    return token

def verify_reset_token(token: str) -> str | None:
    r = get_redis()
    return r.get(f"reset:{token}")

def invalidate_reset_token(token: str):
    r = get_redis()
    r.delete(f"reset:{token}")

