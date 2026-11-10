# app/deps.py
from datetime import timedelta
from typing import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import settings
from .models import SessionLocal, User
from .security import verify_session_value
from .session_store import is_session_valid, touch_session
from .timestamps import utc_now


# DB dependency
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Inaktivitási limit – a SESSION_TIMEOUT_MINUTES env változóval állítható.
MAX_IDLE_SECONDS = settings.SESSION_TIMEOUT_MINUTES * 60
# Ennyi ideig nem írjuk vissza az utolsó aktivitást, hogy ne legyen
# minden hiteles kérésből egy UPDATE + commit.
ACTIVITY_WRITE_INTERVAL_SECONDS = 60


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # 1) session süti beolvasása a settings-ben megadott néven
    session_val = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_val:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # 2) a saját aláírt session értékből felhasználónév + jti kinyerése
    verified = verify_session_value(session_val)
    if not verified:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    username, jti = verified

    # 3) a session a szerveren van nyilvántartva: kilépés, jelszócsere vagy
    #    adminisztratív tiltás azonnal érvénytelenné teszi a sütit.
    if not is_session_valid(username, jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    # 4) user betöltése DB-ből
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")

    # 5) Inaktivitási timeout kezelése (ha van last_activity mező)
    if hasattr(user, "last_activity") and user.last_activity:
        if utc_now() - user.last_activity > timedelta(seconds=MAX_IDLE_SECONDS):
            # lejárt a session az inaktivitás miatt
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

        # Az utolsó aktivitást csak ütemezetten írjuk vissza, de a süti
        # abszolút lejárata így is tolódik.
        idle_for = (utc_now() - user.last_activity).total_seconds()
        if idle_for > ACTIVITY_WRITE_INTERVAL_SECONDS:
            user.last_activity = utc_now()
            db.commit()
        touch_session(username, jti, settings.SESSION_COOKIE_TTL_SECONDS)

    return user

