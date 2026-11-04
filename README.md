# AI Video Repurposer

Stage 1 foundation for a commercial AI video repurposing SaaS. The codebase is a modular monorepo with a Next.js App Router frontend, FastAPI backend, PostgreSQL/Alembic persistence, Redis/Celery worker configuration, and Docker Compose development environment. Product workflows (authentication, uploads, AI processing, editor, billing, and administration) are intentionally not implemented in this stage.

## Repository layout

```text
frontend/                 Next.js + TypeScript + Tailwind scaffold
  app/                     App Router pages and global styles
  components/ hooks/        UI extension points
  lib/ services/            Utilities and API client
  store/                    Zustand UI state
backend/
  app/api/                  FastAPI routes
  app/core/                 Settings and database setup
  app/models/               SQLAlchemy foundation models
  app/{ai,video,workers,storage,billing}/ service extension points
  migrations/               Alembic environment and initial schema revision
  tests/                    Backend tests
docs/ARCHITECTURE.md       Decisions, model relationships, pipeline and resilience
docs/ENVIRONMENT.md        Verified Arena capabilities and test status
docker-compose.yml         Frontend, API, worker, PostgreSQL and Redis
.env.example               Configuration template; contains no real secrets
```

## Requirements and local setup

For the full containerized stack: Docker Engine + Docker Compose plugin. Docker is not required for isolated frontend/backend checks. Node.js 20+ and Python 3.11+ are required for running those services directly. FFmpeg is installed in the backend image; install FFmpeg/FFprobe locally if running video-related backend code outside Docker.

1. Copy `.env.example` to `.env`; set a unique `SECRET_KEY` (at least 16 characters) and local values. Never commit `.env`.
2. Start dependencies and services: `docker compose up --build`.
3. Frontend: <http://localhost:3000>. API docs: <http://localhost:8000/docs>. Readiness: <http://localhost:8000/health/ready>.
4. Compose runs Alembic migrations before starting the API. PostgreSQL and Redis use persistent named volumes.

### Run services separately

- Frontend: `cd frontend && npm install && npm run dev`
- Backend: `python3 -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt`; run from `backend/` with `PYTHONPATH=. uvicorn app.main:app --reload`.
- Migrations: from `backend/`, `PYTHONPATH=. alembic upgrade head` (configure `DATABASE_URL` first; the default local fallback is SQLite solely for basic development checks).
- Tests: from `backend/`, `PYTHONPATH=. pytest`.

The Compose configuration expects `.env` to exist. PostgreSQL and Redis credentials in the example Compose file are for local development only. Production must use managed services, secret management, TLS, backups, and hardened credentials.

## Configuration

`.env.example` lists application URLs, DB/Redis, AI/transcription providers, object storage, and Stripe placeholders. All credentials are server-side. The frontend client uses relative `/api` URLs; Next.js rewrites those requests to `BACKEND_INTERNAL_URL` during local development/Compose. Production should route `/api/*` through its trusted reverse proxy. Stage 1 does not call any external provider.

## Quality checks

`cd frontend && npm run build`

`cd backend && PYTHONPATH=. pytest`

Docker validity, PostgreSQL/Redis integration, and full Compose startup depend on Docker being installed and running. See `docs/ARCHITECTURE.md` for system boundaries and the recoverable processing pipeline.
