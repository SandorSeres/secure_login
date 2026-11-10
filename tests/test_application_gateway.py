"""Focused cross-host authorization tests, using disposable SQLite and fake Redis."""
import os
import unittest
from contextlib import contextmanager
from http.cookies import SimpleCookie
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("SECRET_KEY", "test-only-app-gateway-signing")
os.environ.setdefault("CSRF_KEY", "test-only-app-gateway-csrf")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.models import Base, Application, ApplicationTarget, User, UserApplicationAccess
from app import application_access as gateway


class FakeRedis:
    def __init__(self):
        self.store = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def setex(self, key, seconds, value):
        assert seconds == 60
        self.store[key] = value

    def getdel(self, key):
        return self.store.pop(key, None)


def request(*, host="dashboard.example.com", cookie=None, method="GET", origin=None):
    headers = [(b"host", host.encode()), (b"x-gateway-app", b"dashboard")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if method:
        headers.append((b"x-forwarded-method", method.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": headers, "query_string": b"",
    })


class GatewayAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        @event.listens_for(self.engine, "connect")
        def enable_fk(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(
            username="alice", email="alice@example.invalid",
            password_hash="test-hash", account_level="Free",
        )
        self.db.add_all([
            self.user,
            Application(id="dashboard", name="Dashboard"),
            ApplicationTarget(
                application_id="dashboard",
                public_url="https://dashboard.example.com",
                upstream_origin="http://dashboard:8000",
                docker_network=None,
            ),
        ])
        self.db.commit()
        self.cache = FakeRedis()
        self.redis_patch = patch.object(gateway, "_cache", return_value=self.cache)
        self.redis_patch.start()

    def tearDown(self):
        self.redis_patch.stop()
        self.db.close()
        self.engine.dispose()

    def grant(self):
        self.db.add(UserApplicationAccess(user_id=self.user.id, application_id="dashboard"))
        self.db.commit()

    def ticket(self):
        response = gateway.launch("dashboard", user=self.user, db=self.db)
        self.assertEqual(response.status_code, 303)
        return parse_qs(urlsplit(response.headers["location"]).query)["ticket"][0]

    def test_no_grant_is_denied(self):
        with self.assertRaises(HTTPException) as error:
            self.ticket()
        self.assertEqual(error.exception.status_code, 403)

    def test_one_time_ticket_and_revocation(self):
        self.grant()
        ticket = self.ticket()
        response = gateway.redeem(request(), ticket=ticket, db=self.db)
        self.assertEqual(response.status_code, 303)
        set_cookie = response.headers["set-cookie"]
        self.assertIn("Secure", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Path=/", set_cookie)
        cookie = set_cookie.split(";", 1)[0]
        self.assertEqual(
            gateway.authorize(request(cookie=cookie), db=self.db).status_code,
            204,
        )
        self.db.query(UserApplicationAccess).delete()
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            gateway.authorize(request(cookie=cookie), db=self.db)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as error:
            gateway.redeem(request(), ticket=ticket, db=self.db)
        self.assertEqual(error.exception.status_code, 403)

    def test_wrong_host_rejected_without_spending_ticket(self):
        self.grant()
        ticket = self.ticket()
        with self.assertRaises(HTTPException) as error:
            gateway.redeem(request(host="attacker.example.com"), ticket=ticket, db=self.db)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(gateway.redeem(request(), ticket=ticket, db=self.db).status_code, 303)

    def test_cross_origin_browser_writes_rejected(self):
        self.grant()
        response = gateway.redeem(request(), ticket=self.ticket(), db=self.db)
        cookie = response.headers["set-cookie"].split(";", 1)[0]
        with self.assertRaises(HTTPException) as error:
            gateway.authorize(request(cookie=cookie, method="POST"), db=self.db)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(
            gateway.authorize(
                request(cookie=cookie, method="POST", origin="https://dashboard.example.com"),
                db=self.db,
            ).status_code,
            204,
        )

    def test_unauthenticated_access_rejected(self):
        with self.assertRaises(HTTPException) as error:
            gateway.authorize(request(), db=self.db)
        self.assertEqual(error.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
