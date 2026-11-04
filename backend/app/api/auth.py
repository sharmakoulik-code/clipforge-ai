from datetime import datetime, timedelta, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import (
    SESSION_COOKIE, clear_session_cookie, create_opaque_token, get_current_session,
    get_current_user, hash_password, hash_secret, set_session_cookie, verify_password,
)
from app.models.models import CreditWallet, PasswordResetToken, User, UserSession
from app.services.email import send_password_reset_email
from app.schemas.api import (
    LoginRequest, PasswordResetConfirm, PasswordResetRequest, ProfileUpdate,
    RegisterRequest, UserRead,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _new_session(db: Session, user: User, response: Response) -> None:
    token = create_opaque_token()
    db.add(UserSession(
        user_id=user.id,
        token_hash=hash_secret(token),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_seconds),
    ))
    db.commit()
    set_session_cookie(response, token)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> User:
    email = str(payload.email).strip().lower()
    enforce_rate_limit(request, scope="register", limit=5, identity=email)
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    user = User(email=email, display_name=payload.display_name, password_hash=hash_password(payload.password))
    try:
        db.add(user)
        db.flush()
        db.add(CreditWallet(user_id=user.id, balance=0, reserved=0))
        db.flush()
        _new_session(db, user, response)
        db.refresh(user)
        return user
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists") from exc


@router.post("/login", response_model=UserRead)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> User:
    email = str(payload.email).strip().lower()
    enforce_rate_limit(request, scope="login", limit=10, identity=email)
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled")
    _new_session(db, user, response)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: tuple[User, UserSession] | None = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    if session:
        record = db.get(UserSession, session[1].id)
        if record and not record.revoked_at:
            record.revoked_at = datetime.now(timezone.utc)
            db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/profile", response_model=UserRead)
def update_profile(payload: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    user.display_name = payload.display_name
    db.commit()
    db.refresh(user)
    return user


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(payload: PasswordResetRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str | None]:
    email = str(payload.email).strip().lower()
    enforce_rate_limit(request, scope="password-reset", limit=5, identity=email)
    user = db.query(User).filter(User.email == email).first()
    debug_token: str | None = None
    if user and user.is_active:
        debug_token = create_opaque_token()
        db.add(PasswordResetToken(
            user_id=user.id,
            token_hash=hash_secret(debug_token),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.password_reset_ttl_seconds),
        ))
        db.commit()
        if settings.app_env == "development":
            logger.info("Password-reset request accepted for user_id=%s", user.id)
        else:
            try:
                send_password_reset_email(user.email, debug_token)
            except Exception:
                logger.exception("Password-reset delivery failed user_id=%s", user.id)
    message = "If an account matches that email, a reset link will be sent."
    # Development-only token exposure enables local testing without an email server.
    return {"message": message, "debug_token": debug_token if settings.app_env == "development" else None}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(payload: PasswordResetConfirm, request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    enforce_rate_limit(request, scope="password-reset-confirm", limit=5)
    reset = db.query(PasswordResetToken).filter_by(token_hash=hash_secret(payload.token), used_at=None).first()
    now = datetime.now(timezone.utc)
    if reset is None or reset.expires_at.replace(tzinfo=timezone.utc) <= now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid or expired")
    user = db.get(User, reset.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid or expired")
    reset.used_at = now
    for other_reset in db.query(PasswordResetToken).filter_by(user_id=user.id, used_at=None).all():
        other_reset.used_at = now
    user.password_hash = hash_password(payload.new_password)
    for session in db.query(UserSession).filter_by(user_id=user.id, revoked_at=None).all():
        session.revoked_at = now
    db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
