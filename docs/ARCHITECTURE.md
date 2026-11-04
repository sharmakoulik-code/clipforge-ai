# Architecture (Stage 1)

## System overview

The repository is a modular monorepo. The Next.js App Router frontend is a same-origin-friendly web client; FastAPI owns the public API, authentication boundary, authorization, business rules, and all provider credentials. The frontend calls relative `/api` routes by default. Next.js rewrites those requests server-side to `BACKEND_INTERNAL_URL` (Compose uses the backend service name); production should route `/api/*` through its trusted reverse proxy. Never expose provider or storage secrets to `NEXT_PUBLIC_*` variables.

## Responsibilities and data flow

- **Frontend:** React/TypeScript, strict mode, Tailwind, Lucide, and the selected client libraries (TanStack Query for remote state, Zustand for small UI state, React Hook Form and Zod for forms). Current UI is a static foundation page, not a product dashboard.
- **API and auth:** FastAPI with Pydantic request/response schemas. Stage 1 deliberately does not implement login. Stage 2 should add a standards-based OIDC or email auth flow, short-lived access credentials, secure HTTP-only cookies, CSRF protection for cookie-authenticated mutations, and per-resource authorization. Passwords (if local auth is added) must be Argon2/bcrypt hashes only.
- **Database:** PostgreSQL with SQLAlchemy 2 and Alembic. UUID-like string primary keys are used for portability. Foreign keys and uniqueness constraints encode ownership and idempotency boundaries. Source and generated media are object-storage keys, never database blobs.
- **Uploads/storage:** API issues short-lived, constrained signed upload URLs for S3-compatible object storage; clients upload directly. Persist object key, size, checksum, MIME type, and metadata, then enqueue processing only after upload completion and validation. Local storage is development-only.
- **AI integrations:** provider adapters under `backend/app/ai/`; secrets remain server-side. Store provider/model, prompt/version metadata, and normalized output for traceability. Retry transient rate limits with bounded exponential backoff; do not retry invalid input or policy errors.
- **Background jobs:** Celery + Redis. API creates a durable ProcessingJob and ordered ProcessingStages, then enqueues an ID. Workers claim work idempotently; each stage writes status, output references, and a checkpoint before advancing. Redis is transport, not the source of truth.
- **Video processing:** isolated workers with FFmpeg/FFprobe and optional OpenCV/MediaPipe. Pipeline: Video Upload → Metadata Extraction → Audio Extraction → Transcription → AI Highlight Detection → Speaker Tracking → Vertical Cropping → Caption Generation → FFmpeg Rendering → Output Validation → Download. Model each step as a separately retryable stage. Write intermediates to object storage and checkpoints to the DB; resume from last completed stage, use deterministic output keys, validate codec/dimensions/duration before publishing, and clean up expired temporary artifacts.
- **Credits/billing:** CreditWallet is a cached balance; append-only CreditTransactions are the ledger. Reserve credits idempotently before expensive processing, settle actual usage or release reservations after completion/failure. Stripe events are deduplicated in WebhookEvents and processed asynchronously. Stage 1 defines data only; no charge or credit endpoints exist.
- **Administration:** role/permission checks on API endpoints; SystemConfiguration stores non-secret operational settings. Secrets belong in a secret manager/environment, never this table or logs. Admin actions should be auditable before shipping admin features.

## Model relationships

Users own Projects, wallets, subscriptions, payments, and usage. Projects hold source object references and have Transcripts, Clips, ProcessingJobs, and usage. Jobs have ordered ProcessingStages and resumable ProcessingCheckpoints. Clips refer to optional transcripts and have versioned ClipEdits and ClipRenderVersions. Wallets have append-only CreditTransactions. WebhookEvents are provider-event-idempotent; SystemConfigurations are keyed configuration records.

## Resilience and operational boundaries

Use request IDs and structured logs; never log secrets or signed URLs. API liveness (`/health`) is separate from readiness (`/health/ready`). Worker health and queue depth need deployment monitoring. Apply upload limits, content sniffing, malware scanning policy, signed URL expiry, tenant quotas, database transaction boundaries, retry caps, dead-letter handling, and retention controls before production. Stage 1 contains scaffolding only; authentication, real upload, rendering, external AI, payments, and admin workflows are intentionally out of scope.
