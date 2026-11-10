"""Single source of timestamp values, kept naive-UTC for the existing columns.

The users table stores naive UTC in created_at / last_activity. Python 3.12
deprecates datetime.utcnow(), so every new timestamp goes through here.
"""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
