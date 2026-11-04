# ClipForge AI deployment preparation

This repository contains a Next.js web application and a FastAPI/Celery backend. These settings prepare the existing application for Vercel and Railway; they do not create hosting projects, provision paid services, configure production credentials, or deploy the application. GitHub Pages configuration and repository files are intentionally unchanged. GitHub Pages is not the host for this dynamic Next.js application.

## Deployment plan

1. Create a Vercel project from this repository with **Root Directory** set to `frontend`. This deploys the Next.js application rather than rendering the repository README.
2. Provision managed PostgreSQL and Redis on Railway, and provision a private S3-compatible bucket with a least-privilege application identity. Configure variables in platform secret stores, not source files.
3. Create three Railway services from the repository root, each using `backend/Dockerfile` and its own config-as-code file: `railway.api.toml`, `railway.worker.toml`, or `railway.scheduler.toml`.
4. Configure the API, verify its HTTPS health endpoint and migrations, then start one worker and one scheduler. Keep API and worker replicas to one until distributed rate limiting and PostgreSQL concurrency have been load-tested; keep Celery Beat to one instance.
5. Add the Railway API origin to Vercel as the server-only `BACKEND_INTERNAL_URL`. Browser API calls stay on the Vercel origin and are proxied by Next.js to FastAPI over HTTPS.
6. Smoke-test auth, database/queue readiness, S3 upload and retrieval, processing, and email using test accounts and authorized media. Configure YouTube OAuth only after domains and callback URLs are final; keep Stripe unset or test-mode only.

No step above has been performed against external hosting in this workspace.

## Frontend: Vercel

`frontend/package.json` defines:

| Purpose | Command |
|---|---|
| Install | `npm ci` |
| Production build | `npm run build` |
| Self-hosted production start | `npm run start` |
| TypeScript check | `npm run lint` |

Use the **Next.js** framework preset, project root `frontend`, and the committed [`frontend/vercel.json`](../frontend/vercel.json). Vercel manages the production server; do not configure a static export or GitHub Pages deployment. The existing browser client calls relative `/api/*` URLs. Next.js rewrites those requests to `BACKEND_INTERNAL_URL`; no backend secret or provider API key belongs in a `NEXT_PUBLIC_*` variable. The config requires an HTTPS backend origin in Vercel and fails the build if it is missing or insecure.

Set this server-side Vercel environment variable for Production and each Preview environment that should connect to an API:

| Variable | Value |
|---|---|
| `BACKEND_INTERNAL_URL` | `https://<railway-api-domain>` (origin only; no path, query, credentials, or trailing route) |

If a Vercel custom domain is used, set Railway `FRONTEND_URL` to that exact HTTPS origin. Vercel rewrites for large uploads must be verified against the selected Vercel account's request limits before accepting production media; the application allows uploads up to `MAX_UPLOAD_BYTES` (default 2 GiB).

## Backend: Railway

Create each Railway service with the repository root as the source/build context. Select the service's **Config-as-code file** in Railway settings:

| Service | Config file | Runtime command / behavior |
|---|---|---|
| API | [`railway.api.toml`](../railway.api.toml) | Dockerfile command runs Alembic migrations, then Uvicorn on Railway's `PORT`; `/health/ready` gates deployment. |
| Worker | [`railway.worker.toml`](../railway.worker.toml) | `celery -A app.workers.celery_app.celery worker --loglevel=INFO` |
| Scheduler | [`railway.scheduler.toml`](../railway.scheduler.toml) | `celery -A app.workers.celery_app.celery beat --loglevel=INFO` |

All three use `backend/Dockerfile` from the repository root. It installs FFmpeg (including FFprobe) and DejaVu Sans, required by processing and watermark rendering. The worker and scheduler use the same image and dependencies as the API. Only the API uses the image's default command and performs migrations. Do not give the API and worker different database, Redis, S3, or encryption-key values.

Deploy PostgreSQL and Redis first. Set Railway service references using the actual names Railway assigns; for example, map its PostgreSQL connection variable to `DATABASE_URL` and its Redis connection variable to `REDIS_URL`. Do not copy example URLs literally. The app normalizes common PostgreSQL URL schemes to psycopg 3. Use private service networking where available and TLS for database/Redis connections when supported by the selected service.

## Required production environment

Set these variables in the Railway API and worker services; also give the scheduler the common settings needed to import the application and dispatch scheduled work. Keep values in Railway's secret manager and use the same values across services that share credentials.

| Variable | Production value / purpose |
|---|---|
| `APP_ENV` | `production`; enables secure cookies and production validation. |
| `APP_NAME` | Optional API display/title value. |
| `SECRET_KEY` | Unique random value of at least 32 characters. Generate locally with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. Keep stable and private. |
| `FRONTEND_URL` | Exact Vercel HTTPS origin, e.g. `https://<app-domain>`; no route path. This is the sole configured CORS origin and the destination for account recovery/OAuth completion. |
| `BACKEND_URL` | Exact public HTTPS Railway API origin, e.g. `https://<api-domain>`; used to restrict accepted HTTP Host values. |
| `DATABASE_URL` | Managed PostgreSQL connection URL. SQLite is rejected in production. |
| `REDIS_URL` | Managed Redis connection URL (`redis://` or `rediss://`), not localhost. Readiness fails when Redis cannot be reached. |
| `STORAGE_PROVIDER` | `s3` (or `s3-compatible`); local-only storage is rejected in production. |
| `AWS_S3_BUCKET` | Private persistent bucket name. |
| `AWS_REGION` | Bucket region when using AWS S3. |
| `AWS_S3_PREFIX` | Key namespace, e.g. `clipforge`. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Restricted storage credentials if the platform does not supply an IAM role. Never expose them to Vercel/browser code. |
| `AWS_ENDPOINT_URL` | Optional S3-compatible endpoint; production validation requires HTTPS when set. Leave blank for AWS S3. |
| `SMTP_HOST`, `SMTP_FROM_EMAIL` | Required production account-recovery email delivery. |
| `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_NAME`, `SMTP_STARTTLS` | SMTP connection settings as required by the mail provider. |
| `TRUSTED_PROXY_CIDRS` | Comma-separated CIDRs of the actual trusted ingress proxies only. Determine these for the selected platform/network; do not copy broad or guessed ranges. |

Production configuration rejects a non-HTTPS frontend/backend origin, SQLite, local Redis, local upload storage, missing S3 bucket, weak/default session secret, missing SMTP sender/host, insecure custom S3 endpoint, the development/default or wildcard proxy trust list, and live Stripe keys. Set `TRUSTED_PROXY_CIDRS` to verified ingress proxy ranges before starting production services.

### Optional provider and processing variables

Set only what the deployment will use. All provider credentials are backend-only; never name them `NEXT_PUBLIC_*` or set them in Vercel.

| Group | Variables |
|---|---|
| AI analysis | `AI_PROVIDER`, `AI_MODEL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`. Keep a custom `OPENAI_BASE_URL` under operator control and HTTPS. |
| Transcription | `TRANSCRIPTION_PROVIDER`, `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`, `WHISPER_MODEL_CACHE`. Local faster-whisper downloads/caches models in the worker's local cache; object storage remains the durable media store. |
| Processing/render | `PROCESSING_MAX_CLIPS`, `PROCESSING_TASK_MAX_RETRIES`, `PROCESSING_HEARTBEAT_STALE_SECONDS`, `PROCESSING_JOB_TIMEOUT_SECONDS`, `PROCESSING_MIN_CLIP_SECONDS`, `PROCESSING_MAX_CLIP_SECONDS`, `VIDEO_SAMPLE_INTERVAL_SECONDS`, `RENDER_WIDTH`, `RENDER_HEIGHT`, `RENDER_FPS`, `RENDER_VIDEO_BITRATE`, `MAX_UPLOAD_BYTES`. |
| Worker/account quotas | `CELERY_WORKER_CONCURRENCY`, `CREDITS_PER_SOURCE_MINUTE`, `CREDITS_PER_RENDERED_CLIP`, `FREE_MONTHLY_CREDITS`, `SESSION_TTL_SECONDS`, `PASSWORD_RESET_TTL_SECONDS`. |
| Billing | `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`. Leave unset for this preparation; if configured, production accepts only `sk_test_` / `pk_test_` credentials. No live payments are enabled. |
| YouTube OAuth | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, `SOCIAL_TOKEN_ENCRYPTION_KEY`. Leave unset until authorized test OAuth is explicitly approved. |
| Publishing schedule | `SOCIAL_SCHEDULE_POLL_SECONDS`. |
| Local/Compose only | `LOCAL_STORAGE_PATH`, `POSTGRES_PASSWORD`. |

Other settings and development defaults are listed in [`.env.example`](../.env.example). Never upload that file with real credentials; use the hosting providers' secret stores.

## Domains, cookies, origins, and callbacks

- `BACKEND_INTERNAL_URL` must be the API's HTTPS origin. It is read by Next.js server configuration; the browser continues using same-origin `/api/*` URLs.
- `FRONTEND_URL` must exactly match the Vercel origin and is the only FastAPI CORS origin. No wildcard origins are used; credentialed CORS is enabled only for that origin.
- `BACKEND_URL` must exactly match the API's HTTPS origin and is the accepted public Host. Localhost is retained for local health checks.
- Production session cookies are Secure, HttpOnly, SameSite=Lax, and host-only. The CSRF cookie is Secure, readable by the frontend, and paired with the CSRF request header. Same-origin proxying avoids cross-site cookie requirements; do not switch to `SameSite=None` or make browser calls directly to a different API domain without redesigning auth.
- Google OAuth must use the backend callback, not the frontend callback. Set `GOOGLE_OAUTH_REDIRECT_URI` and register the exact same value in the Google OAuth Web client:

  `https://<railway-api-domain>/api/social/oauth/youtube/callback`

- Generate `SOCIAL_TOKEN_ENCRYPTION_KEY` locally with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Keep it identical for API and workers and back it up securely; rotating/loss requires re-encryption or user reconnection.
- Configure and verify the actual proxy's client-IP forwarding before setting `TRUSTED_PROXY_CIDRS`. The current rate limiter is process-local, so do not scale the API horizontally until distributed rate limiting is provided.

## Persistent object storage and processing

Keep the S3 bucket private with public access blocked and encryption enabled. Grant only the API and worker identities the bucket/prefix operations needed for upload, read, and deletion. The application streams private media through authenticated API routes; do not make video objects public. API and workers may use separate ephemeral local working caches because S3 is the durable source of media artifacts. Configure lifecycle/retention and backups according to the media policy before accepting user files.

`/health` is process liveness. `/health/ready` verifies both PostgreSQL and Redis and returns HTTP 503 if either dependency is unavailable. It does not check S3 credentials, SMTP, AI providers, or worker heartbeat. Railway HTTP health checks apply to the API; monitor Celery worker/scheduler state and job heartbeats separately. Run only one Beat scheduler.

## Verification gates before deployment

Run local checks from the repository:

```powershell
Set-Location backend
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe -m compileall -q app tests migrations
.\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm ci
npm run lint
npm run build
npm audit
```

These are source/build checks, not a deployment. Before any public launch, separately verify PostgreSQL migrations/backup recovery, real Redis/Celery processing and restart recovery, private S3 upload/download/delete, SMTP account recovery, video upload size through Vercel's proxy path, API and worker health, and authorized OAuth using test accounts. No public deployment, service purchase, production OAuth, live payment, or paid provider call is authorized by this guide.
