# app/deps.py
from datetime import datetime, timedelta, timezone
from typing import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import settings
from .models import SessionLocal, User
from .security import verify_session_value


# DB dependency
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Inaktivitási limit (másodpercben)
MAX_IDLE_SECONDS = 600  # 10 perc


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # 1) Session süti beolvasása
    session_val = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_val:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # 2) Aláírt session érték ellenőrzése, felhasználónév kinyerése
    username = verify_session_value(session_val)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    # 3) User betöltése DB-ből
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user",
        )

    # 4) Inaktivitási timeout kezelése (UTC-aware időkkel)
    if hasattr(user, "last_activity") and user.last_activity:
        now = datetime.now(timezone.utc)

        last = user.last_activity
        # Ha valamiért naive datetime érkezne, tegyük UTC-aware-ré
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        if now - last > timedelta(seconds=MAX_IDLE_SECONDS):
            # Lejárt a session az inaktivitás miatt
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
            )

        # Frissítjük az utolsó aktivitást aware UTC értékkel
        user.last_activity = now
        db.commit()

    return user

