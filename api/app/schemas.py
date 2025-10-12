from pydantic import BaseModel, field_validator

class RegisterIn(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def user_rules(cls, v):
        if not (3 <= len(v) <= 64):
            raise ValueError("invalid")
        return v

    @field_validator("password")
    @classmethod
    def pw_rules(cls, v):
        # min 10 char, kis/nagybetű, szám, spec
        import re
        if len(v) < 10 or not re.search(r"[a-z]", v) or not re.search(r"[A-Z]", v) or not re.search(r"\d", v) or not re.search(r"[^A-Za-z0-9]", v):
            raise ValueError("weak")
        return v

