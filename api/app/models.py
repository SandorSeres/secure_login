from datetime import datetime
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import settings

Base = declarative_base()
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True) 
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    account_level: Mapped[str] = mapped_column(String(32), default="Free")  
    last_activity = mapped_column(DateTime, default=datetime.utcnow)


# Independent application catalog and user-to-application grants.
# Existing User columns and authentication behavior remain unchanged.
from sqlalchemy import ForeignKey


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(63), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class UserApplicationAccess(Base):
    __tablename__ = "user_application_access"

    # Composite primary key prevents duplicate grants while allowing:
    # one user -> many applications; one application -> many users.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class ApplicationTarget(Base):
    """Optional private HTTP target; application grants remain the sole authority."""
    __tablename__ = "application_targets"

    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True
    )
    upstream_origin: Mapped[str] = mapped_column(String(255), nullable=False)
    public_url: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    docker_network: Mapped[str | None] = mapped_column(String(128), nullable=True)
    legacy_roots: Mapped[str | None] = mapped_column(String(255), nullable=True)
