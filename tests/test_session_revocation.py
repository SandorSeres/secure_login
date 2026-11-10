"""Revocable sessions and the guarded admin endpoint, without a live Redis."""
import ast
import re
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "api/app/main.py"


def function(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name)


def source_of(name):
    return ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), function(name))


class SessionRevocationTests(unittest.TestCase):
    def test_session_value_carries_a_revocable_identifier(self):
        src = source_of("logout")
        # Only the session that clicked logout: another device stays signed in.
        self.assertIn("revoke_session(verified[0], verified[1])", src)
        self.assertNotIn("revoke_all_sessions(user.username)", src)
        self.assertIn("is_session_valid",
                      (Path(__file__).resolve().parents[1] / "api/app/deps.py").read_text())

    def test_password_change_revokes_sessions_and_app_cookies(self):
        src = source_of("reset_password")
        # Ending a session ends the application cookies it issued, so the
        # route has nothing left to sweep by hand.
        self.assertIn("revoke_all_sessions(username)", src)
        self.assertNotIn("sweep_app_cookies", src)

    def test_admin_endpoint_requires_authentication_and_admin_role(self):
        src = source_of("set_level")
        self.assertIn("admin: User = Depends(current_user)", src)
        self.assertIn("if not is_admin(admin)", src)
        self.assertIn("csrf_token", src)

    def test_admin_level_is_validated_before_the_write(self):
        src = source_of("set_level")
        self.assertIn("SetLevelIn(username=username.strip(), level=level)", src)
        self.assertLess(src.index("SetLevelIn("), src.index("db.commit()"))

    def test_level_change_revokes_the_target_sessions(self):
        src = source_of("set_level")
        self.assertIn("revoke_all_sessions(user.username)", src)
        # Cookies with no session behind them cannot be attributed to anyone,
        # so a privilege change must reach those too.
        self.assertIn("sweep_app_cookies(None", src)

    def test_a_revocation_does_not_sign_other_users_out(self):
        store = (Path(__file__).resolve().parents[1] / "api/app/session_store.py").read_text()
        logout = source_of("logout")
        # Signing out one browser ends that session, not every device's.
        self.assertIn("def revoke_session(", store)
        self.assertNotIn("revoke_all_sessions(user.username)", logout)

    def test_a_scopeless_sweep_can_keep_one_live_scope(self):
        # An administrator's own session must survive the sweep it triggers.
        cookie = (Path(__file__).resolve().parents[1] / "api/app/session_cookie.py").read_text()
        self.assertIn("def sweep_app_cookies(scope: Optional[str], keep:", cookie)

    def test_admin_usernames_are_exact_match_only(self):
        src = (Path(__file__).resolve().parents[1] / "api/app/main.py").read_text()
        body = src[src.index("def is_admin("):]
        body = body[:body.index("\n\n\n")]
        self.assertRegex(body, r"ADMIN_USERNAMES\.split\(\",\"\)")
        self.assertNotIn("startswith", body)
        self.assertIn("user.username in allowed", body)

    def test_allowed_levels_match_the_documented_tiers(self):
        schema = (Path(__file__).resolve().parents[1] / "api/app/schemas.py").read_text()
        for level in ("Free", "Pro", "Enterprise"):
            self.assertIn(level, schema)


if __name__ == "__main__":
    unittest.main()


class SessionCookieTtlTests(unittest.TestCase):
    """The server-side session must not outlive the cookie that references it."""

    def setUp(self):
        from app import session_store
        self.store = session_store
        self.calls = []
        self.values = {}
        self._original = session_store._cache
        session_store._cache = lambda: self

    def tearDown(self):
        from app import session_store
        session_store._cache = self._original

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def setex(self, key, ttl, value):
        self.calls.append(("setex", key, ttl))
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def expire(self, key, ttl):
        self.calls.append(("expire", key, ttl))
        return True

    def exists(self, key):
        return int(key in self.values)

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    def scan_iter(self, match=None, count=None):
        import re as _re
        pattern = "^" + _re.escape(match or "*").replace("\\*", ".*") + "$"
        return iter([key for key in self.values if _re.fullmatch(pattern, key)])

    def test_register_writes_a_timestamped_record(self):
        self.store.register_session("alice", "jti-1", self.store.new_scope_id(), ttl=3600)
        verb, key, ttl = self.calls[0]
        self.assertEqual((verb, ttl), ("setex", 3600))
        self.assertTrue(self.values[key].isdigit(), "value must carry the issue time")

    def test_touch_never_extends_past_the_cookie_lifetime(self):
        import time as _time
        self.store.register_session("alice", "jti-2", self.store.new_scope_id(), ttl=3600)
        key = self.store.session_key("alice", "jti-2")
        self.values[key] = str(int(_time.time()) - 1800)
        self.store.touch_session("alice", "jti-2", ttl=3600)
        verb, _, remaining = self.calls[-1]
        self.assertEqual(verb, "expire")
        self.assertLessEqual(remaining, 3600)
        self.assertLess(remaining, 3600, "a half-lived session must shrink, not reset")

    def test_touch_on_an_unknown_session_does_nothing(self):
        self.store.touch_session("ghost", "nope", ttl=3600)
        self.assertNotIn("expire", [call[0] for call in self.calls])
