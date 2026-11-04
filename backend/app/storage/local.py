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

    def commit(self, key: str) -> None:
        return None

    def delete_prefix(self, prefix: str) -> None:
        path = self.resolve(prefix)
        if path.is_file():
            path.unlink(missing_ok=True)
            return
        if path.is_dir():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file() or child.is_symlink():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()


class S3Storage(LocalStorage):
    """S3-compatible storage with a shared local working cache for FFmpeg workers."""

    def __init__(self) -> None:
        super().__init__()
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("Install boto3 to use S3-compatible object storage.") from exc
        if not settings.aws_s3_bucket:
            raise RuntimeError("AWS_S3_BUCKET must be configured when STORAGE_PROVIDER=s3.")
        self.bucket = settings.aws_s3_bucket
        self.prefix = settings.aws_s3_prefix.strip("/")
        self.client = boto3.client(
            "s3", region_name=settings.aws_region or None,
            endpoint_url=settings.aws_endpoint_url or None,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )

    def _object_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def resolve(self, key: str) -> Path:
        path = super().resolve(key)
        if path.is_file():
            return path
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.client.download_file(self.bucket, self._object_key(key), str(path))
        except Exception as exc:
            # A new upload asks for a writable cache path before the object exists.
            response = getattr(exc, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise
        return path

    def commit(self, key: str) -> None:
        path = super().resolve(key)
        if not path.is_file():
            raise FileNotFoundError(f"Local staging artifact is missing: {key}")
        self.client.upload_file(str(path), self.bucket, self._object_key(key))

    def exists(self, key: str) -> bool:
        path = super().resolve(key)
        if path.is_file():
            return True
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
            return True
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete(self, key: str | None) -> None:
        if not key:
            return
        super().resolve(key).unlink(missing_ok=True)
        self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))

    def delete_prefix(self, prefix: str) -> None:
        super().delete_prefix(prefix)
        object_prefix = self._object_key(prefix.rstrip("/") + "/")
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=object_prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})


def create_storage() -> StorageAdapter:
    if settings.storage_provider == "local":
        return LocalStorage()
    if settings.storage_provider in {"s3", "s3-compatible"}:
        return S3Storage()
    raise RuntimeError(f"Unsupported storage provider: {settings.storage_provider}.")


storage = create_storage()
