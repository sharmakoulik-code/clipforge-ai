from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.core.config import settings


def _fernet() -> Fernet:
    if not settings.social_token_encryption_key:
        raise HTTPException(status_code=503, detail="Social token encryption is not configured on this server.")
    try:
        return Fernet(settings.social_token_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=503, detail="Social token encryption key is invalid.") from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=503, detail="Stored social credentials cannot be decrypted; reconnect the account.") from exc
