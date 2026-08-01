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

Set these in the hosting platform's dashboard (e.g. Render → Environment, or
Railway → Variables). They must NOT live in the repository. `.env` / `.env.local`
are git-ignored.

## Database

- Managed PostgreSQL (e.g. Render Postgres, Railway Postgres, Neon, Supabase).
- Put the connection string in `DATABASE_URL`.
- On first deploy, create the schema by hitting `POST /api/v1/db/init`
  (or run `python backend/database/init_db.py` on the server).
  The app starts fine without it — the API only needs the DB when serving data.

## Static frontend

Already configured: `api/main.py` mounts `frontend/` as static files at `/`.
No build step, no Node toolchain required.

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
