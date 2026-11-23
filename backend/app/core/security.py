from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.db import read_setting

ACCESS_LIFETIME = timedelta(minutes=15)
REFRESH_LIFETIME = timedelta(days=7)
ISSUER = "commerce-agent"
PASSWORD_HASHER = PasswordHasher()
DUMMY_HASH = PASSWORD_HASHER.hash(token_urlsafe(32))


@dataclass(frozen=True)
class AuthSettings:
    secret: str
    secure_cookie: bool

    @classmethod
    def from_values(cls, secret: str | None, secure_cookie: str | None) -> "AuthSettings":
        if not secret or len(secret.encode()) < 32:
            raise RuntimeError("AUTH_JWT_SECRET must contain at least 32 bytes")
        flag = (secure_cookie or "true").lower()
        if flag not in {"true", "false"}:
            raise RuntimeError("AUTH_COOKIE_SECURE must be true or false")
        return cls(secret, flag == "true")

    @classmethod
    def load(cls) -> "AuthSettings":
        return cls.from_values(read_setting("AUTH_JWT_SECRET"), read_setting("AUTH_COOKIE_SECURE"))


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash or DUMMY_HASH, password) and password_hash is not None
    except (InvalidHashError, VerificationError):
        return False


def issue_access_token(settings: AuthSettings, user_id: int, session_id: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"iss": ISSUER, "sub": str(user_id), "sid": session_id, "iat": now, "exp": now + ACCESS_LIFETIME},
        settings.secret,
        algorithm="HS256",
    )


def read_access_token(settings: AuthSettings, token: str) -> tuple[int, int] | None:
    try:
        claims = jwt.decode(
            token, settings.secret, algorithms=["HS256"], issuer=ISSUER,
            options={"require": ["iss", "sub", "sid", "iat", "exp"]},
        )
        if type(claims["sid"]) is int and claims["sid"] > 0:
            return int(claims["sub"]), claims["sid"]
    except (jwt.InvalidTokenError, ValueError):
        pass
    return None
