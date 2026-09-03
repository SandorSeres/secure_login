import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from fastapi import HTTPException


os.environ.update(
    {
        "SECRET_KEY": "test-secret-key",
        "CSRF_KEY": "test-csrf-key",
        "GROUPS_JSON": json.dumps(
            [{"id": "free", "name": "Free", "default": True}]
        ),
        "APPLICATIONS_JSON": "[]",
        "ADMIN_USERNAMES": "[]",
        "DATABASE_URL": "sqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379/15",
    }
)

from app import authorization
from app.config import Settings


def make_settings(groups, applications=None):
    return Settings(
        _env_file=None,
        SECRET_KEY="test-secret-key",
        CSRF_KEY="test-csrf-key",
        GROUPS_JSON=groups,
        APPLICATIONS_JSON=applications or [],
        ADMIN_USERNAMES=[],
        DATABASE_URL="sqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/15",
    )


class DynamicGroupConfigurationTests(unittest.TestCase):
    def test_new_group_controls_application_access(self):
        configured_settings = make_settings(
            groups=[
                {"id": "free", "name": "Free", "default": True},
                {"id": "travel", "name": "Travel"},
            ],
            applications=[
                {
                    "id": "travel-app",
                    "name": "Travel App",
                    "url": "https://travel.example.com",
                    "allowed_groups": ["travel"],
                }
            ],
        )

        with patch.object(authorization, "settings", configured_settings):
            allowed = authorization.applications_for_user(
                SimpleNamespace(account_level="Travel")
            )
            denied = authorization.applications_for_user(
                SimpleNamespace(account_level="free")
            )

        self.assertEqual([application.id for application in allowed], ["travel-app"])
        self.assertEqual(denied, [])

        with patch.object(authorization, "settings", configured_settings):
            with self.assertRaises(HTTPException) as denied_request:
                authorization.require_application_access(
                    SimpleNamespace(account_level="free"), "travel-app"
                )

        self.assertEqual(denied_request.exception.status_code, 403)

    def test_default_group_comes_from_configuration(self):
        configured_settings = make_settings(
            groups=[
                {"id": "basic", "name": "Basic", "default": True},
                {"id": "pro", "name": "Pro"},
            ]
        )

        self.assertEqual(configured_settings.default_group.id, "basic")

    def test_unknown_application_group_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "unknown groups: missing"):
            make_settings(
                groups=[{"id": "free", "name": "Free", "default": True}],
                applications=[
                    {
                        "id": "example",
                        "name": "Example",
                        "url": "https://example.com",
                        "allowed_groups": ["missing"],
                    }
                ],
            )

    def test_exactly_one_default_group_is_required(self):
        with self.assertRaisesRegex(ValidationError, "exactly one default group"):
            make_settings(
                groups=[
                    {"id": "free", "name": "Free"},
                    {"id": "pro", "name": "Pro"},
                ]
            )

    def test_duplicate_group_ids_are_rejected_after_normalization(self):
        with self.assertRaisesRegex(ValidationError, "duplicate group ids"):
            make_settings(
                groups=[
                    {"id": "Free", "name": "Free", "default": True},
                    {"id": "free", "name": "Duplicate"},
                ]
            )


if __name__ == "__main__":
    unittest.main()
