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

PostgreSQL is the production database, accessed through SQLAlchemy 2 sessions and Alembic. SQLite remains a local/test fallback. `users`, `projects`, `processing_jobs`, `processing_stages`, `processing_checkpoints`, transcripts, clips, render versions, and usage records are linked by foreign keys; media bytes never go into PostgreSQL. Local storage confines opaque keys to its configured root. The S3-compatible adapter supports configured buckets/endpoints and a shared local worker cache; API and worker containers must share the configured media/cache volume. `.env.example` documents both providers.

Uploads accept a configured size cap (2 GiB default), an allowlist of video container extensions, container signature checks, and FFprobe validation. User filenames are sanitized for display and never form storage paths. FFprobe extracts duration, dimensions, normalized aspect ratio, frame rate, video/audio codec information, and file size. An ASGI upload-body middleware rejects oversized requests before multipart parsing. Project deletion cascades to the source and derived artifacts in local storage or the configured S3-compatible bucket.

### YouTube references

URLs are strictly parsed for approved YouTube hosts and video URL shapes; the user must confirm rights. The server may request public metadata through YouTube's oEmbed endpoint. It does not download video, scrape private content, bypass DRM/access controls, or use `yt-dlp`. A YouTube reference becomes an `awaiting_upload` project; the user must upload an authorized source before processing.

### Jobs and Stage 3 processing

FastAPI persists `ProcessingJob`, `ProcessingStage`, and `ProcessingCheckpoint` records before publishing a Celery message to Redis. The worker executes source validation, FFmpeg audio extraction/normalization, configured transcription, validated transcript analysis, estimated engagement scoring, sampled OpenCV/optional MediaPipe face tracking, smoothed 9:16 cropping, ASS captions, FFmpeg H.264/AAC rendering, thumbnails/previews, and FFprobe output validation. Completed stages and artifacts are cached; retries skip successful stages and reuse transcript/analysis cache entries. Stage functions are callable independently with `python -m app.processing.cli JOB_ID STAGE`; prerequisites must already be persisted. Job and retry endpoints enforce project ownership, and project pages poll real backend status.

Whisper choices are the OpenAI API or a cached/local faster-whisper model. Analysis uses modular HTTP adapters for OpenAI, Gemini, and Claude. Credentials and model weights are external runtime requirements; failures are surfaced and no clips are invented. Engagement is a 0–100 editorial estimate with seven weighted components, not a probability of virality. Speaker activation is heuristic: when uncertain, the renderer uses a conservative center crop; it does not infer speech solely from face size. Current diarization and conversation split-screen support are not enabled.

### Results and exports

The project results view queries saved clip records and exposes vertical previews, thumbnails, source timestamps, duration, category, score components, transcript, category/sort filters, edit-and-rerender controls, single downloads, and multi-clip ZIP downloads. Clips become completed only after FFprobe confirms a vertical H.264 MP4 with an expected duration. Source video, normalized audio, transcript/analysis JSON, crop tracking, ASS captions, rendered MP4s, thumbnails, and previews are stored via the storage adapter.

### Dashboard, credits, billing, and admin

Dashboard counters, processing history, project lists, and export lists come from database queries scoped to the current user. Wallet/transaction, subscription, payment, edit, render, webhook, usage, and system configuration models remain available from Stage 1. No real billing, credit charging, clip editor, or admin console is implemented in Stage 2.

## Operational requirements before production

Use TLS, a secret manager, non-default DB/Redis credentials, backups, structured request-aware logs, worker/queue monitoring, and distributed rate limiting. Configure SMTP for password resets. Add malware scanning and stronger codec/size/duration policies as needed. Test migrations on the exact PostgreSQL deployment version. Set trusted proxy CIDRs narrowly. Distributed rate limits, upload resumability, registration email verification, audit events, production provider credentials/model cache provisioning, worker isolation, and GPU capacity planning remain operational hardening items.
