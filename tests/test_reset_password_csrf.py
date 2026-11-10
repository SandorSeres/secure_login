"""Password reset page regression: guard against undefined CSRF local."""
import ast
from pathlib import Path
import unittest


class ResetPageCsrfTests(unittest.TestCase):
    def test_reset_page_uses_its_defined_csrf_token(self):
        source = (Path(__file__).resolve().parents[1] / "api/app/main.py").read_text()
        tree = ast.parse(source)
        fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "reset_password_page")
        assigned = {target.id for node in ast.walk(fn) if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)}
        self.assertIn("csrf_token", assigned)
        used = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)}
        self.assertNotIn("csrf", used)
        self.assertIn("csrf_token", used)


if __name__ == "__main__":
    unittest.main()
