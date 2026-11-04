import logging
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.admin import router as admin_router
from app.api.billing import router as billing_router
from app.api.auth import router as auth_router
from app.api.clips import router as clips_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.projects import router as projects_router
from app.api.preferences import router as preferences_router
from app.api.social_accounts import router as social_accounts_router
from app.api.social_publishing import router as social_publishing_router
from app.core.config import settings
from app.core.upload_limit import UploadLimitMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(UploadLimitMiddleware)
backend_host = urlsplit(settings.backend_url).hostname
trusted_hosts = [host for host in (backend_host, "localhost", "127.0.0.1", "::1") if host]
if settings.app_env.lower() not in {"production", "prod"}:
    trusted_hosts.append("testserver")
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=trusted_hosts,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(preferences_router)
app.include_router(social_accounts_router)
app.include_router(social_publishing_router)
app.include_router(admin_router)
app.include_router(billing_router)
app.include_router(projects_router)
app.include_router(clips_router)
app.include_router(jobs_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "ok"}
