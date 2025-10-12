from fastapi import Depends, Request, HTTPException, status
from sqlalchemy.orm import Session
from .models import SessionLocal, User
from .security import verify_session_value

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    sess = request.cookies.get("session")
    if not sess:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    username = verify_session_value(sess)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user

