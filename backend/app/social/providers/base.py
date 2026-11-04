from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


@dataclass(frozen=True)
class ProviderAccount:
    external_id: str
    display_name: str
    username: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PublishResult:
    external_post_id: str
    external_post_url: str | None
    provider_request_id: str | None = None


class PublishingProvider(Protocol):
    slug: str

    def publish(self, video_path: Path, metadata: dict[str, Any], options: dict[str, Any],
                access_token: str, *, request_key: str) -> PublishResult: ...
