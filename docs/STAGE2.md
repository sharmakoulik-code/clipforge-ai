# Stage 2 delivery and verification

> Historical Stage 2 record. The processing worker and storage support were subsequently extended in Stage 3; see [`STAGE3.md`](STAGE3.md) for current capabilities and limits.

## Implemented

- Original responsive Frameforge-branded marketing site with navigation, hero/upload CTA, product-preview section, workflow, features, AI explainer, pricing preview, FAQ, footer, and registration/sign-in routes. Theme toggle supports light/dark themes.
- Argon2id registration/login, logout, `/me`, profile update, HTTP-only persisted sessions, expiration/revocation, signed double-submit CSRF protection on authenticated mutations, password reset with hashed one-use expiring tokens, role guard, and rate limits.
- Dashboard KPIs, recent projects, processing history, export list, subscription/credit values all come from user-scoped API queries (no fabricated statistics). Project rename, detail, history, metadata display, and owner-only deletion are implemented.
- Single-request multipart file upload with drag/drop, browser file selection, client/server size and extension validation, container signature checks, progress, cancellation, and errors. Storage paths are generated and traversal-checked; original video bytes stay in local object storage, never PostgreSQL.
- FFprobe metadata extraction and persistence. YouTube reference URL validation/rights confirmation and optional public oEmbed metadata are supported; no video downloading is performed.
- Persisted Celery/Redis processing jobs, worker validation stage, status/detail endpoints, ownership checks, retry endpoint, and dashboard polling. The worker only verifies the upload and leaves the job `queued` at `awaiting_stage_3`; AI/transcription/rendering are intentionally not implemented.
- Alembic migration `0002_sessions` adds session/reset tables and project source URLs. Compose shares a persistent media volume between API and worker. SMTP settings and a production email adapter are present for password-reset delivery.

## Acceptance workflow exercised

A generated one-second MP4 was sent through the running Next.js `/api` rewrite to FastAPI. The server streamed and validated the upload, ran FFprobe, persisted duration (1.0s), resolution (320×240), H.264 codec, audio-stream count, file size, source project, and processing job. With Redis and a Celery worker running, the worker consumed the job and persisted `queued / awaiting_stage_3 / 5%`. The project was reopened, renamed, then deleted; its local stored file was removed. Registration/session auth, dashboard read, logout/protected access, and invalid-upload response were also smoke-tested over HTTP.

## Test results

- `backend`: 17 pytest tests pass, covering registration, login, logout, protected routes, CSRF, role denial/allow, password reset/session revocation, profile update, upload signature/size rejection, metadata normalization, project/job ownership, dashboard stats, retry, YouTube validation/consent, and deletion cleanup. FFprobe and queue dependencies are mocked only in isolated unit tests.
- Actual FFprobe/FFmpeg tools were temporarily supplied from PyPI for the real MP4 smoke test; they are not added as Python project dependencies. The backend Dockerfile installs FFmpeg/FFprobe for local Compose use.
- An ephemeral PostgreSQL 18.6 server was provisioned from a PyPI wheel; both Alembic migrations applied and the expected tables were inspected. The Compose image targets PostgreSQL 16; PostgreSQL 16 itself was not run here.
- A Redis-compatible 6.2.14 server and real Celery worker were started locally; broker connectivity and job consumption were observed. Compose and its PostgreSQL 16/Redis 7 images were not launched because Docker is unavailable.
- Frontend `npm run build`, `npm run lint` (strict TypeScript check), and `npm audit` passed; audit reports zero vulnerabilities. Browser-driven UI automation has not been run.

## Remaining limits

1. No AI processing, transcript generation, clip rendering, editing, exports, or actual credit/billing flows in this stage.
2. Password reset is production-capable only when real SMTP settings are supplied. Development exposes a debug token intentionally; production does not return it. SMTP delivery was covered with an adapter mock, not a real mail server.
3. YouTube metadata lookup depends on external oEmbed availability and was tested with a mock. The sandbox cannot establish external YouTube API access. URLs are saved as references and require an authorized file upload.
4. Local storage only; no S3/R2 implementation, multipart/resumable upload protocol, signed download, malware scan, or retention worker.
5. Rate limits are process-local; use Redis/edge-level distributed limits for multi-worker deployments. Configure `TRUSTED_PROXY_CIDRS` narrowly.
6. PostgreSQL migration was tested on embedded 18.6, not the Compose PostgreSQL 16 image. Docker Compose startup itself was not tested.
7. Available credits are database-backed (new users start at zero), but no purchase, subscription management, credit reservation, or charge endpoint exists.
8. The Arena checkout is `clipforge-ai` on `arena/01abe54e-clipforge-ai`; commit `cce429d46503e23eaaa766d4686456ba84c295d5` is pushed there. It is not the requested `ai-video-repurposer` repository or `main` branch; see `docs/ENVIRONMENT.md`.
