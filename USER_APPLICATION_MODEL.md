# User ↔ Application N:M data model

This change is **additive** to the original Secure Login baseline. It does
not alter the existing `users` columns, login, sessions, registration, account
levels, reverse proxy configuration, or any HTTP routes.

## Tables

- `applications`: `id` (string primary key), `name` (required display name).
- `user_application_access`: `user_id` FK to `users.id`,
  `application_id` FK to `applications.id`; together they form the
  composite primary key. An index on `application_id` supports reverse lookups.

The pair's uniqueness is enforced by the database, not merely application
logic. The foreign keys use `ON DELETE CASCADE`: removing a user or an
application removes its association rows. Deleting an association does not
delete the user or application.

## Rollout

The existing application startup runs `Base.metadata.create_all(bind=engine)`.
SQLAlchemy therefore creates these **two new tables** on startup if they do
not already exist. No changes to the existing `users` table are required.
However, `create_all` is not a general-purpose migration tool.

Before running a changed version against a persistent PostgreSQL database,
take and verify a backup and first test against a restored copy. Ensure the
database role has permission to create the new tables and index. If automatic
schema creation is prohibited, create only these two tables via an approved
separate DDL procedure. Never drop or recreate `users`.

## SQLAlchemy examples (trusted server-side code only)

```python
from sqlalchemy import select
from app.models import Application, UserApplicationAccess, SessionLocal

with SessionLocal() as db:
    db.add(Application(id="research", name="Research"))
    db.commit()

    # Grant a known existing user access. Duplicate grants are rejected by PK.
    db.add(UserApplicationAccess(user_id=123, application_id="research"))
    db.commit()

    # Find all application IDs assigned to this user.
    ids = db.scalars(
        select(UserApplicationAccess.application_id).where(
            UserApplicationAccess.user_id == 123
        )
    ).all()

    # Revoke only this association.
    grant = db.get(UserApplicationAccess, (123, "research"))
    if grant is not None:
        db.delete(grant)
        db.commit()
```

**Security boundary:** These tables only **store** associations. They do
not implement authorization or expose any endpoint for managing grants.
In particular, simply having a row does **not** make an application
accessible through Secure Login. Enforcement happens in the opt-in
application gateway (`docs/APP_GATEWAY.md`) and in the read-only profile
listing (`api/app/profile_apps.py`).

## Verification

```bash
PYTHONPATH=api SECRET_KEY=test-only-key CSRF_KEY=test-only-csrf \
  DATABASE_URL=sqlite:// \
  python -m unittest discover -s tests -p 'test_user_application_access.py' -v
```

The tests use a disposable in-memory SQLite engine, with foreign key checks
explicitly enabled, and do not touch the deployed database. A separate
PostgreSQL integration test is recommended before production deployment.
