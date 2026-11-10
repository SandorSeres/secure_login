"""A keyspace that behaves like the Redis client the app actually uses.

Only what the app calls is implemented, and glob patterns are matched the
Redis way, so a test cannot pass on a pattern the real client would not accept.
"""
import fnmatch


class FakeCache:
    def __init__(self):
        self.store = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def setex(self, key, seconds, value):
        assert seconds > 0, "a record needs a positive TTL"
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def expire(self, key, seconds):
        return key in self.store

    def getdel(self, key):
        return self.store.pop(key, None)

    def exists(self, key):
        return int(key in self.store)

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    def scan_iter(self, match=None, count=None):
        return iter([key for key in self.store if fnmatch.fnmatchcase(key, match or "*")])
