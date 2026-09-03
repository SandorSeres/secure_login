import secrets
from datetime import timedelta
import redis
from .config import settings

# Lustán hozzuk létre a klienst, ne modulbetöltéskor bukjon el
_redis_client = None
SESSION_PREFIX = "session:"

def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True
        )
    return _redis_client

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


def create_session(session_id: str, username: str, ttl: timedelta) -> None:
    get_redis().setex(f"{SESSION_PREFIX}{session_id}", ttl, username)


def verify_session(session_id: str, username: str) -> bool:
    return get_redis().get(f"{SESSION_PREFIX}{session_id}") == username


def invalidate_session(session_id: str) -> None:
    get_redis().delete(f"{SESSION_PREFIX}{session_id}")
