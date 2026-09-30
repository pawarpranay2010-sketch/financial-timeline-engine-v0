# Production Deployment

This project is a **long-running FastAPI + uvicorn web service** (not serverless).
It serves the standalone frontend from `frontend/` at `/` and exposes the API
under `/api/v1/*`. The Phase 6 intelligence/extraction/Agentic RAG pipeline runs
server-side; PostgreSQL is the datastore; provider/AI credentials are server-side
environment secrets only.

## Production start command

```
uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-5000}
```

- The process reads the host's `PORT` environment variable (no hard-coded port
  in production). The `Procfile` already encodes this command.
- Python version is pinned via `runtime.txt` (`python-3.10.12`).
- Dependencies install from `requirements.txt`.

## Required environment variables (server-side secrets)

| Variable | Required | Purpose |
|---|---|---|
| `PORT` | platform-provided | HTTP port the platform assigns |
| `DATABASE_URL` | **yes** | PostgreSQL connection string, e.g. `postgresql://user:pass@host:5432/db` (managed Postgres) |
| `FMP_API_KEY` | for live data | Financial Modeling Prep key |
| `FINNHUB_API_KEY` | for live data | Finnhub key |
| `ALPHA_VANTAGE_API_KEY` | for live data | Alpha Vantage key |
| `REDIS_URL` | optional | Redis cache (app degrades gracefully when absent) |
| `GOOGLE_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `RAPIDAPI_KEY`, `SAMBANOVA_API_KEY`, `GITHUB_TOKEN`, `CEREBRAS_API_KEY`, `COHERE_API_KEY` | optional | AI gateway keys (only those you configure) |
| `PLATRIXA_MODEL_ENDPOINT_URL` | for model path | HTTPS base URL of the remote inference service (no trailing slash): the Modal service OR the HF Space (`https://pranay-20-platrixa.hf.space`). When set, the Kernel uses a remote provider; when unset, the in-process `LocalHFModelProvider` is used (Render Free cannot load the model — see Phase 7R section) |
| `PLATRIXA_MODEL_TRANSPORT` | optional | Remote transport selection: `http` (default — Modal `POST <url>/interpret`) or `gradio` (HF ZeroGPU Space named API `/interpret_core` via `gradio_client`) |
| `PLATRIXA_MODEL_ENDPOINT_TOKEN` | optional | Bearer token if the Modal endpoint uses proxy auth |
| `PLATRIXA_MODEL_TIMEOUT` | optional | HTTP timeout in seconds for remote calls (default 60 http / 120 gradio — ZeroGPU cold start observed up to ~90 s) |
| `HF_TOKEN` | optional | Hugging Face token for token-gated Spaces (read from the environment by the gradio transport; never printed or logged) |

### Admission & quota variables (Phase 15 / 16 / 5G / 5E / 5H)

These control **who may call the API, how much each tenant may spend, and
whether the replay/async/observability features exist at all**. A deployment
with none of them set serves an *open, unmetered* `/v1` surface with idempotency,
async documents, key management and webhooks switched off.

| Variable | Required | Purpose |
|---|---|---|
| `PLATRIXA_METERING_DATABASE_URL` | **for a metered deployment** | PostgreSQL URL for the admission/quota store. **This is the variable that turns metering ON.** There is deliberately no `DATABASE_URL` fallback (an implicit fallback would fail-closed every request on a host that merely has an ambient `DATABASE_URL`). All four Phase 5 stores (tenants/quota, idempotency, async jobs, request log) share this one database and one engine. |
| `PLATRIXA_DEV_API_KEY` | alternative to metering | Phase 15 shared break-glass key. If set it is the **only** accepted `/v1` data-plane credential (see precedence below). Intended for a single-operator deployment or as the emergency credential while metering is being provisioned. |
| `PLATRIXA_KEY_MANAGEMENT_TOKEN` | for key management | Operator-issued bearer token for the Phase 5G management plane (`/v1/developer/api-keys`). Sent as `X-Platrixa-Management-Token`. Without it the management plane returns `400 API_KEY_MANAGEMENT_NOT_CONFIGURED`. |
| `PLATRIXA_KEY_MANAGEMENT_TENANT_ID` | with the token above | Binds the management token to exactly one tenant. A token without a tenant binding fails closed with `503 API_KEY_MANAGEMENT_UNAVAILABLE`. |
| `PLATRIXA_WEBHOOK_SIGNING_KEY` | for webhooks | Server-side key used to seal/unseal per-endpoint webhook signing secrets. Without it `POST /v1/webhook-endpoints` returns `400 ASYNC_NOT_CONFIGURED` and no endpoint can be registered. |
| `PLATRIXA_PUBLIC_BASE_URL` | recommended for async | Public origin (e.g. `https://api.example.com`) used to build the absolute `status_url` / `result_url` returned by `POST /v1/documents`. Unset, those fields are relative paths (`/v1/jobs/{id}`), which only resolve for same-origin clients. |
| `PLATRIXA_REQUEST_LOG_RETENTION_DAYS` | optional | Request-metadata retention window for `platrixa_request_log` (default 30). Rows are pruned by the operator (`backend.auth.request_log.prune_older_than`), never silently. |
| `PLATRIXA_RULE_PACK_PATH` | optional | Server-side path to a trusted rule-pack YAML file. Clients can never supply rule code or a path through the API. |
| `PLATRIXA_TRUSTED_PROXY_COUNT` | **when behind a proxy** | Number of trusted proxy hops before the client address in `X-Forwarded-For`. Default `0` keys the rate limiter on the transport peer address — behind a CDN/load balancer that means ONE shared bucket for every visitor. Set it to your actual hop count. |
| `PLATRIXA_V1_RATE_LIMIT`, `PLATRIXA_V1_RATE_WINDOW` | optional | `/v1/*` per-key rate limit (default 600 requests / 60 s). |
| `PLATRIXA_API_V1_RATE_LIMIT`, `PLATRIXA_API_V1_RATE_WINDOW` | optional | `/api/v1/*` per-key rate limit (default 30 requests / 60 s) — the browser-facing surface. |

#### Identity precedence (deterministic — Phase 5I)

Exactly one credential boundary is in force, decided in this order, never as a
fallback chain:

| Mode | Condition | `/v1` data plane accepts | Charged? |
|---|---|---|---|
| `phase15-shared-key` | `PLATRIXA_DEV_API_KEY` set | **only** the exact shared key (byte-for-byte, constant-time; a value with surrounding whitespace is rejected) | no unit reserved (no quota store in this mode) |
| `metered-tenants` | no Phase 15 key, `PLATRIXA_METERING_DATABASE_URL` set | **only** keys created through `/v1/developer/api-keys`, each resolving to exactly one tenant | one unit per admitted billable request |
| `open-anonymous` | neither set | any caller (documented zero-config local development) | no unit reserved |

Consequences worth knowing before you configure a host:

- Setting the Phase 15 key **shadows** the Phase 16 tenant path. A deployment
  that sets both accepts the shared key only; tenant keys created through the
  management plane get `401`. Pick one boundary.
- No configuration makes the API both unauthenticated and metered: metering
  only activates together with a store, and the store is what authenticates.
- `GET /v1/ready` reports the active mode, whether the metering store is
  reachable, and a single `admission.production_ready` boolean. It never
  returns key material, tokens, or connection strings.

#### Health vs readiness

| Probe | Meaning |
|---|---|
| `GET /v1/health` | Liveness. Touches nothing — no DB, no provider, no model load. |
| `GET /v1/ready` | **Production admission readiness**: the provider is loadable AND a credential boundary is configured AND the metering store is reachable. `status: "not_ready"` with a reason otherwise. A service can be alive and still not ready; point load balancers at `/v1/health` for liveness and gate deployments on `/v1/ready`. |

Set these in the hosting platform's dashboard (e.g. Render → Environment, or
Railway → Variables). They must NOT live in the repository. `.env` / `.env.local`
are git-ignored.

## Database

- Managed PostgreSQL (e.g. Render Postgres, Railway Postgres, Neon, Supabase).
- Put the connection string in `DATABASE_URL`.
- On first deploy, create the schema by hitting `POST /api/v1/db/init`
  (or run `python backend/database/init_db.py` on the server).
  The app starts fine without it — the API only needs the DB when serving data.

## Static frontend (legacy reference UI)

`api/main.py` mounts `frontend/` as static files at `/`. This is the
**legacy** single-page app (`index.html` + `app.js` + `styles.css`); it needs
no build step and no Node toolchain, and it stays a working fallback.

It is NOT the current product surface. The current front-end is the Next.js 16
app in `frontend/web/` (home, /validate, /capabilities, /trust, /console,
/developer). Serving the V2 app as the main page is a **Cloudflare Pages
project setting**, not a code change — see below.

## Cloudflare Pages frontend → FastAPI backend (Phase 7I)

The production frontend is served from Cloudflare Pages while FastAPI runs
as a separate long-running service (Render/Railway, see below).

### Production Pages configuration (V2 app — current)

This is the configuration that serves the **V2 app as the main page**:

| Pages setting | Value |
|---|---|
| Build root directory | `frontend/web` |
| Build command | `npm run build:pages` |
| Build output directory | `out` |
| Environment variable | `API_BACKEND_URL=https://<your-fastapi-host>` |

`npm run build:pages` (see `frontend/web/scripts/build-pages.mjs`) statically
exports the Next.js app to `out/` and then copies `frontend/functions/` into
`out/functions/`. Cloudflare only discovers Pages Functions inside the build
output directory, so the copy is what makes the API boundary work from the
V2 artifact. The two Node route handlers that cannot be statically exported
are excluded from this one build only and are always restored afterwards; the
managed dev/preview build (`npm run build`) still includes them.

Verified locally: `npm run lint`, `npx tsc --noEmit` (run AFTER a build, since
`LayoutProps` is a Next-generated global from `.next/types`) and
`npm run build:pages` all succeed; all seven routes prerender static and both
Functions are present in the artifact.

If the Pages project is still pointed at `frontend/` it is serving the
**legacy** UI. That is the single setting that decides which page is public.

### API boundary (unchanged for both surfaces)

`frontend/functions/api/[[path]].js` and `frontend/functions/v1/[[path]].js`
proxy every `/api/*` and `/v1/*` request to the FastAPI host, so the browser
keeps a same-origin API base (`/api/v1/kernel/process`). Set:

    API_BACKEND_URL=https://<your-fastapi-host>

Without it the proxy returns an explicit 502 `backend_not_configured`
instead of Cloudflare's generic 405. The Functions add no credentials — the
operator configures backend authentication on the backend itself.

### Direct base override (legacy app only)

Inject before `app.js` loads (e.g. in `frontend/index.html` or at deploy time):

    <script>window.PLATRIXA_API_BASE="https://<your-fastapi-host>";</script>

Never hardcode a backend URL in committed frontend code; the FastAPI host
is deployment configuration owned by the environment.

## Model inference on Modal (Phase 7R — required for the specialist model path)

Render Free (512 MB) cannot load Qwen2.5-1.5B-Instruct: every load attempt is
killed by the platform after ~75–96 s. The model therefore runs as a Modal
serverless GPU service and the FastAPI Kernel calls it over HTTPS.

- Service code: `training/modal_inference.py` (self-contained; owns the pinned
  base `Qwen/Qwen2.5-1.5B-Instruct` @ `989aa79…`, the pinned LoRA adapter
  `Pranay-20/platrixa-financial-semantic-v0.1` @ `b5c0a37…` (repo renamed
  from the historical `platrixa-fyjc-specialist-v0.1`; same artifact), exact
  Phase 6B/6C
  runtime pins, a persistent HF cache volume, and fail-closed adapter loading).
- Deploy (once, from a machine with Modal auth):

  ```
  pip install modal==1.5.5
  modal token set          # or: modal token new
  modal deploy training/modal_inference.py
  ```

- `modal deploy` prints the public URL (e.g.
  `https://platrixa-model-inference.modal.run`). Verify before wiring:

  ```
  curl https://platrixa-model-inference.modal.run/health
  # expect 200 {"status":"healthy", ..., "model_loaded":true, "adapter_loaded":true}
  curl -X POST https://platrixa-model-inference.modal.run/interpret \
       -H 'Content-Type: application/json' -d '{"text":"Purchased furniture for cash Rs.15,000"}'
  ```

- Then set on the FastAPI service (Render dashboard → Environment):
  `PLATRIXA_MODEL_ENDPOINT_URL=https://platrixa-model-inference.modal.run`
- The first cold start downloads ~3 GB into the `platrixa-hf-cache` Modal
  volume; subsequent containers reuse it. The adapter is hard-pinned and
  fail-closed — the service never serves base-only inference.

## HF ZeroGPU Space as the inference runtime (Phase 7S)

The HF Space `Pranay-20/Platrixa` serves the EXACT Phase 6C artifact on
Hugging Face ZeroGPU (no payment path). ZeroGPU requires the `@spaces.GPU`
Gradio execution model, so the Space exposes the named Gradio API
`/interpret_core` (gradio_client compatible) instead of a raw HTTP route.
The backend consumes it through the Phase 7S transport adapter:

- Code: `backend/model_provider/hf_gradio.py` (`HFGradioModelProvider` —
  subclass of `RemoteHFModelProvider`; only the transport is swapped).
- Selection: `PLATRIXA_MODEL_ENDPOINT_URL` set **and**
  `PLATRIXA_MODEL_TRANSPORT=gradio`.
- The adapter enforces the locked model identity on every response (base ID/
  revision + adapter ID/revision + `adapter_loaded`); any mismatch fails
  closed before the candidate reaches the Kernel.
- Timeouts: default 120 s for the gradio transport (ZeroGPU cold start:
  GPU queue + lazy 3 GB load has been observed up to ~90 s). Override with
  `PLATRIXA_MODEL_TIMEOUT` if needed.
- If the Space is private, set `HF_TOKEN` on the Render service (Space
  secrets dashboard); it is read from the environment only, never logged.
- Modal (`http` transport) remains the default remote path; both share the
  same provider boundary, envelope contract, and error taxonomy.

## Deploy on Render (recommended)

1. Push this repo to GitHub (done).
2. In Render → New → **Web Service**, connect the GitHub repo.
3. Render auto-detects Python; ensure:
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
     (Render also reads the `Procfile` if present)
4. Add the environment variables from the table above.
5. Deploy → you get a permanent HTTPS URL: `https://<service>.onrender.com`.
6. Create the schema: `curl -X POST https://<service>.onrender.com/api/v1/db/init`
   then verify `curl https://<service>.onrender.com/api/v1/health`.

## Deploy on Railway (alternative)

1. New Project → Deploy from GitHub repo.
2. Railway auto-detects the `Procfile`; add env vars in the dashboard.
3. Provision a **PostgreSQL** plugin and point `DATABASE_URL` at it.
4. Public domain: Railway provides `https://<project>.up.railway.app`.

## Verification after deploy

```
curl https://<your-url>/api/v1/health        # {"status":"ok", ...}
curl https://<your-url>/                     # the frontend site
curl https://<your-url>/api/v1/providers/status   # masked key presence
```

## Notes

- `backend/module4/config.py` still uses the pydantic-v1 `BaseSettings` import,
  which fails under pydantic v2 — it is **not** imported by the web app runtime
  path (only by `scheduler.py`, which nothing loads), so it does not block
  deployment. If you later enable the scheduler, migrate it to
  `pydantic_settings.BaseSettings` first.
- Never deploy with the repo's tracked `.env`; remove/rotate any values that
  were ever committed and use platform secrets instead.
