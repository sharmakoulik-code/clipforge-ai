# Stage 5 — Social publishing

Updated 2026-11-04. This documents the implementation and verification limits in this checkout; it is not a statement that social accounts have been authorized or that external publishing has been tested.

## Provider support

| Platform | OAuth/account connection | Publishing adapter | Status |
|---|---:|---:|---|
| YouTube | Yes (Google OAuth 2.0 authorization-code flow with PKCE) | Yes (YouTube Data API v3 resumable upload) | Implemented in code; requires Google project configuration, eligible test accounts/channels and any applicable verification or API audit. No real OAuth or upload was performed here. |
| Instagram | No | No | Catalog-only; not available for connection or publishing. |
| TikTok | No | No | Catalog-only; not available for connection or publishing. |
| Facebook Pages / Reels | No | No | Catalog-only; not available for connection or publishing. |
| LinkedIn | No | No | Catalog-only; not available for connection or publishing. |
| X | No | No | Catalog-only; not available for connection or publishing. |
| Pinterest | No | No | Catalog-only; not available for connection or publishing. |

The catalog intentionally distinguishes future platform entries from working integrations. Unsupported accounts cannot be connected or published to, and no simulated success is returned.

## Implemented behavior

- **Accounts and credentials:** server-side OAuth state is random, hashed at rest, expires after ten minutes and is consumed once. The flow requests `openid`, `email`, `profile`, YouTube read-only channel access, and YouTube upload access. Access and refresh tokens are Fernet-encrypted before storage. Disconnect removes local credentials and attempts Google revocation when no other connected channel shares the refresh grant; the response reports whether remote revocation was confirmed.
- **Content drafts:** a rendered, non-empty MP4 clip owned by the signed-in user is required. Drafts are account-specific and idempotent. The editor provides editable YouTube title, caption, description, hashtags, privacy selection and an explicit made-for-kids declaration. Privacy defaults to **private**; the user must choose the audience declaration before saving/publishing. Generated text is a draft, not an automatically approved post.
- **AI metadata:** uses the transcript segments overlapping the selected clip, plus available clip analysis fields (title, hook, category and editorial reason). It does **not** perform semantic visual understanding or claim to verify what appears in frames. Platform-specific length limits are checked; metadata is saved as versioned draft content. Usage limits and configured credit charges are applied; a failed generation is refunded when the charge was committed and refund succeeds.
- **Publish and schedule:** immediate publish queues one worker task per account target. Single and bulk scheduling store UTC instants plus the IANA timezone used to interpret the submitted local wall time. Nonexistent daylight-saving times are rejected; ambiguous local times use the earlier occurrence. Calendar and queue/history views show per-target state, attempts, provider errors, external links and audit events. Users can cancel/pause/resume eligible scheduled targets or retry definite failures.
- **Safety and recovery:** ownership checks cover clips, posts, accounts and targets. Idempotency keys prevent duplicate post/target creation and the worker atomically claims a queued target. Provider failures are classified into retryable, definite failure, reauthentication or uncertain outcomes. An uncertain upload result is held for manual reconciliation and is not blindly retried. Celery Beat dispatches due work and recovers expired worker leases; run the `social-scheduler` service as well as API, worker, Redis and database.
- **Plan controls:** the admin plan catalog includes connected-account count, scheduled-post count, monthly publishing volume, AI metadata generation count, credit charge and bulk-scheduling availability. Scheduled work reserves monthly publishing volume in the month it is due; immediate work reserves the current month. Limits are enforced server-side.

## Setup for a Google/YouTube test project

1. Create or select a Google Cloud project for the application. Enable **YouTube Data API v3** and configure an OAuth consent screen appropriate to the intended test audience. Add only authorized test users while the consent screen is in testing. Complete any verification/audit requirements Google presents before relying on unlisted/public uploads or wider availability; Google can limit uploads from unreviewed projects.
2. Create a Google OAuth **Web application** client. Add the backend callback URI exactly as configured below, for example `http://localhost:8000/api/social/oauth/youtube/callback` for local development. Production requires the exact HTTPS backend callback URI registered with Google. Do not use a frontend callback URL.
3. Copy `.env.example` to the local environment file. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and the exact `GOOGLE_OAUTH_REDIRECT_URI`. Generate a unique `SOCIAL_TOKEN_ENCRYPTION_KEY` using:

   ```sh
   cd backend
   .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   Keep this key stable, private and backed up in a secret manager. Losing or rotating it without decrypt-and-reencrypt migration makes stored tokens unreadable; users will need to reconnect after an unrecoverable loss. Never commit credentials or use a production key in development.
4. Configure the existing database and Redis settings as described in [Environment setup](ENVIRONMENT.md). Apply migrations with `cd backend && alembic upgrade head`. For Compose development, start the application services including the `social-scheduler` Beat service. Celery workers must use the same database, Redis, token-encryption key and media storage as the API.
5. Start the web application and API, sign in, open **Social Publisher → Connected accounts**, connect a Google account authorized for the consent screen, and approve the listed scopes. Verify that the account appears as connected. A callback alone is not proof that YouTube API access or upload eligibility is fully approved.
6. Generate a clip using the existing workflow, wait until its render is complete, create a social draft, generate or edit content, and review every field. For a low-risk first API test, publish with **Private** visibility to an authorized test channel. Confirm the resulting per-target history and the uploaded video in YouTube Studio. This manual test is not claimed to have been run in this workspace.

Do not share `.env` files, OAuth client secrets, refresh tokens, or the Fernet key. Use Google’s official revocation/account-security controls if consent needs to be removed.

## Local verification and known limitations

- Five mocked Stage 5 tests cover OAuth one-time state/PKCE and encrypted tokens, resumable upload request construction, clip-scoped metadata with idempotent charges/refunds, ownership/quota/timezone rules, and Celery Beat dispatch plus idempotent worker completion/audit/usage recording.
- Frontend TypeScript checking and production compilation pass locally. Alembic migration drift check passes against the local SQLite configuration.
- Backend Stage 5 tests use mocks/local SQLite; they do not establish Google authorization, YouTube upload acceptance, provider quota availability, refresh-token behavior in production, or live social publishing. No real Google credentials or authorized channel are configured in this workspace.
- The backend suite has **46 passed, 3 failed**: the only failures are three existing FFmpeg integration tests that require system `ffmpeg`/`ffprobe`, absent in this workspace. Excluding those 3 tests produces 46 passes.
- PostgreSQL/Redis/Celery runtime behavior and Compose container builds were not run here because Docker is unavailable. Concurrency behavior should be exercised against the intended PostgreSQL/Redis deployment before launch.
- YouTube API project verification, quotas, upload restrictions, branding/consent requirements, privacy obligations, production retention/deletion, token rotation, monitoring and support procedures must be reviewed for the deployment’s Google project. No public deployment, paid service activation, live payment, or external publishing was performed.

## Configuration reference

- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`: server OAuth client configuration.
- `SOCIAL_TOKEN_ENCRYPTION_KEY`: Fernet key for encrypted OAuth credentials.
- `SOCIAL_SCHEDULE_POLL_SECONDS`: Celery Beat polling interval (default 30 seconds).
- `FRONTEND_URL`: OAuth completion redirect destination. Use only an owned, trusted HTTPS domain in production.

Social routes are mounted under `/api/social`; the browser uses the existing same-origin `/api` proxy convention.
