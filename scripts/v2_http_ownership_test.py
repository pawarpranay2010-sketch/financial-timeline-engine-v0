#!/usr/bin/env python3
"""Visitor Ownership V2 — HTTP ownership evidence against the real FastAPI app.

Boots api.main.create_app() against the isolated test database, seeds website /
developer / legacy jobs, and drives real HTTP requests with signed visitor
assertions.

Run:
  PLATRIXA_METERING_DATABASE_URL=postgresql://.../test... \
  PLATRIXA_VISITOR_HMAC_SECRET=... \
      python scripts/v2_http_ownership_test.py
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if not ok else ""))


def _url() -> str:
    url = (os.getenv("PLATRIXA_METERING_DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("PLATRIXA_METERING_DATABASE_URL is not set")
    if "test" not in url.lower():
        raise SystemExit("refusing to run: database URL does not look like a test database")
    return url


def _engine(url: str):
    from sqlalchemy import create_engine

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url, future=True)


def seed(engine) -> None:
    from sqlalchemy import text

    now = int(time.time())
    rows = [
        # job_id, result_id, visitor_id, owner_type
        ("job_a", "res_a", "a" * 32, "website_visitor"),
        ("job_b", "res_b", "b" * 32, "website_visitor"),
        ("job_dev", "res_dev", None, "developer_client"),
        ("job_legacy", "res_legacy", None, "legacy"),
    ]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM platrixa_async_jobs WHERE job_id LIKE 'job_%'"))
        for job_id, result_id, visitor_id, owner_type in rows:
            conn.execute(
                text(
                    "INSERT INTO platrixa_async_jobs "
                    "(job_id, tenant_id, result_id, request_hash, status, kind, request_json, "
                    " result_json, http_status, retryable, visitor_id, owner_type, "
                    " created_at, updated_at) VALUES "
                    "(:j, 'anonymous', :r, :h, 'COMPLETED', 'document', '{}', '{}', 200, false, "
                    " :v, :o, now(), now())"
                ),
                {"j": job_id, "r": result_id, "h": "h-" + job_id, "v": visitor_id, "o": owner_type},
            )
    print(f"seeded jobs at {now}")


def visitor_headers(visitor: str, method: str, path: str, query: str = "", body: bytes = b"") -> dict:
    secret = (os.getenv("PLATRIXA_VISITOR_HMAC_SECRET") or "").strip()
    if not secret:
        raise SystemExit("PLATRIXA_VISITOR_HMAC_SECRET is not set")
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"V1|platrixa|{visitor}|{method.upper()}|{path}|{query}|{body_hash}|{ts}|{nonce}"
    sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "x-platrixa-surface": "website",
        "x-platrixa-visitor-id": visitor,
        "x-platrixa-visitor-assertion": f"{canonical}|{sig}",
        "x-platrixa-request-timestamp": ts,
        "x-platrixa-visitor-nonce": nonce,
    }


def main() -> int:
    url = _url()
    engine = _engine(url)
    seed(engine)

    from fastapi.testclient import TestClient

    from api.main import create_app

    app = create_app()
    client = TestClient(app)
    A, B = "a" * 32, "b" * 32

    # Test-only API key in the isolated store (metered mode requires one).
    from backend.auth import api_keys as _keys

    raw_key = _keys.create_api_key("anonymous", f"v2-http-{uuid.uuid4().hex[:8]}", "test")["key"]

    def status(path: str, headers: dict | None = None):
        merged = {"x-platrixa-api-key": raw_key}
        merged.update(headers or {})
        return client.get(path, headers=merged)

    # baseline: the app answers at all
    none = status("/v1/jobs/job_a")
    check("baseline request reaches the app", none.status_code in (200, 401, 403, 404, 503),
          f"{none.status_code} {none.text[:120]}")

    # A can read A's job
    ra = status("/v1/jobs/job_a", visitor_headers(A, "GET", "/v1/jobs/job_a"))
    check("Visitor A can access A's job", ra.status_code == 200, f"{ra.status_code} {ra.text[:160]}")

    # B cannot read A's job
    rb = status("/v1/jobs/job_a", visitor_headers(B, "GET", "/v1/jobs/job_a"))
    check("Visitor B cannot access A's job", rb.status_code == 404, f"{rb.status_code} {rb.text[:160]}")

    # B cannot read A's result
    rbr = status("/v1/results/res_a", visitor_headers(B, "GET", "/v1/results/res_a"))
    check("Visitor B cannot access A's result", rbr.status_code == 404,
          f"{rbr.status_code} {rbr.text[:160]}")
    rar = status("/v1/results/res_a", visitor_headers(A, "GET", "/v1/results/res_a"))
    check("Visitor A can access A's result", rar.status_code == 200, f"{rar.status_code} {rar.text[:160]}")

    # website visitors cannot reach developer / legacy jobs
    rd = status("/v1/jobs/job_dev", visitor_headers(A, "GET", "/v1/jobs/job_dev"))
    check("website visitor cannot access developer job", rd.status_code == 404,
          f"{rd.status_code} {rd.text[:160]}")
    rl = status("/v1/jobs/job_legacy", visitor_headers(A, "GET", "/v1/jobs/job_legacy"))
    check("website visitor cannot access legacy job", rl.status_code == 404,
          f"{rl.status_code} {rl.text[:160]}")

    # inaccessible vs nonexistent must be externally indistinguishable
    rn = status("/v1/jobs/job_does_not_exist", visitor_headers(A, "GET", "/v1/jobs/job_does_not_exist"))
    same_shape = (
        rb.status_code == rn.status_code == 404
        and rb.json().get("error", {}).get("code") == rn.json().get("error", {}).get("code")
        and set(rb.json().keys()) == set(rn.json().keys())
    )
    check("cross-visitor 404 indistinguishable from nonexistent", same_shape,
          f"B={rb.status_code}:{rb.text[:120]} none={rn.status_code}:{rn.text[:120]}")

    # developer path still works without any visitor identity
    rdev = status("/v1/jobs/job_dev")
    check("developer authorization still works without visitor scope", rdev.status_code == 200,
          f"{rdev.status_code} {rdev.text[:160]}")

    # missing assertion fails closed
    rmiss = status("/v1/jobs/job_a", {"x-platrixa-surface": "website"})
    check("website request without assertion fails closed", rmiss.status_code == 403,
          f"{rmiss.status_code} {rmiss.text[:160]}")

    # invalid assertion fails closed
    bad = visitor_headers(A, "GET", "/v1/jobs/job_a")
    bad["x-platrixa-visitor-assertion"] = bad["x-platrixa-visitor-assertion"].replace(A, "c" * 32)
    rbad = status("/v1/jobs/job_a", bad)
    check("website request with forged assertion fails closed", rbad.status_code == 403,
          f"{rbad.status_code} {rbad.text[:160]}")

    # assertion bound to a different path cannot be replayed onto this one
    wrong_path = visitor_headers(A, "GET", "/v1/jobs/job_b")
    rwp = status("/v1/jobs/job_a", wrong_path)
    check("assertion bound to another request target is rejected", rwp.status_code == 403,
          f"{rwp.status_code} {rwp.text[:160]}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} HTTP ownership checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
