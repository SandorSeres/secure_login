"""Profile application lists follow user grants; no unassigned apps leak."""
import os
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-profile-only-key")
os.environ.setdefault("CSRF_KEY", "test-profile-only-csrf")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, User, Application, ApplicationTarget, UserApplicationAccess
from app.profile_apps import applications_for_user


class ProfileApplicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.alice = User(username="alice", email="alice@example.invalid",
                          password_hash="x", account_level="Pro")
        self.bob = User(username="bob", email="bob@example.invalid",
                        password_hash="x", account_level="Free")
        self.db.add_all([self.alice, self.bob,
                         Application(id="dashboard", name="AI-Dashboard"),
                         Application(id="research", name="Research"),
                         Application(id="pending", name="Pending")])
        self.db.flush()
        self.db.add_all([
            ApplicationTarget(application_id="dashboard", public_url="https://research.code.hu",
                              upstream_origin="http://dashboard:8000", docker_network="dash_net"),
            ApplicationTarget(application_id="research",
                              public_url="https://research.code.hu/apps/research",
                              upstream_origin="http://research:8205", docker_network="research_net"),
            UserApplicationAccess(user_id=self.alice.id, application_id="dashboard"),
            UserApplicationAccess(user_id=self.alice.id, application_id="pending"),
            UserApplicationAccess(user_id=self.bob.id, application_id="research"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_only_own_grants_and_unconfigured_ones_not_launchable(self):
        result = applications_for_user(self.db, self.alice.id)
        self.assertEqual([a["id"] for a in result], ["dashboard", "pending"])
        self.assertTrue(result[0]["launchable"])
        self.assertFalse(result[1]["launchable"])
        self.assertNotIn("research", [a["id"] for a in result])

    def test_grant_revocation_reflected_immediately(self):
        self.assertEqual(len(applications_for_user(self.db, self.alice.id)), 2)
        self.db.query(UserApplicationAccess).filter(
            UserApplicationAccess.user_id == self.alice.id,
            UserApplicationAccess.application_id == "dashboard",
        ).delete()
        self.db.commit()
        self.assertEqual([a["id"] for a in applications_for_user(self.db, self.alice.id)],
                         ["pending"])

    def test_template_has_only_authorized_launch_links_and_empty_state(self):
        templates = Environment(
            loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] /
                                         "api/app/templates")),
            autoescape=select_autoescape(["html"]),
        )
        template = templates.get_template("profile.html")
        rendered = template.render(username="alice", account_level="Pro",
                                   csrf_token="test-csrf",
                                   applications=applications_for_user(self.db, self.alice.id))
        self.assertIn("/applications/dashboard/launch", rendered)
        self.assertNotIn("/applications/research/launch", rendered)
        self.assertNotIn("/applications/pending/launch", rendered)
        self.assertIn("Alkalmazásaim", rendered)
        empty = template.render(username="new", account_level="Free",
                                csrf_token="test-csrf", applications=[])
        self.assertIn("nincs hozzád rendelt alkalmazás", empty)
        self.assertNotIn("/applications/dashboard/launch", empty)


if __name__ == "__main__":
    unittest.main()
