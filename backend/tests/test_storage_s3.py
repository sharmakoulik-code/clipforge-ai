from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings
from app.storage.local import S3Storage


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "missing"}}, "HeadObject")
        return {}

    def upload_file(self, filename, bucket, key):
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_s3_adapter_commits_downloads_and_deletes(monkeypatch, tmp_path):
    fake = FakeS3()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: fake)
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "aws_s3_bucket", "unit-test-bucket")
    monkeypatch.setattr(settings, "aws_s3_prefix", "test-prefix")
    adapter = S3Storage()
    key = "artifacts/project/test.json"
    path = adapter.resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("cached artifact")
    adapter.commit(key)
    assert adapter.exists(key)
    path.unlink()
    restored = adapter.resolve(key)
    assert restored.read_text() == "cached artifact"
    adapter.delete(key)
    assert not adapter.exists(key)
