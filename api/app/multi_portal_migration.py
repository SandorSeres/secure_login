"""Explicit, guarded PostgreSQL schema migration from one-to-one to many-to-many.

Never invoked on app startup. Take a tested backup and perform on a clone first.
"""
from __future__ import annotations

import argparse
import json
from sqlalchemy import create_engine, text


def inspect_constraints(connection):
    return dict(connection.execute(text("""
        SELECT con.conname, con.contype
          FROM pg_constraint con
          JOIN pg_class tbl ON tbl.oid=con.conrelid
          JOIN pg_namespace ns ON ns.oid=tbl.relnamespace
         WHERE ns.nspname=current_schema()
           AND tbl.relname='user_portal_access'
           AND con.contype IN ('p','u')
    """)).fetchall())


def assert_schema_ready(engine):
    """Refuse enabling multi-portal against a still-exclusive legacy schema."""
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as conn:
        if inspect_constraints(conn) != {"user_portal_access_pkey": "p"}:  # no unique portal restriction
            raise RuntimeError("Multi-portal schema migration required before enable")
        keys = conn.execute(text("""
            SELECT array_agg(att.attname ORDER BY idx.ordinality)
            FROM pg_constraint co
            JOIN pg_class tbl ON tbl.oid=co.conrelid
            JOIN pg_namespace ns ON ns.oid=tbl.relnamespace
            JOIN unnest(co.conkey) WITH ORDINALITY idx(attnum, ordinality) ON TRUE
            JOIN pg_attribute att ON att.attrelid=tbl.oid AND att.attnum=idx.attnum
            WHERE ns.nspname=current_schema() AND tbl.relname='user_portal_access' AND co.contype='p'
        """)).scalar()
        if keys != ["user_id", "portal_id"]:
            raise RuntimeError("Unexpected multi-portal primary key")

def migrate(engine, *, execute=False):
    if engine.dialect.name != 'postgresql':
        raise ValueError('PostgreSQL-only migration')
    with engine.begin() as conn:
        # Serialize concurrent migrations, with transaction-scoped lock.
        conn.execute(text('SELECT pg_advisory_xact_lock(18723491)'))
        constraints = inspect_constraints(conn)
        expected = {'user_portal_access_pkey': 'p',
                    'uq_user_portal_access_portal_id': 'u'}
        # May already be migrated; do not perform blind destructive DDL.
        if constraints == {'user_portal_access_pkey': 'p'}:
            # PK layout must be double checked before treating as migrated.
            keys = conn.execute(text("""
                SELECT array_agg(att.attname ORDER BY idx.ordinality)
                FROM pg_constraint co
                JOIN pg_class tbl ON tbl.oid=co.conrelid
                JOIN pg_namespace ns ON ns.oid=tbl.relnamespace
                JOIN unnest(co.conkey) WITH ORDINALITY idx(attnum, ordinality) ON TRUE
                JOIN pg_attribute att ON att.attrelid=tbl.oid AND att.attnum=idx.attnum
                WHERE ns.nspname=current_schema() AND tbl.relname='user_portal_access'
                  AND co.contype='p'
            """)).scalar()
            if keys == ['user_id', 'portal_id']:
                return 'already-migrated'
        if constraints != expected:
            raise ValueError('Unexpected user_portal_access constraints; manual review required')
        # Do not execute if unexplained duplicate user/portal rows exist.
        if not execute:
            return 'ready-for-explicit-migration'
        conn.execute(text('ALTER TABLE user_portal_access DROP CONSTRAINT uq_user_portal_access_portal_id'))
        conn.execute(text('ALTER TABLE user_portal_access DROP CONSTRAINT user_portal_access_pkey'))
        conn.execute(text('ALTER TABLE user_portal_access ADD CONSTRAINT user_portal_access_pkey PRIMARY KEY (user_id, portal_id)'))
        return 'migrated'


def main():
    parser = argparse.ArgumentParser(description='Guarded multi-portal PostgreSQL migration; never runs automatically')
    parser.add_argument('--execute', action='store_true', help='Execute DDL only after backup and isolated rehearsal')
    parser.add_argument('--acknowledge-backup-and-downtime', action='store_true')
    args = parser.parse_args()
    if args.execute and not args.acknowledge_backup_and_downtime:
        parser.error('--execute requires --acknowledge-backup-and-downtime')
    from .config import settings
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    print(json.dumps({'result': migrate(engine, execute=args.execute)}))


if __name__ == '__main__':
    main()
