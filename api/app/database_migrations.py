from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum
from sqlalchemy.engine import Engine


def migrate_account_level_to_string(engine: Engine) -> bool:
    """Convert the legacy PostgreSQL account-level enum without losing data."""
    if engine.dialect.name != "postgresql":
        return False

    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return False

    account_level_column = next(
        (
            column
            for column in inspector.get_columns("users")
            if column["name"] == "account_level"
        ),
        None,
    )
    if account_level_column is None or not isinstance(
        account_level_column["type"], PostgreSQLEnum
    ):
        return False

    # The identifiers are fixed application schema names, not user input.
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE users ALTER COLUMN account_level DROP DEFAULT")
        )
        connection.execute(
            text(
                "ALTER TABLE users ALTER COLUMN account_level "
                "TYPE VARCHAR(63) USING account_level::text"
            )
        )

    return True


def sync_access_groups(engine: Engine, group_table, groups) -> None:
    """Store the validated group configuration for local administration tools."""
    rows = [
        {
            "id": group.id,
            "name": group.name,
            "is_default": group.default,
            "sort_order": sort_order,
        }
        for sort_order, group in enumerate(groups)
    ]

    with engine.begin() as connection:
        connection.execute(group_table.delete())
        connection.execute(group_table.insert(), rows)
