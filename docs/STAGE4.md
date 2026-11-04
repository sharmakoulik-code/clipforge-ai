# Stage 4 — SaaS foundations and editor

Status as of 2026-11-04. This describes code and local checks, not a live commercial launch. Backend tests, frontend type/lint checks and production build, and Alembic checks have passed locally; Docker Compose and real provider/payment integrations have not been run here.

## Implemented in the repository

- **Plans and credits:** configurable plan catalog with validated portrait export limits/features, credit estimates, monthly free-credit lazy allocation, reservation/settlement/refund records, and idempotent atomic wallet updates. Uploads, retries, and edited renders reserve credits; successful work settles actual usage, and failed work refunds reservations.
- **Billing, test mode:** Stripe checkout, customer/subscription state, webhook handling and billing history use the Stripe API. The backend rejects `sk_live_` credentials in production configuration. Configure only Stripe test keys until a separately approved live launch. No live payment was made or tested.
- **Editor and versioned renders:** ownership-scoped clip edit/suggestion/version/restore APIs; source video preview and seek/timing controls; title, caption text/timing/style, crop, export filename, description and hashtags; render validation; and downloads. Completed new renders are stored as immutable render-version records, and restoring a prior render restores its saved render settings. Failed renders retain the previously active render. Test coverage uses local fixtures/mocks, not real production videos.
- **Admin:** server-side admin authorization for dashboard metrics, provider configuration/test requests, user/plan/credit controls, audit history, and stale-job inspection/recovery. Provider secrets are environment configuration and are not returned in API responses. OpenAI-compatible endpoints are supported via `OPENAI_BASE_URL` (HTTPS only), with model/provider selection in the admin configuration.
- **Worker recovery:** task retries/backoff, configurable provider request timeout, periodic worker heartbeats plus stage/failure metadata, stale-job recovery controls, and pipeline checkpoints. Recovery has unit/API coverage, but distributed multi-worker crash-recovery has not been stress-tested.
- **Account preferences/security:** profile/password/account deletion, activity timestamp, theme/notification preferences and authenticated settings. Local rate limiting is still process-local; use a shared rate-limit store before scaling horizontally.
- **Export policies:** account-plan-derived render dimensions and free-plan watermarking are applied to initial and edited renders. Plan settings are validated before use.
- **Deployment preparation:** backend image runs as a non-root user, frontend image builds/runs Next.js in production mode, Compose is explicitly for local development with loopback-only published ports, and production settings reject development secrets, insecure frontend URLs, SQLite, missing Redis/SMTP, and live Stripe keys.

## Verification performed

- `backend`: compileall and `pytest -q` — **44 passed**.
- Alembic upgrade through `0005_recovery_and_activity`, followed by `alembic check` — **No new upgrade operations detected**.
- `frontend`: `npm run lint` (TypeScript) and `npm run build` — passed; `npm audit` reported **0 vulnerabilities**.
- `git diff --check` — passed after the latest edit pass.

The checks above are local and do not establish availability or correctness of external services. The test suite uses mocks/local fixtures where provider, SMTP, Stripe, object-storage or queue services would otherwise be required.

## Partial or not verified

- No live OpenAI/Gemini/Anthropic/OpenAI-compatible or transcription request was run; no real provider key was used. Provider timeout and endpoint behavior still need an integration test using approved test credentials.
- No Stripe test checkout/webhook was run against Stripe, and no live billing is enabled. Payment reconciliation, tax, refunds, subscription cancellation, dispute handling, and customer-support operations need production design/review.
- SMTP delivery, production PostgreSQL/Redis, cloud object storage, backups/restore, TLS/reverse-proxy headers, and external secret management were not exercised.
- Docker/Compose is unavailable in this workspace. The Dockerfiles and Compose YAML have not been built/launched here; the Compose stack is a local-development aid, not a production deployment topology.
- Monthly free-credit allocation is lazy (on wallet access/credit flows), not a scheduled monthly job. Expiration and automated grant reconciliation remain future work.
- Rate limits are process-local. Multi-instance coordination, per-provider cost budgets/alerts, quotas, and observability dashboards remain incomplete.
- The editor has functional controls and render history; it is not a frame-accurate nonlinear editing suite. Caption word timings and cropping need broader real-footage QA.
- YouTube processing remains metadata-only. Users must provide a source file they are authorized to use; there is no download or access-control bypass.

## Before any public/paid launch

1. Obtain explicit approval for the target domain, deployment provider, budget, and whether live billing is permitted. No public deployment, paid service purchase, or live billing has been performed.
2. Generate independent production secrets; put them in the deployment platform's secret store. Set `APP_ENV=production`, HTTPS `FRONTEND_URL`, managed PostgreSQL/Redis, SMTP delivery, a real persistent object store, and a narrow trusted-proxy CIDR list.
3. Put the API behind TLS and a correctly configured proxy; verify secure session/CSRF behavior, trusted host/origin policy, upload limits, CORS, and rate limiting. Never expose database/Redis ports publicly.
4. Review privacy/retention, user deletion, terms, billing policies, tax/refunds, provider DPAs, content rights, incident response, and data backups. Test backup restore and deletion on staging.
5. Build and scan immutable images; run migrations as a one-off release step; launch API and workers separately; configure logs/metrics, queue depth, storage, and per-user/provider spend alerts.
6. Run a staging end-to-end test with a rights-cleared sample asset, provider test credentials, Stripe test mode, and SMTP test recipient. Verify recovery, refunds, render versions, cleanup, and support procedures.
7. Only after a separate explicit approval, configure live payment credentials and make a production release.
