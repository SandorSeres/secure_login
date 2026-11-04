"""Minimal HTTP regressions for unchanged login paths and focused security repairs.

Runs with a disposable SQLite connection and mocked external Redis/email services.
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-not-production")
os.environ.setdefault("CSRF_KEY", "test-csrf-not-production")
os.environ["DATABASE_URL"] = "sqlite://"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.deps import get_db, current_user
from app.models import User
import app.main as main

class FakeQuery:
    def __init__(self, user):
        self.user = user
    def filter(self, *args):
        return self
    def first(self):
        return self.user

class FakeDB:
    def __init__(self, user=None):
        self.user = user
        self.commits = 0
    def query(self, model):
        return FakeQuery(self.user)
    def commit(self):
        self.commits += 1

@pytest.fixture
def client(monkeypatch):
    user = User(username="regression", email="regression@example.test",
                password_hash="not-a-real-hash", account_level="Free")
    db = FakeDB(user)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    monkeypatch.setattr(main, "is_locked", lambda username, ip: 0)
    monkeypatch.setattr(main, "verify_password", lambda password, hashed: password == "correct")
    monkeypatch.setattr(main, "reset_fail", lambda username, ip: None)
    monkeypatch.setattr(main, "record_failed_login", lambda username, ip: 1)
    # Do not connect to Redis or SMTP.
    with TestClient(app, base_url="https://testserver") as http:
        yield http, db, user
    app.dependency_overrides.clear()

def test_login_profile_logout(client):
    http, db, _ = client
    login_page = http.get("/")
    assert login_page.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
    assert match, "Login form did not render a CSRF token"
    csrf = match.group(1)
    assert http.cookies.get("csrf_token") == csrf
    result = http.post("/auth/login",
                       data={"username":"regression","password":"correct","csrf_token":csrf},
                       follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == "/profile"
    assert "session=" in result.headers["set-cookie"]
    profile = http.get("/profile")
    assert profile.status_code == 200
    assert "regression" in profile.text
    # Logout requires the CSRF token currently issued by the profile response.
    logout_csrf = http.cookies.get("csrf_token")
    assert logout_csrf
    result = http.post("/auth/logout", data={"csrf_token":logout_csrf}, follow_redirects=False)
    assert result.status_code == 303

def test_missing_csrf_denied(client):
    http, _, _ = client
    assert http.post("/auth/login", data={"username":"regression","password":"correct"},
                     follow_redirects=False).status_code == 403
    assert http.post("/auth/logout", data={}, follow_redirects=False).status_code == 403

def test_password_reset_page_csrf(client, monkeypatch):
    http, _, _ = client
    monkeypatch.setattr(main, "verify_reset_token", lambda token: "regression" if token == "valid" else None)
    assert http.get("/reset-password?token=invalid").status_code == 400
    result = http.get("/reset-password?token=valid")
    assert result.status_code == 200
    assert "csrf_token" in result.text

def test_legacy_admin_is_never_public(client, monkeypatch, tmp_path):
    http, db, user = client
    assert http.post("/admin/set_level?username=regression&level=Admin").status_code in (401, 503)
    assert user.account_level == "Free"
    assert db.commits == 0
    token = tmp_path / "iam_admin_token"
    token.write_text("k" * 48)
    monkeypatch.setenv("IAM_ADMIN_TOKEN_FILE", str(token))
    result = http.post("/admin/set_level?username=regression&level=Admin",
                       headers={"Authorization":"Bearer " + "k"*48})
    assert result.status_code == 200
    assert user.account_level == "Admin"
    assert db.commits == 1
