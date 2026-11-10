"""Regression guards for successful-login idle reset and profile CSRF rendering."""
import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "api/app/main.py"


def function(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(n for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


class LoginSessionRegressionTests(unittest.TestCase):
    def test_login_resets_idle_timestamp_before_issuing_cookie(self):
        fn = function("login")
        src = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), fn)
        self.assertIn("user.last_activity = datetime.utcnow()", src)
        self.assertIn("db.commit()", src)
        self.assertLess(src.index("user.last_activity = datetime.utcnow()"),
                        src.index("session_val = create_session_value(user.username)"))
        self.assertLess(src.index("db.commit()"),
                        src.index("session_val = create_session_value(user.username)"))

    def test_profile_uses_defined_csrf_token(self):
        fn = function("profile")
        src = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), fn)
        self.assertIn("csrf_token=csrf_token", src)
        self.assertIn("set_csrf_cookie(resp, csrf_token, 3600)", src)
        loaded = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)
                  and isinstance(n.ctx, ast.Load)}
        self.assertNotIn("csrf", loaded)


if __name__ == "__main__":
    unittest.main()
