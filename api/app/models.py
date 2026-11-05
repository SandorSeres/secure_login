# app/models.py
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    String,
    Integer,
    DateTime,
    ForeignKey,
    create_engine,
    func,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, sessionmaker

from .config import settings

# --- SQLAlchemy alapok -------------------------------------------------------

Base = declarative_base()

# Stabil kapcsolat: pool_pre_ping; a 2.0-s API-k használatához future=True
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

# --- Modellek ----------------------------------------------------------------


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(63), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class Portal(Base):
    __tablename__ = "portals"

    id: Mapped[str] = mapped_column(String(63), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class User(Base):
    __tablename__ = "users"

    # Gyakori lekérdezésekhez és adatkonzisztenciához
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_username", "username"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Felhasználónév (kötelező)
    username: Mapped[str] = mapped_column(String(128), nullable=False)

    # E-mail (kötelező, egyedi)
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    # Jelszó hash (Argon2, nem tárolunk plain-textet)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)

    # Általános szerepkör; a portál-hozzáférést nem ez vezérli.
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Létrehozás ideje (időzónás UTC). Postgres-ben TIMESTAMPTZ.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Utolsó aktivitás (időzónás UTC) — current_user dependency frissíti.
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def touch(self) -> None:
        """Alkalmi segítség: last_activity frissítése (aktív session mellett)."""
        self.last_activity = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role_id}>"


class UserPortalAccess(Base):
    """User-to-portal grants. Legacy unique constraints persist until explicit migration."""

    __tablename__ = "user_portal_access"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    portal_id: Mapped[str] = mapped_column(
        ForeignKey("portals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
