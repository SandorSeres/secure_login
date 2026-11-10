# app/schemas.py
from pydantic import BaseModel, EmailStr, field_validator

# KÖZÖS jelszó-szabály validátor
def _validate_password(v: str) -> str:
    import re
    if (
        len(v) < 10
        or not re.search(r"[a-z]", v)
        or not re.search(r"[A-Z]", v)
        or not re.search(r"\d", v)
        or not re.search(r"[^A-Za-z0-9]", v)
    ):
        raise ValueError("weak")
    return v

class RegisterIn(BaseModel):
    username: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def pw_rules(cls, v):  # reuse
        return _validate_password(v)

class ResetPasswordIn(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def pw_rules(cls, v):  # ugyanaz a szabály
        return _validate_password(v)


class SetLevelIn(BaseModel):
    """Allowed account levels; anything else is rejected before the DB write."""
    username: str
    level: str

    @field_validator("level")
    @classmethod
    def level_is_known(cls, v: str) -> str:
        if v not in ("Free", "Pro", "Enterprise"):
            raise ValueError("unknown level")
        return v

