"""Focused cross-host authorization tests, using disposable SQLite and fake Redis."""
import fnmatch
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
from app import session_cookie as gateway_session_cookie
from app.session_cookie import sweep_app_cookies
from app import session_store
import app.session_cookie as gateway_session_module
from app.session_store import new_scope_id
from app.security import create_session_value


class FakeRedis:
    def __init__(self):
        self.store = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def setex(self, key, seconds, value):
        assert seconds > 0
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
        # Redis glob, not a prefix: * must not cross a key's own separators.
        return iter([key for key in self.store
                     if fnmatch.fnmatchcase(key, match or "*")])


def request(*, host="research.code.hu", cookie=None, method="GET", origin=None,
            upgrade=None, forward_session_cookie=False, cookies=()):
    cookies = list(cookies) + ([cookie] if cookie else [])
    headers = [(b"host", host.encode()), (b"x-gateway-app", b"dashboard")]
    # Every cookie of one request shares a header; Starlette has no way to
    # read one that arrived on a header of its own.
    for value in cookies:
        headers.append((b"cookie", value if isinstance(value, bytes) else value.encode()))
    if method:
        headers.append((b"x-forwarded-method", method.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    if upgrade:
        headers.append((b"x-original-upgrade", upgrade.encode()))
    # Redeeming through the identity host also carries the Secure Login
    # session cookie; it must never be forwarded to an application upstream.
    if forward_session_cookie:
        headers.append((b"cookie", "session=" + create_session_value("alice", "jti-leak-test")))
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": headers, "query_string": b"",
    })


def request_with_app(app, *, cookie=None, path="/"):
    """A request as the site of a prefixed application receives it.

    /apps/<id> is part of the address the proxy routed to, and its block puts
    the application id in the gateway header; the path it forwards has already
    had the prefix stripped.
    """
    headers = [(b"host", b"research.code.hu"), (b"x-gateway-app", app.encode()),
               (b"x-forwarded-method", b"GET")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "method": "GET", "path": path,
                    "query_string": b"", "headers": headers})


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
                public_url="https://research.code.hu",
                upstream_origin="http://dashboard:8000",
                docker_network=None,
            ),
        ])
        self.db.commit()
        self.cache = FakeRedis()
        self.scope = new_scope_id()
        # A real session writes a scope record whose TTL is the TTL of the
        # session. That record is what a scoped cookie depends on, so it lives
        # in the same fake keyspace as everything else.
        # Every module reaches Redis through redis_client.get_client, so one
        # patch points the gateway, the revocation helpers and the session
        # records at the same fake keyspace.
        self.redis_patch = patch("app.redis_client.get_client", return_value=self.cache)
        self.redis_patch.start()

    def tearDown(self):
        self.redis_patch.stop()
        self.db.close()
        self.engine.dispose()

    def grant(self):
        self.db.add(UserApplicationAccess(user_id=self.user.id, application_id="dashboard"))
        self.db.commit()

    def launch_ticket(self, app_id="dashboard", scope=None, live_session=True):
        """Ticket for a launch, exactly as a browser would get one.

        The scope comes from the session cookie of the session that launched
        it, so a live scope means a registered session. Without one -- a
        session record that already ended -- launch cannot name a scope and
        the cookie it leads to lands in the unscoped bucket.
        """
        if live_session and scope:
            jti = new_scope_id()
            session_store.register_session("alice", jti, scope)
        else:
            # A session whose scope record is already gone: launch cannot name
            # a scope, so the cookie its ticket leads to is unscoped.
            jti = session_store.new_scope_id()
        response = gateway.launch(
            app_id, request(cookies=["session=" + create_session_value("alice", jti)]),
            user=self.user, db=self.db)
        self.assertEqual(response.status_code, 303)
        return parse_qs(urlsplit(response.headers["location"]).query)["ticket"][0]

    def ticket(self, scope=None, live_session=True, app_id="dashboard"):
        """Ticket bound to a live scope; scope="" mimics a lost session record."""
        return self.launch_ticket(app_id=app_id,
                                  scope=self.scope if scope is None else scope,
                                  live_session=live_session)

    def test_named_app_ticket_cannot_authorize_root_dashboard(self):
        self.grant()
        self.db.add(Application(id="research", name="Research"))
        self.db.add(ApplicationTarget(
            application_id="research",
            public_url="https://research.code.hu/apps/research",
            upstream_origin="http://research:8205",
            docker_network=None,
        ))
        self.db.flush()
        self.db.add(UserApplicationAccess(user_id=self.user.id, application_id="research"))
        self.db.commit()

        redirect = gateway.launch("research", request(), user=self.user, db=self.db)
        self.assertIn("/apps/research/_secure-login/redeem", redirect.headers["location"])
        ticket = parse_qs(urlsplit(redirect.headers["location"]).query)["ticket"][0]
        response = gateway.redeem(
            request_with_app("research", path="/apps/research/_secure-login/redeem"),
            ticket=ticket, db=self.db,
        )
        self.assertEqual(response.headers["location"], "/apps/research/")
        research_cookie = response.headers["set-cookie"].split(";", 1)[0]
        self.assertEqual(
            gateway.authorize(request_with_app("research", cookie=research_cookie), db=self.db).status_code,
            204,
        )
        with self.assertRaises(HTTPException) as failure:
            gateway.authorize(request(cookie=research_cookie), db=self.db)
        self.assertEqual(failure.exception.status_code, 401)

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
                request(cookie=cookie, method="POST", origin="https://research.code.hu"),
                db=self.db,
            ).status_code,
            204,
        )

    def test_websocket_upgrade_requires_same_origin(self):
        self.grant()
        response = gateway.redeem(request(), ticket=self.ticket(), db=self.db)
        cookie = response.headers["set-cookie"].split(";", 1)[0]
        with self.assertRaises(HTTPException) as error:
            gateway.authorize(
                request(cookie=cookie, upgrade="websocket", origin="https://evil.example.com"),
                db=self.db,
            )
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(
            gateway.authorize(
                request(cookie=cookie, upgrade="websocket", origin="https://research.code.hu"),
                db=self.db,
            ).status_code,
            204,
        )

    def issued(self, scope=None, live_session=True):
        """Redeemed app cookie for a session bound to the given scope.

        A scope with no live record is the unscoped case: redeem cannot tell a
        stale handle from a forged one, so it must not bind the cookie to it.
        """
        response = gateway.redeem(
            request(), ticket=self.ticket(scope=scope, live_session=live_session),
            db=self.db)
        return response.headers["set-cookie"].split(";", 1)[0]

    def scope_record(self, scope):
        return session_store.scope_record_key(scope)

    def assertAuthorized(self, cookie):
        self.assertEqual(
            gateway.authorize(request(cookie=cookie), db=self.db).status_code, 204)

    def assertDenied(self, cookie):
        with self.assertRaises(HTTPException) as failure:
            gateway.authorize(request(cookie=cookie), db=self.db)
        self.assertEqual(failure.exception.status_code, 401)

    def test_logout_denies_the_app_cookies_that_session_issued(self):
        self.grant()
        cookie = self.issued(scope=self.scope)
        self.assertAuthorized(cookie)
        sweep_app_cookies(self.scope)
        self.assertDenied(cookie)

    def test_another_sessions_app_cookie_survives_a_logout(self):
        self.grant()
        mine_cookie = self.issued(scope=self.scope)
        other_scope = new_scope_id()
        other_cookie = self.issued(scope=other_scope)
        self.assertAuthorized(mine_cookie)
        self.assertAuthorized(other_cookie)
        sweep_app_cookies(self.scope)
        self.assertDenied(mine_cookie)
        self.assertAuthorized(other_cookie)

    def test_app_cookie_stops_working_when_its_session_dies(self):
        # Ending the session is enough on its own: an application cookie is
        # bound to the login that issued it and cannot outlive it.
        self.grant()
        cookie = self.issued(scope=self.scope)
        self.assertAuthorized(cookie)
        session_store.revoke_all_sessions("alice")
        self.assertDenied(cookie)

    def test_a_cookie_whose_scope_record_expired_is_denied(self):
        # The session ended and its binding record went with it. What is left
        # is a signed cookie and a stray record, which must not be enough.
        self.grant()
        cookie = self.issued(scope=self.scope)
        self.assertAuthorized(cookie)
        self.cache.store.pop(session_store.scope_record_key(self.scope), None)
        self.assertDenied(cookie)

    def test_revocation_without_a_scope_ends_every_live_session(self):
        # An administrative action cannot know the sessions involved, so it
        # ends every live scope and denies the cookies left without one.
        self.grant()
        live_cookie = self.issued(scope=self.scope)
        sessionless_cookie = self.issued(live_session=False)
        self.assertAuthorized(live_cookie)
        self.assertAuthorized(sessionless_cookie)
        sweep_app_cookies(None)
        self.assertDenied(live_cookie)
        self.assertDenied(sessionless_cookie)

    def test_a_scopeless_sweep_denies_an_abandoned_cookie(self):
        # A cookie whose session ended leaves a record behind and can no
        # longer be attributed to anybody, so the sweep must deny it rather
        # than let it go on working on its signature alone.
        self.grant()
        abandoned = self.issued(scope=new_scope_id())
        self.assertAuthorized(abandoned)
        self.cache.store.pop(session_store.scope_record_key(
            gateway._signer.loads(abandoned.split("=", 1)[1])["scope"]), None)
        sweep_app_cookies(None)
        self.assertDenied(abandoned)

    def test_identity_session_cookie_is_never_a_binding_source(self):
        # The session cookie must never be read as a binding source, because
        # Caddy strips it before proxying: an upstream could otherwise learn a
        # revocation handle it can never be trusted to keep. The scope travels
        # in the ticket payload, which no client or upstream ever sees.
        self.grant()
        response = gateway.redeem(
            request(forward_session_cookie=True),
            ticket=self.ticket(live_session=False), db=self.db)
        cookie = response.headers["set-cookie"].split(";", 1)[0]
        self.assertEqual(
            gateway.authorize(request(cookie=cookie), db=self.db).status_code, 204)
        # Nothing in the keyspace may be keyed by, or point at, a jti that
        # arrived in a request header.
        self.assertEqual(
            [key for key in self.cache.store
             if "jti-leak-test" in key
             or str(self.cache.store[key]).endswith("jti-leak-test")],
            [])
        # Redeemed on the application host, where the session cookie is
        # stripped: this cookie is deliberately not bound to a session.
        self.assertEqual(gateway._signer.loads(cookie.split("=", 1)[1])["scope"],
                         gateway_session_cookie.UNSCOPED)

    def test_redeem_names_its_application_from_the_host_alone(self):
        # The client reaches redeem on the application host with no gateway
        # header, and the cookie-less authorize subrequest never stamps one
        # for it either, so Host and path decide. A host with no configured
        # site may never name one.
        self.grant()
        for header in ({}, {"x-gateway-app": "dashboard"}):
            req = request(host="research.code.hu")
            req.scope["headers"] = [(k, v) for k, v in req.scope["headers"]
                                    if k != b"x-gateway-app"]
            req.scope["headers"] += [(k, v.encode()) for k, v in header.items()]
            self.assertEqual(gateway.redeem(req, ticket=self.ticket(), db=self.db).status_code, 303)

        attacker = request(host="attacker.example.com")
        attacker.scope["headers"] = [(k, v) for k, v in attacker.scope["headers"]
                                     if k != b"x-gateway-app"]
        with self.assertRaises(HTTPException) as failure:
            gateway.redeem(attacker, ticket=self.ticket(), db=self.db)
        self.assertEqual(failure.exception.status_code, 403)

    def test_a_forged_gateway_header_cannot_reach_another_application(self):
        # research.code.hu is the root site for dashboard and also hosts
        # /apps/research. A request for the root site may not name research,
        # because that is how one app would mint another's cookie.
        self.grant()
        self.db.add(Application(id="research", name="Research"))
        self.db.add(ApplicationTarget(
            application_id="research",
            public_url="https://research.code.hu/apps/research",
            upstream_origin="http://research:8205", docker_network=None))
        self.db.commit()

        req = request(host="research.code.hu")
        req.scope["headers"] = [(k, b"research" if k == b"x-gateway-app" else v)
                                for k, v in req.scope["headers"]]
        self.assertEqual(gateway._gateway_app(req, self.db), "")
        # A ticket minted for research cannot be redeemed at the root site.
        self.db.add(UserApplicationAccess(user_id=self.user.id, application_id="research"))
        self.db.commit()
        with self.assertRaises(HTTPException) as failure:
            gateway.redeem(req, ticket=self.ticket(app_id="research"), db=self.db)
        self.assertEqual(failure.exception.status_code, 403)

        # The real route for research does work, header and all.
        prefixed = Request({
            "type": "http", "method": "GET",
            "path": "/apps/research/_secure-login/redeem", "query_string": b"",
            "headers": [(b"host", b"research.code.hu"), (b"x-gateway-app", b"research")],
        })
        self.assertEqual(
            gateway.redeem(prefixed, ticket=self.ticket(app_id="research"),
                           db=self.db).status_code, 303)

    def test_redeem_on_the_identity_host_is_refused(self):
        # An unconfigured Host may not borrow an application id from a header,
        # because that is how one host would mint cookies for another.
        self.grant()
        req = Request({
            "type": "http", "method": "GET", "path": "/_secure-login/redeem",
            "query_string": b"", "headers": [
                (b"host", b"login.code.hu"), (b"x-gateway-app", b"dashboard")],
        })
        with self.assertRaises(HTTPException) as failure:
            gateway.redeem(req, ticket=self.ticket(), db=self.db)
        self.assertEqual(failure.exception.status_code, 403)

    def test_request_without_gateway_method_header_is_not_trusted(self):
        self.grant()
        response = gateway.redeem(request(), ticket=self.ticket(), db=self.db)
        cookie = response.headers["set-cookie"].split(";", 1)[0]
        req = request(cookie=cookie)
        req.scope["headers"] = [(k, v) for k, v in req.scope["headers"]
                                if k != b"x-forwarded-method"]
        with self.assertRaises(HTTPException) as failure:
            gateway.authorize(req, db=self.db)
        self.assertEqual(failure.exception.status_code, 403)

    def test_revoked_app_cookie_is_denied_before_the_grant_lookup(self):
        self.grant()
        response = gateway.redeem(request(), ticket=self.ticket(), db=self.db)
        cookie = response.headers["set-cookie"].split(";", 1)[0]
        self.assertEqual(gateway.authorize(request(cookie=cookie), db=self.db).status_code, 204)
        cookie_id = gateway._signer.loads(cookie.split("=", 1)[1])["cookie_id"]
        gateway_session_cookie._cache().setex(
            gateway_session_cookie.revocation_key(cookie_id), 60, "1")
        with self.assertRaises(HTTPException) as failure:
            gateway.authorize(request(cookie=cookie), db=self.db)
        self.assertEqual(failure.exception.status_code, 401)

    def test_unauthenticated_access_rejected(self):
        with self.assertRaises(HTTPException) as error:
            gateway.authorize(request(), db=self.db)
        self.assertEqual(error.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
