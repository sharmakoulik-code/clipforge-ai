# ClipForge AI — AI Video Repurposer

ClipForge turns rights-cleared long-form video uploads into vertical short clips with captions and an interactive post-processing editor. This repository has evolved through five stages: the account/dashboard foundation, secure ingestion, resumable AI/video processing, SaaS/editor foundations, and social publishing foundations. See [Stage 2](docs/STAGE2.md), [Stage 3](docs/STAGE3.md), [Stage 4](docs/STAGE4.md), and [Stage 5](docs/STAGE5.md) for scope and verification boundaries.

> **Launch status:** local implementation and automated checks are available, but this is not a verified production deployment. Docker/Compose, live AI providers, Stripe, SMTP, cloud storage, and production operations have not been end-to-end tested. Stripe live keys are rejected in production configuration. Do not deploy publicly, enable live payments, or incur paid service charges without explicit approval.

## Repository layout

```text
frontend/                 Next.js App Router, React, TypeScript, Tailwind
backend/app/api/           Auth, projects/jobs, clips/editor, billing, preferences, admin
backend/app/processing/    Resumable video processing pipeline
backend/app/workers/       Celery jobs, retries and render versioning
backend/app/billing/       Plan catalog and credit reservations/settlement
backend/app/storage/       Local and S3-compatible storage adapters
backend/migrations/        Alembic schema migrations
backend/tests/             API, billing, editor and pipeline tests
docs/                      Architecture, environment and staged delivery notes
docker-compose.yml         Local development stack only
.env.example               Non-secret local configuration template
```

## Local development

Requirements: Python 3.11+, Node.js 22+, PostgreSQL, Redis, and FFmpeg/FFprobe. Docker Engine and the Compose plugin are the easiest local stack. The Dockerfiles have production-oriented defaults, while `docker-compose.yml` explicitly overrides the frontend to the Next.js development server and binds published ports to loopback.

1. Copy `.env.example` to `.env`.
2. Replace `SECRET_KEY` with a freshly generated value (`python -c 'import secrets; print(secrets.token_urlsafe(48))'`). Set a unique URL-safe `POSTGRES_PASSWORD`; the local Compose URL uses this value directly, so avoid URI-reserved characters unless URL-encoding it.
3. Run `docker compose up --build` and open <http://localhost:3000>. The API health endpoint is <http://localhost:8000/health/ready>.
4. Compose applies Alembic migrations before starting the API. PostgreSQL, Redis, uploaded media, and the local ASR model cache use named volumes.

Compose is for development only: its services use development settings and local credentials, even though the database/Redis ports are loopback-bound. Do not expose this stack as a public production deployment.

### Run frontend/backend directly

Create/activate a Python 3.11 virtual environment, install `backend/requirements.txt`, and install frontend dependencies with `npm ci`. Copy `.env.example` to `.env`, provide a unique `SECRET_KEY`, and configure the local PostgreSQL/Redis URLs. Then run:

```bash
(cd backend && PYTHONPATH=. alembic upgrade head)
(cd backend && PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000)
# another shell
(cd backend && PYTHONPATH=. celery -A app.workers.celery_app.celery worker --loglevel=INFO)
# another shell
(cd frontend && npm run dev)
```

The frontend proxies relative `/api/*` requests through `BACKEND_INTERNAL_URL` (default `http://127.0.0.1:8000`). The worker and API must share the same database, Redis, storage, and local media/model-cache volumes when using local storage/ASR. Real provider-backed processing requires explicitly configured provider credentials and incurs provider usage charges.

## Configuration and security

- Never commit `.env` or expose API, SMTP, Stripe, storage, or session secrets to browser code.
- Production mode (`APP_ENV=production`) requires a generated secret of at least 32 characters, HTTPS `FRONTEND_URL`, PostgreSQL, Redis, and SMTP. Use managed secrets, TLS, backups, persistent object storage, and a narrow `TRUSTED_PROXY_CIDRS` in a real deployment.
- `OPENAI_BASE_URL` supports OpenAI-compatible chat-completions endpoints and must use HTTPS. Keep the endpoint under operator control; provider credentials are sent to it.
- Stripe remains **test-mode only**. Set only `sk_test_` credentials until a separately approved production release; `sk_live_` is explicitly blocked in production configuration.
- Rate limits are currently process-local and are not sufficient for a multi-instance deployment. Refer to [Stage 4 launch gaps](docs/STAGE4.md).
- Password-reset email requires SMTP in production. Development may return a debug reset token to facilitate local testing.
- YouTube ingestion is public metadata-only. It does not download videos or bypass access controls; upload only assets you are authorized to process.

## Checks

```bash
(cd backend && PYTHONPATH=. ../.venv/bin/python -m compileall -q app tests migrations)
(cd backend && PYTHONPATH=. ../.venv/bin/pytest -q)
(cd backend && PYTHONPATH=. ../.venv/bin/alembic -c alembic.ini upgrade head && PYTHONPATH=. ../.venv/bin/alembic -c alembic.ini check)
(cd frontend && npm run lint && npm run build)
git diff --check
```

See [docs/STAGE4.md](docs/STAGE4.md) and [docs/STAGE5.md](docs/STAGE5.md) for implemented/partial/unverified features, provider support, setup requirements and pre-launch gaps. See [docs/RAILWAY.md](docs/RAILWAY.md) for the backend deployment checklist, and [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) for workspace/tool verification limitations.
