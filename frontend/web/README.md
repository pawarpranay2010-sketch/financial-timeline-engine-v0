# Platrixa — Next.js developer frontend

`frontend/web` is a Next.js 16 + TypeScript + Tailwind CSS 4 app that talks
to the **real** Platrixa FastAPI backend through a same-origin server-side
proxy during local development. The legacy static app (`frontend/`) and the
backend are untouched.

```
Browser (same-origin /api/*, /v1/*)
   ↓
Next.js route handlers (server-side proxy, lib/platrixa-proxy.ts)
   ↓
FastAPI  (PLATRIXA_API_BASE_URL, e.g. http://127.0.0.1:8000)
   ↓
Platrixa deterministic authorities
```

## Running locally with the real backend

1. Start FastAPI (repository root):

   ```bash
   uvicorn api.main:app --host 127.0.0.1 --port 8000
   ```

   (FastAPI reads `PORT`, default 5000 — any port works; point the proxy at it.)

2. Point the proxy at it — create `frontend/web/.env.local` (git-ignored):

   ```
   PLATRIXA_API_BASE_URL=http://127.0.0.1:8000
   # Optional: only when the backend sets PLATRIXA_DEV_API_KEY or the
   # Phase 16 metering gate. Never use NEXT_PUBLIC_* for this.
   # PLATRIXA_API_KEY=<server-side key>
   ```

3. Start the frontend:

   ```bash
   npm run dev        # or: npm run build && npm run start
   ```

The browser only ever calls same-origin `/api/*` and `/v1/*`; the proxy
forwards them verbatim (method, JSON body, `Content-Type`, `X-Request-Id`)
and returns the upstream status/body unchanged. Only the `/api/` and `/v1/`
namespaces are forwarded — this is not a generic proxy. With no backend
configured the proxy fails closed with a structured
`503 BACKEND_NOT_CONFIGURED` envelope, and the UI shows a connection state —
it never substitutes mock data for a live failure.

## Environment variables

| Variable | Side | Purpose |
|---|---|---|
| `PLATRIXA_API_BASE_URL` | server | FastAPI base URL the proxy targets (fixed destination) |
| `PLATRIXA_API_KEY` | server | Optional; attached as `X-Platrixa-API-Key` when the backend gates `/v1/*` (Phase 15/16). Never logged, never sent to the browser, never `NEXT_PUBLIC_*` |

No secrets appear in the client bundle: both variables are read exclusively
inside Node route handlers.

## Status semantics

The UI renders the backend's statuses verbatim (`VERIFIED`,
`REVIEW_REQUIRED`, `BLOCKED`, `UNSUPPORTED_TRANSACTION`, … plus the Phase 5A
six-state transport mapping where present). Transport status is not
financial authority — the frontend never upgrades, downgrades, or invents
results.
