# app/models.py
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    String,
    Integer,
    DateTime,
    Enum as SAEnum,
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

# --- Domain enumok -----------------------------------------------------------

class AccountLevel(str, Enum):
    Free = "Free"
    Pro = "Pro"
    Enterprise = "Enterprise"

# --- Modellek ----------------------------------------------------------------

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

    # Jelszó hash (bcrypt, nem tárolunk plain-textet)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)

    # Fiók szint
    account_level: Mapped[AccountLevel] = mapped_column(
        SAEnum(AccountLevel, name="account_level_enum"),
        nullable=False,
        default=AccountLevel.Free,                  # Python oldali default
        server_default=AccountLevel.Free.value,    # DB oldali default
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
        return f"<User id={self.id} username={self.username!r} level={self.account_level}>"

