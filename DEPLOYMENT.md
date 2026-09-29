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
