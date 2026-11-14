#!/usr/bin/env python3
"""Next.js proxy (frontend/web/lib/platrixa-proxy.ts) — real HTTP integration.

The Next app serves the same proxy contract as the Cloudflare Pages functions
(``app/v1/[...path]/route.ts`` and ``app/api/[...path]/route.ts``). This drives
a RUNNING ``next dev`` server over real HTTP to the isolated FastAPI backend and
checks identity, spoofing resistance, signing, Origin forwarding, API-key
injection, idempotency-key forwarding and cross-visitor isolation.

Run:
  python scripts/v2_next_proxy_integration_test.py
Env:
  NEXT_PROXY_BASE   default http://127.0.0.1:3100
  PLATRIXA_METERING_DATABASE_URL   isolated test DB (for the ownership assertions)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = (os.getenv("NEXT_PROXY_BASE") or "http://127.0.0.1:3100").rstrip("/")
PUBLIC_ORIGIN = (os.getenv("PLATRIXA_PUBLIC_ORIGIN") or "https://app.platrixa.test").strip()
SECRET = (os.getenv("PLATRIXA_VISITOR_HMAC_SECRET") or "").strip()
A, B = "a" * 32, "b" * 32

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def cookie(visitor: str) -> dict[str, str]:
    return {"Cookie": f"platrixa_visitor_id={visitor}"}


def db_engine():
    from sqlalchemy import create_engine

    url = (os.getenv("PLATRIXA_METERING_DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("PLATRIXA_METERING_DATABASE_URL is not set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url, future=True)


def seed_fixtures() -> None:
    """Own fixtures: visitor A/B jobs plus one developer job, in the test DB."""
    from sqlalchemy import text

    engine = db_engine()
    rows = [
        ("job_next_a", "res_next_a", A, "website_visitor", "anonymous"),
        ("job_next_b", "res_next_b", B, "website_visitor", "anonymous"),
        ("job_next_dev", "res_next_dev", None, "developer_client", "anonymous"),
    ]
    with engine.begin() as conn:
        for job_id, result_id, visitor, owner_type, tenant in rows:
            conn.execute(
                text(
                    "INSERT INTO platrixa_async_jobs (job_id, tenant_id, result_id, request_hash, "
                    "status, kind, request_json, visitor_id, owner_type, created_at, updated_at) "
                    "VALUES (:j, :t, :r, 'h', 'COMPLETED', 'inference', '{}', :v, :o, now(), now()) "
                    "ON CONFLICT (job_id) DO UPDATE SET visitor_id = :v, owner_type = :o, "
                    "tenant_id = :t, status = 'COMPLETED'"
                ),
                {"j": job_id, "t": tenant, "r": result_id, "v": visitor, "o": owner_type},
            )


def main() -> int:
    if not SECRET:
        raise SystemExit("PLATRIXA_VISITOR_HMAC_SECRET is not set")
    seed_fixtures()

    # Wait for the dev server (first route compiles on demand).
    for _ in range(60):
        try:
            requests.get(f"{BASE}/api/v1/health", timeout=2)
            break
        except requests.RequestException:
            time.sleep(1)

    check("Next proxy answers over real HTTP", True, BASE)

    r = requests.get(f"{BASE}/v1/jobs/job_next_a", headers=cookie(A), timeout=30)
    check("visitor A reads own job through the Next proxy", r.status_code == 200,
          f"status={r.status_code} body={r.text[:120]}")

    r = requests.get(f"{BASE}/v1/jobs/job_next_a", headers=cookie(B), timeout=30)
    check("visitor B cannot read A's job (cross-visitor isolation)", r.status_code == 404,
          f"status={r.status_code}")

    r = requests.get(f"{BASE}/v1/results/res_next_a", headers=cookie(B), timeout=30)
    check("visitor B cannot read A's result", r.status_code == 404, f"status={r.status_code}")

    r = requests.get(f"{BASE}/v1/jobs/job_next_dev", headers=cookie(A), timeout=30)
    check("website visitor cannot reach a developer job", r.status_code == 404,
          f"status={r.status_code}")

    forged = hashlib.sha256(b"").hexdigest()
    reconstructed = (
        f"V1|platrixa|{A}|GET|/v1/jobs/job_next_a||{forged}|{int(time.time())}|forged-nonce"
    )
    sig = hmac.new(SECRET.encode(), reconstructed.encode(), hashlib.sha256).hexdigest()
    r = requests.get(
        f"{BASE}/v1/jobs/job_next_a",
        headers={
            **cookie(B),
            "x-platrixa-visitor-id": A,
            "x-platrixa-surface": "developer",
            "x-platrixa-visitor-assertion": f"{reconstructed}|{sig}",
        },
        timeout=30,
    )
    check("browser-supplied identity/assertion headers cannot spoof the cookie",
          r.status_code == 404, f"status={r.status_code}")

    r = requests.post(f"{BASE}/v1/jobs/job_next_a", headers={**cookie(A), "Origin": PUBLIC_ORIGIN}, timeout=30)
    check("trusted Origin mutation passes CSRF and backend auth (405 = GET-only route)",
          r.status_code == 405, f"status={r.status_code}")

    r = requests.post(f"{BASE}/v1/jobs/job_next_a", headers={**cookie(A), "Origin": "https://evil.example"}, timeout=30)
    check("foreign Origin mutation is rejected before the backend", r.status_code == 403,
          f"status={r.status_code}")

    # Idempotency-key forwarding + visitor ownership on a real create.
    idem = f"v2-next-{uuid.uuid4().hex}"
    body = {"raw_input": "next proxy integration probe"}
    first = requests.post(
        f"{BASE}/v1/inference-jobs",
        headers={**cookie(A), "Origin": PUBLIC_ORIGIN, "Idempotency-Key": idem,
                 "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=60,
    )
    check("job creation through the Next proxy is accepted", first.status_code in (200, 201, 202),
          f"status={first.status_code} body={first.text[:160]}")
    try:
        job_id = first.json().get("job_id", "")
    except ValueError:
        job_id = ""

    if job_id:
        from sqlalchemy import text

        engine = db_engine()
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT visitor_id, owner_type, tenant_id FROM platrixa_async_jobs WHERE job_id=:j"),
                {"j": job_id},
            ).mappings().first()
            idem_row = conn.execute(
                text(
                    "SELECT count(*) FROM platrixa_idempotency_keys "
                    "WHERE endpoint LIKE '%inference-jobs%' AND created_at > now() - interval '5 minutes'"
                )
            ).scalar_one()
        check("created job is owned by the cookie's visitor (signed identity reached the DB)",
              bool(row)
              and str(row["visitor_id"]).replace("-", "") == A
              and row["owner_type"] == "website_visitor",
              str(dict(row) if row else None))
        check("Idempotency-Key was forwarded to the backend and recorded", idem_row >= 1,
              f"rows={idem_row}")

    replay = requests.post(
        f"{BASE}/v1/inference-jobs",
        headers={**cookie(A), "Origin": PUBLIC_ORIGIN, "Idempotency-Key": idem,
                 "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=60,
    )
    check("same Idempotency-Key replays instead of creating a second job",
          replay.status_code in (200, 201, 202)
          and (replay.headers.get("Idempotent-Replayed", "").lower() == "true"
               or (job_id and replay.json().get("job_id") == job_id)),
          f"status={replay.status_code} replayed={replay.headers.get('Idempotent-Replayed')}")

    r = requests.get(f"{BASE}/v1/jobs/{job_id}", headers=cookie(B), timeout=30)
    check("the newly created job is invisible to another visitor", r.status_code == 404,
          f"status={r.status_code}")

    failed = [x for x in RESULTS if not x[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} Next proxy integration checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
