from slowapi import Limiter
from slowapi.util import get_remote_address
from .config import settings
from . import redis_client
from user_agents import parse as ua_parse
import time

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])



def _redis():
    """Resolved per call, so tests and imports never open a connection."""
    return redis_client.client()

def failed_login_key(username: str, ip: str) -> str:
    return f"fail:{username}:{ip}"

LOCK_THRESHOLD = 5
LOCK_SECONDS = 15 * 60  # 15 minutes

# Hibás próbálkozások számolása. A TTL görgővel (minden hibánál újratöltve)
# a számláló sosem jut el a küszöbig, így a zárolás nem válhatott
# felhasználónév-felsorolásra alkalmas időzítővé.
def record_failed_login(username: str, ip: str):
    k = failed_login_key(username, ip)
    count = _redis().incr(k)
    _redis().expire(k, LOCK_SECONDS)
    return count

def is_locked(username: str, ip: str) -> int:
    k = failed_login_key(username, ip)
    count = int(_redis().get(k) or 0)
    if count >= LOCK_THRESHOLD:
        ttl = _redis().ttl(k)
        return max(ttl, 1)
    return 0

def reset_fail(username: str, ip: str):
    _redis().delete(failed_login_key(username, ip))

