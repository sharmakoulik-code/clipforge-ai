from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status

from app.core.config import settings
from app.storage.base import StorageAdapter


class LocalStorage:
    """Development storage adapter; object keys are opaque and never user paths."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.local_storage_path).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def new_key(self, suffix: str) -> str:
        return f"source/{uuid4().hex}{suffix.lower()}"

    def resolve(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid storage key")
        return candidate

    def delete(self, key: str | None) -> None:
        if not key:
            return
        path = self.resolve(key)
        path.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.resolve(key).is_file()


def create_storage() -> StorageAdapter:
    if settings.storage_provider != "local":
        raise RuntimeError(f"Unsupported storage provider: {settings.storage_provider}. Only local storage is enabled in Stage 2.")
    return LocalStorage()


storage = create_storage()
