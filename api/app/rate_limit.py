from slowapi import Limiter
from slowapi.util import get_remote_address
from redis import Redis
from .config import settings
from user_agents import parse as ua_parse
import time

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)

def failed_login_key(username: str, ip: str) -> str:
    return f"fail:{username}:{ip}"

LOCK_THRESHOLD = 5
LOCK_SECONDS = 15 * 60  # 15 minutes

def record_failed_login(username: str, ip: str):
    k = failed_login_key(username, ip)
    count = redis.incr(k)
    if count == 1:
        redis.expire(k, LOCK_SECONDS)
    return count

def is_locked(username: str, ip: str) -> int:
    k = failed_login_key(username, ip)
    count = int(redis.get(k) or 0)
    if count >= LOCK_THRESHOLD:
        ttl = redis.ttl(k)
        return max(ttl, 1)
    return 0

def reset_fail(username: str, ip: str):
    redis.delete(failed_login_key(username, ip))

