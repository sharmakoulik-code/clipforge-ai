from typing import Any, Awaitable, Callable

from fastapi.responses import JSONResponse

from app.core.config import settings


class PayloadTooLarge(Exception):
    pass


class UploadLimitMiddleware:
    """Enforce a streaming body limit before Starlette parses multipart uploads."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app
        self.request_limit = settings.max_upload_bytes + 1024 * 1024  # multipart framing allowance

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/projects/upload":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.request_limit:
                    await JSONResponse({"detail": "Upload request is too large"}, status_code=413)(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.request_limit:
                    raise PayloadTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except PayloadTooLarge:
            await JSONResponse({"detail": "Upload request is too large"}, status_code=413)(scope, receive, send)
