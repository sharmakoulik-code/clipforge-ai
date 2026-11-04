# Railway deployment (backend)

This guide deploys the FastAPI API and the two Celery processes as separate Railway services. PostgreSQL, Redis, and object storage are required for a durable production setup. Do not use `docker-compose.yml` as the Railway production definition; it is for local development.

> Railway account provisioning is not connected to this workspace. These steps prepare the repository/runtime, but the project and services must be created in the Railway dashboard (or Railway CLI after you authenticate there). Never paste provider secrets into chat or commit them.

## 1. Add infrastructure services

Create a Railway project from the GitHub repository `sharmakoulik-code/clipforge-ai`. Add managed PostgreSQL and Redis services. Ensure the Git source includes the commit containing this guide and the backend changes below.

Use Railway variable references (or copy the values from the Railway service pages) in each backend service:

- `DATABASE_URL` = the PostgreSQL service's `DATABASE_URL`
- `REDIS_URL` = the Redis service's `REDIS_URL`

The backend normalizes common `postgres://` and `postgresql://` URLs to the installed psycopg 3 driver.

## 2. Configure the API service

Create a service from the same repository with the repository root as its build context and `backend/Dockerfile` as the Dockerfile. Do not set the build context to `backend/`: the Dockerfile copies files from `backend/` relative to the repository root.

The Docker image installs FFmpeg and starts the API on Railway's `PORT` (defaulting to 8000 locally). On startup it applies Alembic migrations, then starts Uvicorn. The health-check path is `/health/ready`. Start with a single API replica; rate limiting is process-local and is not ready for multi-replica traffic.

Set at minimum:

```text
APP_ENV=production
SECRET_KEY=<unique random value, at least 32 characters>
FRONTEND_URL=https://<your-vercel-domain>
DATABASE_URL=<PostgreSQL service URL>
REDIS_URL=<Redis service URL>
SMTP_HOST=<your SMTP host>
SMTP_FROM_EMAIL=<verified sender address>
```

Generate a secret locally with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. Add optional SMTP username/password/port settings if your provider requires them. Production startup intentionally rejects missing SMTP, SQLite, non-HTTPS frontend URLs, or a weak/default secret.

After Railway creates a public domain for the API, check `https://<api-domain>/health/ready`; a healthy response includes `"status":"ready"` and `"database":"ok"`.

## 3. Add worker and scheduler services

Create two more services from the same repository and Dockerfile (`backend/Dockerfile`), using the same `DATABASE_URL`, `REDIS_URL`, and other backend runtime variables as the API. Set these service start commands:

- Worker: `celery -A app.workers.celery_app.celery worker --loglevel=INFO`
- Scheduler: `celery -A app.workers.celery_app.celery beat --loglevel=INFO`

Only the API service should run database migrations on startup. The worker and scheduler do not need public domains.

## 4. Configure uploads and provider integrations

Do not rely on a service's local filesystem for user uploads: ephemeral files can be lost on restart and are not shared between API and workers. Configure an S3-compatible bucket shared by the API and worker:

```text
STORAGE_PROVIDER=s3
AWS_S3_BUCKET=<bucket>
AWS_REGION=<region>
AWS_ACCESS_KEY_ID=<access key>
AWS_SECRET_ACCESS_KEY=<secret key>
AWS_ENDPOINT_URL=<optional S3-compatible endpoint>
AWS_S3_PREFIX=clipforge
```

Set whichever AI provider credentials the product should use (`OPENAI_API_KEY`, `GEMINI_API_KEY`, or `ANTHROPIC_API_KEY`). These are optional for boot, but processing needs a configured provider. For YouTube OAuth, configure all of `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, and `SOCIAL_TOKEN_ENCRYPTION_KEY`; use this callback URL in Google Cloud as well:

```text
https://<api-domain>/api/social/oauth/youtube/callback
```

Generate a Fernet key locally for `SOCIAL_TOKEN_ENCRYPTION_KEY` with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Do not enable OAuth until the public callback URL and encryption key are in place.

## 5. Point the Vercel frontend at the API

In the Vercel frontend project's environment variables, set:

```text
BACKEND_INTERNAL_URL=https://<api-domain>
```

In Railway, keep `FRONTEND_URL` set to the Vercel app's HTTPS origin. Redeploy the frontend after changing its variable, and redeploy the API after changing backend variables.

## Remaining launch checks

- Confirm backups and retention for PostgreSQL and object storage.
- Confirm the Redis plan supports the app's Celery queue and that all three backend services use the same Redis instance.
- Confirm SMTP delivery and password-reset links.
- Run one small, rights-cleared upload through the full API → worker → object storage path before inviting users.
- Confirm YouTube OAuth separately with a test channel. Other social platforms do not currently have publishing adapters.
- Review [Stage 4 launch gaps](STAGE4.md) and [Stage 5 provider limits](STAGE5.md). A successful container build is not a complete production-readiness check.
