"""Unit tests for service API security and managed portal persistence.

Run with IAM Python dependencies installed. No real PostgreSQL or Docker needed.
"""
import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "a"*48)
os.environ.setdefault("CSRF_KEY", "test-csrf-key-" + "b"*48)
os.environ.setdefault("ROLES_JSON", '[{"id":"user","name":"User","default":true}]')

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import PortalConfig, settings
from app.models import Base, User, Role, Portal, UserPortalAccess
from app.managed_portals import ManagedPortal, register_portal, portal_by_id
from app.rem_provisioning import require_service
from fastapi import HTTPException


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_MULTI_PORTAL", True)
    monkeypatch.setattr(settings, "PORTALS_JSON", [])
    engine=create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def config():
    return PortalConfig(id="research-a",name="Research A",
                        url="https://research.example/apps/research-a/",
                        upstream="127.0.0.1:8205")


def test_managed_registration_and_idempotence(db):
    assert register_portal(db, config()) == "created"
    assert register_portal(db, config()) == "already-registered"
    loaded=portal_by_id("research-a",db)
    assert loaded.upstream=="127.0.0.1:8205"
    assert db.query(ManagedPortal).count()==1


def test_conflicts_cannot_overwrite(db):
    register_portal(db, config())
    changed=config().model_copy(update={"upstream":"127.0.0.1:8206"})
    with pytest.raises(ValueError):
        register_portal(db, changed)
    assert portal_by_id("research-a",db).upstream=="127.0.0.1:8205"


def test_service_token_is_opt_in(monkeypatch):
    monkeypatch.setattr(settings,"ENABLE_MULTI_PORTAL",True)
    monkeypatch.setattr(settings,"REM_PROVISIONING_TOKEN",None)
    with pytest.raises(HTTPException) as exc:
        require_service("Bearer " + "x"*32)
    assert exc.value.status_code==404


def test_service_token_never_uses_browser_session(monkeypatch):
    token="a"*48
    monkeypatch.setattr(settings,"ENABLE_MULTI_PORTAL",True)
    monkeypatch.setattr(settings,"REM_PROVISIONING_TOKEN",token)
    for wrong in [None, "Bearer "+("z"*48), "Basic "+token]:
        with pytest.raises(HTTPException) as exc:
            require_service(wrong)
        assert exc.value.status_code==401
    assert require_service("Bearer "+token) is None


def test_startup_sync_preserves_managed_portal(db):
    from app.database_migrations import sync_portals
    register_portal(db, config())
    sync_portals(db.get_bind(), Portal.__table__, [])
    db.expire_all()
    assert portal_by_id("research-a", db) is not None
    assert db.get(Portal, "research-a") is not None


def test_disabled_mode_hides_managed_portals(db, monkeypatch):
    register_portal(db, config())
    monkeypatch.setattr(settings, "ENABLE_MULTI_PORTAL", False)
    assert portal_by_id("research-a", db) is None


def test_configured_portal_upstream_is_reserved(db, monkeypatch):
    other = PortalConfig(id="configured", name="Configured",
                         url="https://configured.example/",
                         upstream="127.0.0.1:8205")
    monkeypatch.setattr(settings, "PORTALS_JSON", [other])
    with pytest.raises(ValueError):
        register_portal(db, config())
    assert db.query(ManagedPortal).count() == 0


def test_http_service_api_register_grant_and_revoke(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.rem_provisioning import router
    monkeypatch.setattr(settings, "REM_PROVISIONING_TOKEN", "x"*48)
    db.add(Role(id="user", name="User", is_default=True, sort_order=0))
    db.add(User(username="test-user", email="test-user@example.invalid",
                password_hash="fake-test-only", role_id="user"))
    db.commit()
    user_id=db.query(User).one().id
    app=FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db]=lambda: db
    client=TestClient(app)
    path="/internal/rem/instances"
    assert client.post(path,json=config().model_dump(mode="json")).status_code==401
    auth={"Authorization":"Bearer "+"x"*48}
    result=client.post(path,json=config().model_dump(mode="json"),headers=auth)
    assert result.status_code==200, result.text
    assert result.json()["result"]=="created"
    assert client.post(path,json=config().model_dump(mode="json"),headers=auth).json()["result"]=="already-registered"
    assert client.get(path+"/research-a",headers=auth).status_code==200
    grant=path+"/research-a/grants"
    assert client.post(grant,json={"user_id":user_id},headers=auth).status_code==200
    assert db.get(UserPortalAccess,(user_id,"research-a")) is not None
    assert client.post(grant,json={"user_id":user_id},headers=auth).status_code==200
    assert client.delete(grant+f"/{user_id}",headers=auth).status_code==200
    db.expire_all()
    assert db.get(UserPortalAccess,(user_id,"research-a")) is None
    assert client.delete(grant+f"/{user_id}",headers=auth).status_code==200
    assert client.post(grant,json={"user_id":999999},headers=auth).status_code==404
    assert client.get(path+"/missing",headers=auth).status_code==404
