"""PostgreSQL 1:1 -> M:N grants migration, tested only on disposable CI database."""
import os
import unittest
from sqlalchemy import create_engine, text
from app.multi_portal_migration import migrate, assert_schema_ready

URL = os.environ.get("REM_TEST_PG_URL")


@unittest.skipUnless(URL, "Requires disposable REM_TEST_PG_URL database")
class PostgresMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(URL, pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS user_portal_access"))
            c.execute(text("""
                CREATE TABLE user_portal_access (
                    user_id INTEGER NOT NULL PRIMARY KEY,
                    portal_id VARCHAR(63) NOT NULL,
                    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_user_portal_access_portal_id UNIQUE (portal_id)
                )
            """))
            c.execute(text("INSERT INTO user_portal_access(user_id, portal_id) VALUES (1, 'first')"))

    def tearDown(self):
        with self.engine.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS user_portal_access"))

    def test_dry_run_and_explicit_migration_preserve_grants(self):
        self.assertEqual(migrate(self.engine), "ready-for-explicit-migration")
        with self.assertRaises(RuntimeError):
            assert_schema_ready(self.engine)
        self.assertEqual(migrate(self.engine, execute=True), "migrated")
        self.assertEqual(migrate(self.engine, execute=True), "already-migrated")
        assert_schema_ready(self.engine)
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO user_portal_access(user_id, portal_id) VALUES (1, 'second'), (2, 'first')"))
            rows = c.execute(text("SELECT user_id, portal_id FROM user_portal_access ORDER BY user_id, portal_id")).all()
        self.assertEqual(rows, [(1, "first"), (1, "second"), (2, "first")])

    def test_unexpected_constraint_refuses_migration(self):
        with self.engine.begin() as c:
            c.execute(text("ALTER TABLE user_portal_access ADD CONSTRAINT unexpected UNIQUE (assigned_at)"))
        with self.assertRaises(ValueError):
            migrate(self.engine, execute=True)


if __name__ == "__main__":
    unittest.main()
