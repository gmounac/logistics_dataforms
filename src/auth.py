"""Password hashing and the session-cookie helpers.

The FastAPI wiring (dependencies, routes) lives in api.py; this module is just
the pieces it needs, kept import-cycle-free.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy.orm import Session
from starlette.requests import Request

from src.models import User

# argon2id with the library defaults — fine for an internal tool.
_ph = PasswordHasher()

# Key under which the signed session cookie carries the user id.
SESSION_KEY = "uid"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _ph.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash uses weaker parameters than the current default."""
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def user_from_session(session: Session, request: Request) -> User | None:
    """The signed-in, still-active user for this request, or None."""
    uid = request.session.get(SESSION_KEY)
    if uid is None:
        return None
    user = session.get(User, uid)
    if user is None or user.disabled:
        return None
    return user
