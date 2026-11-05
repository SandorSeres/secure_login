from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum
from sqlalchemy.engine import Engine


def migrate_legacy_access_schema(engine: Engine) -> bool:
    """Migrate the pre-role PostgreSQL schema in place without losing users."""
    if engine.dialect.name != "postgresql":
        return False

    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return False

    columns = {column["name"]: column for column in inspector.get_columns("users")}
    statements: list[str] = []

    legacy_role = columns.get("account_level")
    if legacy_role is not None and "role_id" not in columns:
        statements.append("ALTER TABLE users ALTER COLUMN account_level DROP DEFAULT")
        if isinstance(legacy_role["type"], PostgreSQLEnum):
            statements.append(
                "ALTER TABLE users ALTER COLUMN account_level "
                "TYPE VARCHAR(63) USING account_level::text"
            )
        statements.append("ALTER TABLE users RENAME COLUMN account_level TO role_id")

    for column_name in ("created_at", "last_activity"):
        column = columns.get(column_name)
        if column is None:
            continue
        if not getattr(column["type"], "timezone", False):
            statements.append(
                f"ALTER TABLE users ALTER COLUMN {column_name} "
                f"TYPE TIMESTAMPTZ USING {column_name} AT TIME ZONE 'UTC'"
            )
        if not column.get("default"):
            statements.append(
                f"ALTER TABLE users ALTER COLUMN {column_name} "
                "SET DEFAULT CURRENT_TIMESTAMP"
            )

    if inspector.has_table("access_groups") and not inspector.has_table("roles"):
        statements.append("ALTER TABLE access_groups RENAME TO roles")

    if not statements:
        return False

    # All identifiers above are fixed application schema names, not user input.
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

    return True


def ensure_user_role_foreign_key(engine: Engine) -> bool:
    """Add the role foreign key missing from legacy PostgreSQL databases."""
    if engine.dialect.name != "postgresql":
        return False

    inspector = inspect(engine)
    role_foreign_key_exists = any(
        foreign_key.get("constrained_columns") == ["role_id"]
        and foreign_key.get("referred_table") == "roles"
        and foreign_key.get("referred_columns") == ["id"]
        for foreign_key in inspector.get_foreign_keys("users")
    )
    if role_foreign_key_exists:
        return False

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE users ADD CONSTRAINT fk_users_role_id_roles "
                "FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT"
            )
        )
    return True


def _sync_configuration_rows(
    engine: Engine,
    table,
    rows: Iterable[dict[str, Any]],
    preserve_ids: set[str] | None = None,
) -> None:
    desired_rows = list(rows)
    desired_ids = {row["id"] for row in desired_rows}

    with engine.begin() as connection:
        existing_ids = set(connection.execute(select(table.c.id)).scalars())

        for row in desired_rows:
            if row["id"] in existing_ids:
                connection.execute(
                    table.update().where(table.c.id == row["id"]).values(**row)
                )
            else:
                connection.execute(table.insert().values(**row))

        removed_ids = existing_ids - desired_ids - (preserve_ids or set())
        if removed_ids:
            connection.execute(table.delete().where(table.c.id.in_(removed_ids)))


def sync_roles(engine: Engine, role_table, user_table, roles) -> None:
    """Store roles and normalize every user to a configured role."""
    role_rows = [
        {
            "id": role.id,
            "name": role.name,
            "is_default": role.default,
            "sort_order": sort_order,
        }
        for sort_order, role in enumerate(roles)
    ]
    desired_ids = {row["id"] for row in role_rows}
    default_role_id = next(role.id for role in roles if role.default)

    with engine.begin() as connection:
        existing_ids = set(connection.execute(select(role_table.c.id)).scalars())

        for row in role_rows:
            if row["id"] in existing_ids:
                connection.execute(
                    role_table.update()
                    .where(role_table.c.id == row["id"])
                    .values(**row)
                )
            else:
                connection.execute(role_table.insert().values(**row))

            # Legacy account levels were not consistently normalized.
            connection.execute(
                user_table.update()
                .where(func.lower(func.trim(user_table.c.role_id)) == row["id"])
                .values(role_id=row["id"])
            )

        connection.execute(
            user_table.update()
            .where(user_table.c.role_id.not_in(desired_ids))
            .values(role_id=default_role_id)
        )

        removed_ids = existing_ids - desired_ids
        if removed_ids:
            connection.execute(
                role_table.delete().where(role_table.c.id.in_(removed_ids))
            )


def sync_portals(engine: Engine, portal_table, portals) -> None:
    """Store validated portal metadata without resetting user assignments."""
    # Never delete server-provisioned portal rows during PORTALS_JSON synchronization.
    with engine.connect() as connection:
        managed = set(connection.execute(text('SELECT id FROM managed_portals')).scalars())
    _sync_configuration_rows(
        engine,
        portal_table,
        (
            {
                "id": portal.id,
                "name": portal.name,
                "enabled": portal.enabled,
                "sort_order": sort_order,
            }
            for sort_order, portal in enumerate(portals)
        ),
        preserve_ids=managed,
    )
