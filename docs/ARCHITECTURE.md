# Architecture

## System overview

The repository is a modular monorepo. Next.js App Router serves the marketing site and authenticated workspace; FastAPI owns authentication, authorization, project/video metadata, dashboard data, uploads, and job orchestration. The browser calls same-origin relative `/api` routes. Next.js rewrites them server-side to `BACKEND_INTERNAL_URL` (Compose uses `http://backend:8000`); production should route `/api/*` through a trusted reverse proxy. Provider credentials remain server-side and are never placed in `NEXT_PUBLIC_*` variables.

## Stage 2 boundaries

### Frontend

React/TypeScript strict mode, Tailwind with a class-driven light/dark theme, Lucide, TanStack Query for server state, Zustand for small UI state, React Hook Form/Zod dependencies, reusable UI primitives, and centralized typed API services. Public routes provide marketing, FAQ, and pricing preview. Protected routes query `/api/auth/me` and show dashboard, upload, project detail, and account settings. API cookies are same-origin and auth expiry returns the browser to sign-in. Upload uses `XMLHttpRequest` for transfer progress and cancellation.

### Authentication and authorization

Registration hashes passwords with Argon2id. Login creates a high-entropy opaque token, stores only its SHA-256 digest in `user_sessions`, and sends the token in an HTTP-only, SameSite=Lax cookie (Secure in production). Sessions are expiring and revocable. Mutating authenticated requests require a signed double-submit CSRF cookie and matching `X-CSRF-Token` header. Protected project/job queries always filter through the authenticated user; inaccessible IDs return 404. `is_admin` and an admin-only API dependency are established; there is no public role escalation. Login, registration, reset, upload, and URL import have in-process rate limits. Production with multiple API workers should move these limits to Redis or an edge gateway.

Password reset tokens are random, stored only as hashes, expire, are one-use, and revoke current sessions. Development returns the token for local use. Production sends it through the configured SMTP adapter; SMTP host/from details and credentials must be configured. Email delivery is not enabled in this workspace without real SMTP settings.

### Persistence and object storage

PostgreSQL is the production database, accessed through SQLAlchemy 2 sessions and Alembic. SQLite remains a local/test fallback. `users`, `projects`, `processing_jobs`, and pipeline foundations are linked by foreign keys; uploaded bytes never go into PostgreSQL. The local storage adapter uses generated UUID-based keys, confines resolved paths to its configured root, and streams upload chunks to disk. Compose mounts a persistent media volume shared by API and workers. The adapter protocol is the extension point for a future S3/R2 implementation; only local storage is implemented now.

Uploads accept a configured size cap (2 GiB default), an allowlist of video container extensions, container signature checks, and FFprobe validation. User filenames are sanitized for display and never form storage paths. FFprobe extracts duration, dimensions, normalized aspect ratio, frame rate, video/audio codec information, and file size. An ASGI upload-body middleware rejects oversized requests before multipart parsing. Delete removes the project-owned local source after the DB delete commits.

### YouTube references

URLs are strictly parsed for approved YouTube hosts and video URL shapes; the user must confirm rights. The server may request public metadata through YouTube's oEmbed endpoint. It does not download video, scrape private content, bypass DRM/access controls, or use `yt-dlp`. A YouTube reference becomes an `awaiting_upload` project; the user must upload an authorized source before processing.

### Jobs and future processing

FastAPI persists `ProcessingJob`/`ProcessingStage` records before publishing a Celery message to Redis. The worker validates the source key and marks the upload-ready checkpoint; jobs stay `queued` at `awaiting_stage_3`. If Redis is offline at dispatch, a persisted job is marked failed with `queue_unavailable` and can be retried when the worker is available. Job and retry endpoints enforce project ownership. Dashboard polling reads backend state.

The future processing state vocabulary is `queued`, `uploading`, `extracting_audio`, `transcribing`, `analyzing`, `detecting_speakers`, `cropping`, `captioning`, `rendering`, `completed`, and `failed`. Stage 3 will implement the pipeline: Video Upload → Metadata Extraction → Audio Extraction → Transcription → AI Highlight Detection → Speaker Tracking → Vertical Cropping → Caption Generation → FFmpeg Rendering → Output Validation → Download. Each stage should be independently retryable, checkpoint its inputs/outputs and artifacts, and use deterministic object keys. The Stage 2 worker deliberately does not run AI or render clips.

### Dashboard, credits, billing, and admin

Dashboard counters, processing history, project lists, and export lists come from database queries scoped to the current user. Wallet/transaction, subscription, payment, edit, render, webhook, usage, and system configuration models remain available from Stage 1. No real billing, credit charging, clip editor, or admin console is implemented in Stage 2.

## Operational requirements before production

Use TLS, a secret manager, non-default DB/Redis credentials, backups, structured request-aware logs, worker/queue monitoring, and distributed rate limiting. Configure SMTP for password resets. Add malware scanning and stronger codec/size/duration policies as needed. Test migrations on the exact PostgreSQL deployment version. Set trusted proxy CIDRs narrowly. Production object storage, upload resumability, registration email verification, audit events, and AI adapters remain future hardening/work items.
