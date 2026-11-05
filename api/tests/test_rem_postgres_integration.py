"""PostgreSQL-backed HTTP integration against an ephemeral CI-only database.

Never pointed at production. The GitHub Actions job starts disposable PostgreSQL.
"""
import os
os.environ.setdefault("SECRET_KEY", "ci-secret-" + "s"*48)
os.environ.setdefault("CSRF_KEY", "ci-csrf-" + "c"*48)
os.environ.setdefault("ROLES_JSON", '[{"id":"user","name":"User","default":true}]')
os.environ.setdefault("ENABLE_MULTI_PORTAL", "true")
os.environ.setdefault("REM_PROVISIONING_TOKEN", "ci-only-" + "t"*48)
os.environ.setdefault("PORTALS_JSON", "[]")

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.models import SessionLocal, User, UserPortalAccess
from app.deps import current_user
from app.multi_portal_migration import assert_schema_ready
from app.models import engine

def test_postgres_provisioning_verify_and_revoke():
    assert engine.dialect.name == "postgresql", "Requires disposable PostgreSQL"
    assert_schema_ready(engine)
    with SessionLocal() as db:
        user = User(username="ci-research-user", email="ci-research-user@example.invalid",
                    password_hash="ci-not-a-real-password", role_id="user")
        db.add(user)
        db.commit()
        user_id = user.id
        # Authentication is covered separately; inject a known synthetic user to
        # exercise the real grant-aware /auth/verify endpoint and DB persistence.
        app.dependency_overrides[current_user] = lambda: user
        try:
            with TestClient(app) as client:
                token = {"Authorization": "Bearer " + settings.REM_PROVISIONING_TOKEN}
                url = "/internal/rem/instances"
                portal = {"id": "ci-research", "name": "CI Research",
                          "url": "https://research.example.invalid/apps/ci-research/",
                          "upstream": "127.0.0.1:8205", "enabled": True}
                assert client.post(url, json=portal).status_code == 401
                response = client.post(url, json=portal, headers=token)
                assert response.status_code == 200, response.text
                assert response.json()["result"] == "created"
                assert client.get("/auth/verify?portal_id=ci-research").status_code == 403
                grant = url + "/ci-research/grants"
                response = client.post(grant, headers=token, json={"user_id": user_id})
                assert response.status_code == 200, response.text
                assert client.get("/auth/verify?portal_id=ci-research").status_code == 204
                assert client.get("/auth/verify?portal_id=unknown").status_code == 403
                assert client.delete(grant + "/" + str(user_id), headers=token).status_code == 200
                assert client.get("/auth/verify?portal_id=ci-research").status_code == 403
                db.expire_all()
                assert db.get(UserPortalAccess, (user_id, "ci-research")) is None
        finally:
            app.dependency_overrides.pop(current_user, None)
