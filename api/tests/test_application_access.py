"""Isolated IAM API contract tests without requiring the production login app."""
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Test-only configuration. Never use these credentials for production.
os.environ.setdefault("SECRET_KEY", "test-secret-not-for-production")
os.environ.setdefault("CSRF_KEY", "test-csrf-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Base, User  # noqa: E402
from app.deps import get_db  # noqa: E402
from app.application_access import router  # noqa: E402


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add_all([
            User(username="alpha", email="alpha@example.test", password_hash="x"),
            User(username="beta", email="beta@example.test", password_hash="x"),
        ])
        db.commit()
        ids = [r.id for r in db.query(User).order_by(User.id)]
    def override():
        with Session() as db:
            yield db
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = override
    token_file = tmp_path / "service_token"
    token_file.write_text("t" * 48, encoding="utf-8")
    monkeypatch.setenv("IAM_ADMIN_TOKEN_FILE", str(token_file))
    yield TestClient(api), ids
    engine.dispose()


def auth():
    return {"Authorization": "Bearer " + "t" * 48}


def register(client, key, port):
    return client.post("/iam/admin/instances", headers=auth(),
                       json={"instance_key": key, "application_type": "research",
                             "upstream": f"http://127.0.0.1:{port}"})


def test_fail_closed(ctx, monkeypatch):
    client, _ = ctx
    assert client.post("/iam/admin/instances", json={}).status_code == 401
    monkeypatch.delenv("IAM_ADMIN_TOKEN_FILE")
    assert client.post("/iam/admin/instances", json={}, headers=auth()).status_code == 503


def test_multi_user_multi_app_and_revocation(ctx):
    client, (first, second) = ctx
    assert register(client, "research-one", 8201).status_code == 201
    assert register(client, "research-two", 8202).status_code == 201
    assert register(client, "research-one", 8201).status_code == 201
    assert register(client, "research-one", 8205).status_code == 409
    for key, uid in [("research-one", first), ("research-two", first), ("research-one", second)]:
        r = client.put(f"/iam/admin/instances/{key}/grants", headers=auth(),
                       json={"user_id": uid, "role": "member"})
        assert r.status_code == 200
    check = lambda key, uid: client.get(
        f"/iam/admin/instances/{key}/access/{uid}", headers=auth()).json()["allowed"]
    assert check("research-one", first) is False  # disabled by default
    assert client.put("/iam/admin/instances/research-one/enabled?enabled=true",
                      headers=auth()).status_code == 200
    assert client.put("/iam/admin/instances/research-two/enabled?enabled=true",
                      headers=auth()).status_code == 200
    assert check("research-one", first) is True
    assert check("research-one", second) is True
    assert check("research-two", first) is True
    assert check("research-two", second) is False
    assert client.delete(f"/iam/admin/instances/research-one/grants/{first}",
                         headers=auth()).status_code == 200
    assert check("research-one", first) is False
    assert check("research-one", second) is True


def test_reject_external_upstreams(ctx):
    client, _ = ctx
    for url in ("http://example.com:8201", "https://127.0.0.1:8201",
                "http://127.0.0.1:8201/path", "http://127.0.0.1:8201@evil.example:9000"):
        result = client.post("/iam/admin/instances", headers=auth(),
                             json={"instance_key": "bad-target",
                                   "application_type": "research", "upstream": url})
        assert result.status_code == 422
