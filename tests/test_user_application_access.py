"""Additive N:M schema tests; run against a disposable in-memory database."""
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-only-not-a-production-secret")
os.environ.setdefault("CSRF_KEY", "test-only-not-a-production-csrf-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, Application, User, UserApplicationAccess


class UserApplicationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        @event.listens_for(self.engine, "connect")
        def enforce_foreign_keys(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def user(self, username):
        return User(
            username=username,
            email=f"{username}@example.invalid",
            password_hash="test-hash",
            account_level="Free",
        )

    def test_only_adds_tables_without_modifying_users_columns(self):
        columns = {column["name"] for column in inspect(self.engine).get_columns("users")}
        self.assertEqual(
            columns,
            {"id", "username", "password_hash", "email",
             "created_at", "account_level", "last_activity"},
        )
        self.assertIn("applications", inspect(self.engine).get_table_names())
        self.assertIn("user_application_access", inspect(self.engine).get_table_names())

    def test_many_to_many_permissions_and_queries(self):
        with Session(self.engine) as db:
            alice, bob = self.user("alice"), self.user("bob")
            app_a, app_b = Application(id="research", name="Research"), Application(id="dashboard", name="Dashboard")
            db.add_all([alice, bob, app_a, app_b])
            db.flush()
            db.add_all([
                UserApplicationAccess(user_id=alice.id, application_id=app_a.id),
                UserApplicationAccess(user_id=alice.id, application_id=app_b.id),
                UserApplicationAccess(user_id=bob.id, application_id=app_a.id),
            ])
            db.commit()
            self.assertEqual(
                set(db.scalars(select(UserApplicationAccess.application_id).where(
                    UserApplicationAccess.user_id == alice.id)).all()),
                {"research", "dashboard"},
            )
            self.assertEqual(
                set(db.scalars(select(UserApplicationAccess.user_id).where(
                    UserApplicationAccess.application_id == "research")).all()),
                {alice.id, bob.id},
            )

    def test_duplicate_grants_rejected(self):
        with Session(self.engine) as db:
            user = self.user("alice")
            db.add_all([user, Application(id="research", name="Research")])
            db.flush()
            db.add(UserApplicationAccess(user_id=user.id, application_id="research"))
            db.commit()
            db.add(UserApplicationAccess(user_id=user.id, application_id="research"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            self.assertEqual(db.query(UserApplicationAccess).count(), 1)

    def test_missing_user_or_application_rejected(self):
        with Session(self.engine) as db:
            db.add(UserApplicationAccess(user_id=987654, application_id="missing"))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_deleting_user_or_application_removes_only_corresponding_grants(self):
        with Session(self.engine) as db:
            alice, bob = self.user("alice"), self.user("bob")
            research = Application(id="research", name="Research")
            dashboard = Application(id="dashboard", name="Dashboard")
            db.add_all([alice, bob, research, dashboard])
            db.flush()
            aid, bid = alice.id, bob.id
            db.add_all([
                UserApplicationAccess(user_id=aid, application_id="research"),
                UserApplicationAccess(user_id=bid, application_id="research"),
                UserApplicationAccess(user_id=bid, application_id="dashboard"),
            ])
            db.commit()
            db.delete(alice)
            db.commit()
            self.assertEqual(
                {(g.user_id, g.application_id) for g in db.scalars(select(UserApplicationAccess))},
                {(bid, "research"), (bid, "dashboard")},
            )
            db.delete(research)
            db.commit()
            self.assertEqual(
                {(g.user_id, g.application_id) for g in db.scalars(select(UserApplicationAccess))},
                {(bid, "dashboard")},
            )

    def test_create_all_preserves_existing_users_and_grants(self):
        with Session(self.engine) as db:
            user = self.user("existing")
            app = Application(id="research", name="Research")
            db.add_all([user, app])
            db.flush()
            db.add(UserApplicationAccess(user_id=user.id, application_id=app.id))
            db.commit()
            original_id = user.id
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(User.id).where(User.username == "existing")), original_id)
            self.assertEqual(db.query(UserApplicationAccess).count(), 1)


if __name__ == "__main__":
    unittest.main()
