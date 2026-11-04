from datetime import datetime, timezone
from hashlib import sha256
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.models import User, UserSession

PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not-a-real-password-for-timing-equalization")
SESSION_COOKIE = "clipforge_session"
CSRF_COOKIE = "clipforge_csrf"


def _csrf_signature(token: str) -> str:
    return hmac.new(settings.secret_key.encode(), token.encode(), "sha256").hexdigest()


def _valid_csrf(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    token, signature = value.rsplit(".", 1)
    expected = _csrf_signature(token)
    return hmac.compare_digest(signature, expected)


def hash_secret(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def create_opaque_token() -> str:
    return secrets.token_urlsafe(40)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate = password_hash or DUMMY_PASSWORD_HASH
    try:
        valid = PASSWORD_HASHER.verify(candidate, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return bool(valid and password_hash)


def get_current_session(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> tuple[User, UserSession]:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf_cookie = request.cookies.get(CSRF_COOKIE)
        csrf_header = request.headers.get("x-csrf-token")
        if not _valid_csrf(csrf_cookie) or not csrf_header or not hmac.compare_digest(csrf_cookie or "", csrf_header):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
    session = db.query(UserSession).filter_by(token_hash=hash_secret(session_token), revoked_at=None).first()
    now = datetime.now(timezone.utc)
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is unavailable")
    return user, session


def get_current_user(session: tuple[User, UserSession] = Depends(get_current_session)) -> User:
    return session[0]


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user


def set_session_cookie(response, token: str) -> None:
    secure = settings.app_env.lower() in {"production", "prod"}
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    csrf_token = create_opaque_token()
    response.set_cookie(
        key=CSRF_COOKIE,
        value=f"{csrf_token}.{_csrf_signature(csrf_token)}",
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    secure = settings.app_env.lower() in {"production", "prod"}
    response.delete_cookie(key=SESSION_COOKIE, httponly=True, secure=secure, samesite="lax", path="/")
    response.delete_cookie(key=CSRF_COOKIE, httponly=False, secure=secure, samesite="lax", path="/")
