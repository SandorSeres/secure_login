"""Browser-session gateway checks with an isolated real SQLAlchemy database."""
import os
import sys
from pathlib import Path
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "gateway-test-secret-not-production")
os.environ.setdefault("CSRF_KEY", "gateway-test-csrf-not-production")
os.environ["DATABASE_URL"] = "sqlite://"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.models import Base, User
from app.deps import get_db
from app.security import create_session_value
from app.application_access import ApplicationInstance, UserInstanceGrant, gateway_router


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        users = [
            User(username="alice", email="alice@example.test", password_hash="test",
                 last_activity=datetime.utcnow()),
            User(username="bob", email="bob@example.test", password_hash="test",
                 last_activity=datetime.utcnow()),
        ]
        db.add_all(users)
        db.flush()
        first = ApplicationInstance(instance_key="workspace-one", application_type="research",
                                    upstream="http://127.0.0.1:8201", enabled=True)
        second = ApplicationInstance(instance_key="workspace-two", application_type="research",
                                     upstream="http://127.0.0.1:8202", enabled=False)
        db.add_all([first, second])
        db.flush()
        db.add_all([
            UserInstanceGrant(instance_id=first.id, user_id=users[0].id, role="owner", enabled=True),
            UserInstanceGrant(instance_id=first.id, user_id=users[1].id, role="viewer", enabled=False),
            UserInstanceGrant(instance_id=second.id, user_id=users[0].id, role="owner", enabled=True),
        ])
        db.commit()
    api = FastAPI()
    api.include_router(gateway_router)
    def override_db():
        with Session() as db:
            yield db
    api.dependency_overrides[get_db] = override_db
    with TestClient(api, base_url="https://testserver") as client:
        yield client, Session
    engine.dispose()


def test_no_session_and_forged_headers_cannot_authorize(setup):
    client, _ = setup
    url = "/iam/gateway/authorize/workspace-one"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-User-ID": "1", "X-Forwarded-User": "alice",
                                    "X-Remote-User": "alice"}).status_code == 401
    assert client.get(url, cookies={"session": "invalid"}).status_code == 401


def test_grant_only_for_correct_user_and_enabled_instance(setup):
    client, _ = setup
    url = "/iam/gateway/authorize/"
    alice = {"session": create_session_value("alice")}
    bob = {"session": create_session_value("bob")}
    ok = client.get(url + "workspace-one", cookies=alice)
    assert ok.status_code == 204
    assert ok.headers["cache-control"] == "no-store"
    assert client.get(url + "workspace-two", cookies=alice).status_code == 403
    assert client.get(url + "unknown", cookies=alice).status_code == 403
    assert client.get(url + "workspace-one", cookies=bob).status_code == 403


def test_revocation_is_effective_next_request(setup):
    client, Session = setup
    cookies = {"session": create_session_value("alice")}
    url = "/iam/gateway/authorize/workspace-one"
    assert client.get(url, cookies=cookies).status_code == 204
    with Session() as db:
        grant = db.query(UserInstanceGrant).join(
            ApplicationInstance, UserInstanceGrant.instance_id == ApplicationInstance.id
        ).filter(ApplicationInstance.instance_key == "workspace-one").first()
        grant.enabled = False
        db.commit()
    assert client.get(url, cookies=cookies).status_code == 403
