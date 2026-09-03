import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum

from app.database_migrations import migrate_account_level_to_string, sync_access_groups


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_postgresql_enum_is_converted_to_string(self):
        connection = Mock()
        engine = Mock()
        engine.dialect.name = "postgresql"

        @contextmanager
        def transaction():
            yield connection

        engine.begin.side_effect = transaction
        inspector = Mock()
        inspector.has_table.return_value = True
        inspector.get_columns.return_value = [
            {
                "name": "account_level",
                "type": PostgreSQLEnum("Free", "Pro", name="account_level_enum"),
            }
        ]

        with patch("app.database_migrations.inspect", return_value=inspector):
            changed = migrate_account_level_to_string(engine)

        self.assertTrue(changed)
        self.assertEqual(connection.execute.call_count, 2)
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertIn("DROP DEFAULT", statements[0])
        self.assertIn("TYPE VARCHAR(63)", statements[1])

    def test_non_postgresql_database_is_not_modified(self):
        engine = Mock()
        engine.dialect.name = "sqlite"

        changed = migrate_account_level_to_string(engine)

        self.assertFalse(changed)
        engine.begin.assert_not_called()

    def test_configured_groups_are_replaced_transactionally(self):
        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        groups = Table(
            "access_groups",
            metadata,
            Column("id", String(63), primary_key=True),
            Column("name", String(100), nullable=False),
            Column("is_default", Boolean, nullable=False),
            Column("sort_order", Integer, nullable=False),
        )
        metadata.create_all(engine)

        sync_access_groups(
            engine,
            groups,
            [
                SimpleNamespace(id="free", name="Free", default=True),
                SimpleNamespace(id="pro", name="Pro", default=False),
            ],
        )
        sync_access_groups(
            engine,
            groups,
            [
                SimpleNamespace(id="free", name="Free", default=True),
                SimpleNamespace(id="travel", name="Travel", default=False),
            ],
        )

        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    groups.c.id,
                    groups.c.name,
                    groups.c.is_default,
                    groups.c.sort_order,
                ).order_by(groups.c.sort_order)
            ).fetchall()

        self.assertEqual(
            [tuple(row) for row in rows],
            [("free", "Free", True, 0), ("travel", "Travel", False, 1)],
        )


if __name__ == "__main__":
    unittest.main()
