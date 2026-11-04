from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.billing.plans import get_user_plan
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user
from app.models.models import AuditLog, OAuthCredential, SocialAccount, SocialOAuthState, SocialProvider, User
from app.social.crypto import decrypt_secret, encrypt_secret
from app.social.providers import ProviderError, get_provider

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/social", tags=["social accounts"])


def _youtube_configured() -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret
                and settings.google_oauth_redirect_uri and settings.social_token_encryption_key)


def _provider_row(db: Session, slug: str) -> SocialProvider:
    row = db.query(SocialProvider).filter_by(slug=slug, enabled=True).first()
    if not row:
        raise HTTPException(status_code=404, detail="Social provider is not available.")
    return row


def _log(db: Session, user_id: str, action: str, *, target_id: str | None = None, details: dict | None = None) -> None:
    db.add(AuditLog(actor_user_id=user_id, action=action, target_type="social", target_id=target_id, details=details))


@router.get("/providers")
def list_social_providers(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.query(SocialProvider).filter_by(enabled=True).order_by(SocialProvider.display_name).all()
    youtube_ready = _youtube_configured()
    return {"providers": [{
        "slug": row.slug, "name": row.display_name, "oauth_supported": row.oauth_supported,
        "publishing_supported": row.publishing_supported,
        "connect_available": bool(row.slug == "youtube" and row.oauth_supported and youtube_ready),
        "approval_required": row.approval_required, "availability_note": row.availability_note,
    } for row in rows]}


@router.get("/accounts")
def list_social_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.query(SocialAccount, SocialProvider).join(SocialProvider, SocialProvider.id == SocialAccount.provider_id).filter(
        SocialAccount.user_id == user.id
    ).order_by(SocialAccount.created_at.desc()).all()
    return {"accounts": [{
        "id": account.id, "provider": provider.slug, "provider_name": provider.display_name,
        "external_account_id": account.external_account_id, "display_name": account.display_name,
        "username": account.username, "status": account.status,
        "connected_at": account.connected_at, "disconnected_at": account.disconnected_at,
        "metadata": account.account_metadata or {},
    } for account, provider in rows]}


@router.post("/accounts/youtube/oauth/start")
def start_youtube_oauth(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit(request, scope="social-oauth-start", limit=5)
    provider_row = _provider_row(db, "youtube")
    provider = get_provider("youtube")
    if not provider or not provider_row.oauth_supported:
        raise HTTPException(status_code=501, detail=provider_row.availability_note)
    if not _youtube_configured():
        raise HTTPException(status_code=503, detail="Set Google OAuth client credentials, redirect URI, and token-encryption key on the server.")
    _, plan = get_user_plan(db, user.id)
    connected = db.query(SocialAccount).filter_by(user_id=user.id, status="connected").count()
    reconnectable = db.query(SocialAccount).filter(
        SocialAccount.user_id == user.id, SocialAccount.status.in_(["disconnected", "reauth_required"])
    ).first() is not None
    if connected >= int(plan.get("max_connected_accounts", 0)) and not reconnectable:
        raise HTTPException(status_code=403, detail="Your plan's connected-account limit has been reached.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    state_row = SocialOAuthState(
        user_id=user.id, provider_id=provider_row.id,
        state_hash=hashlib.sha256(state.encode()).hexdigest(), code_verifier=verifier,
        redirect_uri=settings.google_oauth_redirect_uri,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(state_row)
    db.commit()
    authorization_url = provider.authorization_url(settings.google_oauth_client_id,
                                                     settings.google_oauth_redirect_uri, state, verifier)
    return {"authorization_url": authorization_url, "expires_in_seconds": 600}


@router.get("/oauth/youtube/callback")
def youtube_oauth_callback(state: str = "", code: str = "", error: str | None = None,
                           db: Session = Depends(get_db)) -> RedirectResponse:
    result_page = settings.frontend_url.rstrip("/") + "/dashboard/social"
    if not state:
        return RedirectResponse(result_page + "?connection=invalid_state", status_code=303)
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    state_row = db.query(SocialOAuthState).filter_by(state_hash=state_hash).with_for_update().first()
    now = datetime.now(timezone.utc)
    if not state_row or state_row.consumed_at or state_row.expires_at.replace(tzinfo=timezone.utc) <= now:
        return RedirectResponse(result_page + "?connection=invalid_state", status_code=303)
    state_row.consumed_at = now
    db.commit()
    if error or not code:
        return RedirectResponse(result_page + "?connection=cancelled", status_code=303)
    if not _youtube_configured():
        return RedirectResponse(result_page + "?connection=server_not_configured", status_code=303)
    provider_row = db.get(SocialProvider, state_row.provider_id)
    provider = get_provider(provider_row.slug) if provider_row else None
    if not provider or not provider_row.enabled:
        return RedirectResponse(result_page + "?connection=provider_unavailable", status_code=303)
    try:
        token = provider.exchange_code(code, settings.google_oauth_client_id, settings.google_oauth_client_secret,
                                       state_row.redirect_uri, state_row.code_verifier)
        access_token = str(token["access_token"])
        accounts = provider.accounts(access_token)
        _, plan = get_user_plan(db, state_row.user_id)
        db.query(User).filter_by(id=state_row.user_id).with_for_update().first()
        current = db.query(SocialAccount).filter_by(user_id=state_row.user_id, status="connected").count()
        limit = int(plan.get("max_connected_accounts", 0))
        allowed = max(0, limit - current)
        if accounts and allowed == 0 and not any(
            db.query(SocialAccount.id).filter_by(user_id=state_row.user_id, provider_id=provider_row.id,
                                                  external_account_id=info.external_id, status="connected").first()
            for info in accounts
        ):
            db.rollback()
            return RedirectResponse(result_page + "?connection=account_limit", status_code=303)
        expires_in = max(60, min(86400, int(token.get("expires_in", 3600))))
        expires_at = now + timedelta(seconds=expires_in)
        scopes = str(token.get("scope") or "").split()
        refresh = str(token["refresh_token"]) if token.get("refresh_token") else None
        connected_count = 0
        for account_info in accounts:
            existing = db.query(SocialAccount).filter_by(user_id=state_row.user_id, provider_id=provider_row.id,
                                                          external_account_id=account_info.external_id).first()
            if existing and existing.status == "connected":
                account = existing
            else:
                if connected_count >= allowed:
                    break
                if existing:
                    account = existing
                    account.status, account.disconnected_at = "connected", None
                    account.display_name, account.username = account_info.display_name, account_info.username
                    account.account_metadata = account_info.metadata
                else:
                    account = SocialAccount(user_id=state_row.user_id, provider_id=provider_row.id,
                                            external_account_id=account_info.external_id,
                                            display_name=account_info.display_name, username=account_info.username,
                                            status="connected", account_metadata=account_info.metadata)
                    db.add(account)
                    db.flush()
                connected_count += 1
            credential = db.query(OAuthCredential).filter_by(social_account_id=account.id).first()
            if credential is None:
                credential = OAuthCredential(social_account_id=account.id,
                    access_token_encrypted=encrypt_secret(access_token),
                    refresh_token_encrypted=encrypt_secret(refresh) if refresh else None,
                    expires_at=expires_at, scopes=scopes, token_type=str(token.get("token_type", "Bearer")))
                db.add(credential)
            else:
                credential.access_token_encrypted = encrypt_secret(access_token)
                if refresh:
                    credential.refresh_token_encrypted = encrypt_secret(refresh)
                credential.expires_at, credential.scopes = expires_at, scopes
                credential.last_refreshed_at = now
            _log(db, state_row.user_id, "social.account.connected", target_id=account.id, details={"provider": "youtube"})
        if connected_count == 0 and current == 0:
            db.rollback()
            return RedirectResponse(result_page + "?connection=no_accounts", status_code=303)
        db.commit()
        return RedirectResponse(result_page + "?connection=youtube_connected", status_code=303)
    except ProviderError as exc:
        db.rollback()
        logger.warning("YouTube OAuth callback failed code=%s", exc.code)
        outcome = "no_accounts" if exc.code == "youtube_channel_missing" else "oauth_failed"
        return RedirectResponse(result_page + f"?connection={outcome}", status_code=303)
    except Exception:
        db.rollback()
        logger.exception("Unexpected YouTube OAuth callback failure")
        return RedirectResponse(result_page + "?connection=oauth_failed", status_code=303)


@router.delete("/accounts/{account_id}")
def disconnect_social_account(account_id: str, request: Request, user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit(request, scope="social-account-disconnect", limit=10)
    account = db.query(SocialAccount).filter_by(id=account_id, user_id=user.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Connected account not found.")
    credential = db.query(OAuthCredential).filter_by(social_account_id=account.id).first()
    remote_revoked = False
    if credential:
        token_to_revoke = None
        own_refresh = None
        try:
            token_to_revoke = decrypt_secret(credential.refresh_token_encrypted or credential.access_token_encrypted)
            if credential.refresh_token_encrypted:
                own_refresh = decrypt_secret(credential.refresh_token_encrypted)
        except HTTPException:
            logger.warning("Could not decrypt social credential during disconnect account_id=%s", account.id)
        siblings = db.query(OAuthCredential).join(SocialAccount, SocialAccount.id == OAuthCredential.social_account_id).filter(
            SocialAccount.user_id == user.id, SocialAccount.provider_id == account.provider_id,
            SocialAccount.id != account.id, SocialAccount.status == "connected"
        ).all()
        shared_grant = bool(siblings and not own_refresh)
        if own_refresh:
            for sibling in siblings:
                if not sibling.refresh_token_encrypted:
                    shared_grant = True
                    break
                try:
                    if decrypt_secret(sibling.refresh_token_encrypted) == own_refresh:
                        shared_grant = True
                        break
                except HTTPException:
                    shared_grant = True
                    break
        if token_to_revoke and not shared_grant:
            provider_row = db.get(SocialProvider, account.provider_id)
            implementation = get_provider(provider_row.slug) if provider_row else None
            if implementation:
                try:
                    implementation.revoke_token(token_to_revoke)
                    remote_revoked = True
                except ProviderError:
                    logger.warning("Google OAuth revocation was not confirmed account_id=%s", account.id)
        db.delete(credential)
    account.status, account.disconnected_at = "disconnected", datetime.now(timezone.utc)
    _log(db, user.id, "social.account.disconnected", target_id=account.id,
         details={"remote_revoked": remote_revoked})
    db.commit()
    message = ("Local credentials were removed and Google confirmed permission revocation." if remote_revoked else
               "Local credentials were removed. Google-side consent could not be revoked independently; check Google Account permissions if needed.")
    return {"status": "disconnected", "remote_revoked": remote_revoked, "message": message}
