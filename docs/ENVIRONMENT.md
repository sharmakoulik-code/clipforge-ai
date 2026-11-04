# Arena environment and GitHub capability report

Checked during Stage 1/2 on 2026-11-04. “Verified” means an operation was run in this workspace; “unverified/unavailable” means no successful integration test was possible.

## Repository and GitHub

- The checkout is `/home/user/clipforge-ai`, remote `https://github.com/sharmakoulik-code/clipforge-ai.git`, based at `277ff7664b4cff398532a7de04c6612ec90079e7`.
- GitHub CLI authentication is active for `sharmakoulik-code`; the remote `main` branch is accessible. Account listing reports `clipforge-ai` as public.
- The requested `ai-video-repurposer` repository was not found under this connected account. The Arena session is bound to `arena/01abe54e-clipforge-ai`; no repo/branch switch was made. If another owner/repository was intended, connect a new Arena workspace to it.
- Stage 2 commit `cce429d46503e23eaaa766d4686456ba84c295d5` was pushed to `origin/arena/01abe54e-clipforge-ai`; `git ls-remote` confirmed the ref. It is not a commit to `main`.

## Verified local capability matrix

| Capability | Result | Evidence / limits |
|---|---|---|
| Read/create/modify files, terminal, Git | Verified | Inspected Stage 1 first; Stage 2 files and docs updated. |
| GitHub access | Verified | `gh auth status`, `git ls-remote`, and prior Stage 1 push succeeded. |
| Node/npm install | Verified | Dependencies installed from npm registry. |
| Python install | Verified | Python 3.11 virtual environment and backend packages installed from PyPI. |
| Next.js | Verified | Next.js 16.4.5 production build and strict TypeScript check pass. Dev preview runs on port 8000. |
| FastAPI | Verified | Uvicorn runs on port 8001; health and auth/project API calls work through the frontend rewrite. |
| Automated tests | Verified | Backend pytest: 17 passed, with one Starlette/httpx deprecation warning. Frontend build/type-check passed; npm audit found zero vulnerabilities. |
| PostgreSQL | Verified with temporary test server | Embedded PostgreSQL 18.6 was started from a PyPI wheel; both Alembic migrations applied and the expected tables were inspected. Compose PostgreSQL 16 was not run. |
| Redis/Celery | Verified with temporary test service | Redis-compatible 6.2.14 and Celery 5.6.3 worker ran; task dispatch and DB status update were observed. Compose Redis 7 was not run. |
| FFmpeg/FFprobe | Verified with temporary test binaries | PyPI-provided binaries were used to generate and probe a real MP4; actual upload/metadata flow passed. Not added to repository dependencies; the backend Dockerfile installs FFmpeg. |
| Docker / Compose | Unavailable | Docker and Compose executables are absent. YAML parses successfully with PyYAML, but Compose was not launched. |
| SMTP / mail delivery | Not live-tested | SMTP adapter and settings are implemented; no SMTP credentials/server were supplied. Adapter path is tested with a mock. |
| YouTube oEmbed | Not live-tested | URL/rights/response paths are tested with a mock. Sandbox outbound access does not establish YouTube API connectivity. No video download is performed. |
| AI, Stripe, cloud object storage | Not implemented/tested in Stage 2 | Provider credentials were not supplied; AI/video generation, billing, and S3/R2 adapters remain later work. |

## Integration checks

- A real one-second MP4 was uploaded through Next.js `/api` to FastAPI; FFprobe metadata, DB project/job persistence, Redis dispatch, Celery source validation, project rename, and deletion of the stored file were verified.
- Auth/session, dashboard, protected APIs, CSRF validation, invalid upload rejection, ownership checks, and retry behavior were exercised by tests and/or live HTTP checks.
- Alembic migrations were tested on SQLite and temporary PostgreSQL 18.6. Exact Compose PostgreSQL 16 runtime remains unverified.

## Current preview

- Frontend: `0.0.0.0:8001` (Arena app preview).
- FastAPI: `0.0.0.0:8002`.
- Redis test service: `0.0.0.0:6379`.

The local binary/process setup above is for verification only; it does not change committed dependencies. See `docs/STAGE2.md` for product scope and limitations.
