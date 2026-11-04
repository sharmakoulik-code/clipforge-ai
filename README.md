# AI Video Repurposer

Stage 2 of a four-stage project: an original creator SaaS shell with public marketing pages, account/session security, database-backed dashboard, local video ingestion, video metadata, ownership-scoped projects, and an asynchronous Celery/Redis job foundation. The actual transcription, highlight detection, clip editor/rendering, billing, and admin console are intentionally not implemented yet.

## Repository layout

```text
frontend/                 Next.js 16 App Router, React, TypeScript, Tailwind
  app/                     Marketing, auth, dashboard, upload, project, settings routes
  components/              Reusable theme, auth, dashboard and UI components
  lib/ services/            Client utilities and typed API/upload services
  store/ hooks/             State and extension points
backend/
  app/api/                  Auth, project/upload, dashboard, job and admin-health routes
  app/core/                 Settings, database, security, rate limiting, upload guard
  app/models/               SQLAlchemy persistence models
  app/{ai,video,workers,storage,billing}/ modular adapters and task extensions
  migrations/versions/      Alembic baseline and Stage 2 migration
  tests/                    Backend tests
docs/ARCHITECTURE.md       Current architecture and Stage 3 boundaries
docs/STAGE2.md             Delivered features, verification and remaining limits
docs/ENVIRONMENT.md        Arena/GitHub capability report
docker-compose.yml         Frontend, API, worker, PostgreSQL, Redis
.env.example               Configuration template; no credentials
```

## Local development

Full-stack development requires Docker Engine and the Docker Compose plugin. Node.js 20+ and Python 3.11+ are required to run the frontend/backend directly. FFmpeg/FFprobe is included in the backend image.

1. Copy `.env.example` to `.env`. Set a unique `SECRET_KEY` (at least 16 characters). Keep `.env` private and never commit it.
2. Start the stack with `docker compose up --build`.
3. Open the app at <http://localhost:3000>, API docs at <http://localhost:8000/docs>, and readiness at <http://localhost:8000/health/ready>.
4. Compose migrates the database before the API starts. PostgreSQL, Redis, and uploaded local media use persistent volumes shared with the worker where needed.

### Run without Docker

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env  # then set SECRET_KEY; configure PostgreSQL/Redis if available

(cd backend && PYTHONPATH=. alembic upgrade head)
(cd backend && PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000)
```

In another shell, run `cd frontend && npm install && npm run dev`. Next.js serves on port 3000 and rewrites relative `/api/*` calls to `BACKEND_INTERNAL_URL` (default `http://127.0.0.1:8000`). The Arena preview currently serves the frontend at port 8001 and the API at port 8002; this is a preview-only port arrangement.

The backend defaults to SQLite for local smoke tests, but production should use PostgreSQL. Redis is required for Celery dispatch and worker processing. Without it, uploads remain persisted and the job is visibly marked `queue_unavailable`; retry after the queue is available. Local video uploads require FFmpeg/FFprobe on the host, or use the included Docker backend image.

## Authentication and security

Registration requires a valid email, display name, and password of at least 12 characters. Passwords use Argon2id. The server stores hashed opaque session tokens and sets HTTP-only, SameSite=Lax cookies (Secure in production); protected mutations also validate a signed CSRF token. Password reset tokens are hashed, single-use, expiring, and revoke sessions. Configure `SMTP_*` for production password-reset delivery; development returns a debug reset token solely to make local testing possible. Never expose SMTP, AI, storage, or payment credentials in frontend variables.

Rate limits are process-local in this stage, not distributed. Configure `TRUSTED_PROXY_CIDRS` narrowly behind a proxy. Local storage is the only implemented storage provider; files are stored outside PostgreSQL under opaque keys and are removed when their project is deleted.

## Stage 2 API surface

- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
- `POST /api/auth/password-reset`, `POST /api/auth/password-reset/confirm`, `PATCH /api/auth/profile`
- `GET /api/dashboard`
- `POST /api/projects/upload` (multipart), `POST /api/projects/youtube` (metadata reference only)
- `GET /api/projects`, `GET /api/projects/{project_id}`, `PATCH /api/projects/{project_id}`, `DELETE /api/projects/{project_id}`
- `GET /api/projects/{project_id}/status`, `GET /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/retry`

The YouTube route may request public oEmbed metadata, but it does not download video or bypass access controls. Users must upload an authorized source file. The Stage 2 Celery worker verifies stored media and leaves the job queued for Stage 3's AI pipeline; it does not generate clips.

## Quality checks

```bash
(cd frontend && npm run build && npm run lint && npm audit)
(cd backend && PYTHONPATH=. ../.venv/bin/pytest -q)
(cd backend && PYTHONPATH=. ../.venv/bin/alembic -c alembic.ini upgrade head)
```

See [`docs/STAGE2.md`](docs/STAGE2.md) for exact test and integration results, known limitations, and the end-to-end upload smoke test. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) records data ownership and the Stage 3 pipeline plan.
