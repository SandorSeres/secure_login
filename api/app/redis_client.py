"""One Redis client for the whole app, built from REDIS_URL.

token_store used REDIS_HOST/REDIS_PORT while everything else used REDIS_URL,
so a deployment setting only one of them silently split keys across two
servers. REDIS_URL is now the single source of truth.
"""
from __future__ import annotations

from redis import Redis

from .config import settings


def get_client() -> Redis:
    """Build a client per call: connections are pooled, a cached object is not.

    Cached clients would also defeat replacement, letting one module keep
    talking to a real server while the rest of a request used a fake.
    """
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)


def client() -> Redis:
    """The accessor every module uses, looked up here on every call.

    A module that imports this name and binds it locally could not be pointed
    at a test fake without patching every one of those modules separately.
    """
    return get_client()
