# app/schemas.py
from pydantic import BaseModel, EmailStr, Field, field_validator


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
    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
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
