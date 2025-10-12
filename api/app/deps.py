# app/deps.py
from datetime import datetime, timedelta
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


# Inaktivitási limit (másodpercben) – állítsd igény szerint
MAX_IDLE_SECONDS = 600  # 10 perc


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # 1) session süti beolvasása a settings-ben megadott néven
    session_val = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_val:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # 2) a saját aláírt session értékedből felhasználónév kinyerése/ellenőrzése
    username = verify_session_value(session_val)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    # 3) user betöltése DB-ből
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")

    # 4) Inaktivitási timeout kezelése (ha van last_activity mező)
    if hasattr(user, "last_activity") and user.last_activity:
        if datetime.utcnow() - user.last_activity > timedelta(seconds=MAX_IDLE_SECONDS):
            # lejárt a session az inaktivitás miatt
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

        # frissítjük az utolsó aktivitást
        user.last_activity = datetime.utcnow()
        db.commit()

    return user

