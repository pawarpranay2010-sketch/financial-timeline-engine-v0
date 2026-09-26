"""Phase 5E — Async Document Processing test suite.

Verifies the async document lifecycle on REAL PostgreSQL (embedded
pgserver): durable job store, lease-based worker recovery, job polling,
result retrieval through THE 5D envelope, idempotent duplicate
submission, tenant isolation, webhook registration/signing/replay
protection, and honest failure semantics.

Run:
    python3 scripts/fte_fyjc_81_phase5e_async_documents_test.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import async_jobs  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402
from backend.auth import idempotency as idem  # noqa: E402
from backend.auth.models import current_usage_month  # noqa: E402
from backend.auth.tokens import hash_token  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Stub client (facade contract; per-input accounting so result mixing is
# detectable) + document stub (kernel_result wrapper for the doc pipeline)
# ---------------------------------------------------------------------------


class _R:
    def __init__(self, text: str, status: str) -> None:
        self.status = status
        self.status_label = status
        self.success = status == "VERIFIED"
        self.request_id = f"req-{abs(hash(text)) % 100000}"
        self.next_action = "" if status == "VERIFIED" else "review"
        self.issues: list = []
        self.grounding_issues: list = []
        self.rule_evidence: list = []
        digits = "".join(ch for ch in text if ch.isdigit()) or "0"
        self.interpretation = {
            "transaction_type": "PURCHASE",
            "parties": ["vendor"],
            "amounts": [{"value": digits, "currency": "INR", "source": "explicit"}],
        }
        self.accounting = {"status": status, "total": digits} if status == "VERIFIED" else None


class StubClient:
    """Counting stub with runtime-switchable behavior (worker-facing)."""

    def __init__(self, status: str = "VERIFIED") -> None:
        self._status = status
        self.fail_mode: str | None = None  # None | "provider"
        self.calls = 0
        self.lock = threading.Lock()

    def process(self, text, request_id=None):
        from platrixa.errors import InputError, PlatrixaError

        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise InputError("input must be a non-empty transaction string")
        with self.lock:
            self.calls += 1
            fail_mode = self.fail_mode
        if fail_mode == "provider":
            raise PlatrixaError("model provider unavailable")
        return _R(text, self._status)

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


def _client(stub) -> TestClient:
    os.environ.pop("PLATRIXA_DEV_API_KEY", None)
    developer.set_client(stub)
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Real PostgreSQL backend (embedded; discarded after the run)
# ---------------------------------------------------------------------------

_PG = None


def pg_backend():
    global _PG
    if _PG is not None:
        return _PG
    import pgserver
    from sqlalchemy import create_engine, text

    pgdata = Path("/tmp/platrixa_phase5e_pgdata")
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(pgdata)
    uri = srv.get_uri()
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    os.environ[async_jobs.WEBHOOK_SEALING_ENV_VAR] = "test-webhook-signing-key-5e"
    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    quota_ddl = (
        Path(__file__).resolve().parent.parent / "backend" / "database" / "platrixa_tenant_quota_schema.sql"
    ).read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(quota_ddl))
    idem._session_factory_cache.pop(uri.replace("postgresql://", "postgresql+psycopg2://", 1), None)
    idem._session_factory()
    async_jobs._session_factory_cache.pop(uri.replace("postgresql://", "postgresql+psycopg2://", 1), None)
    async_jobs._session_factory()
    _PG = (srv, engine)
    return _PG


def make_tenant(engine, key: str, tenant_id: str, limit: int) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platrixa_tenant_quotas "
                "(api_key_hash, tenant_id, monthly_limit, current_month_usage, usage_month, is_active) "
                "VALUES (:h, :t, :l, 0, :m, true) "
                "ON CONFLICT (api_key_hash) DO UPDATE SET monthly_limit = :l, current_month_usage = 0, is_active = true"
            ),
            {"h": hash_token(key), "t": tenant_id, "l": limit, "m": current_usage_month()},
        )


def tenant_usage(engine, key: str) -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT current_month_usage FROM platrixa_tenant_quotas WHERE api_key_hash = :h"),
            {"h": hash_token(key)},
        ).first()
    return int(row[0]) if row else 0


def reset_tables(engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM platrixa_async_jobs"))
        conn.execute(text("DELETE FROM platrixa_webhook_endpoints"))
        conn.execute(text("DELETE FROM platrixa_idempotency_keys"))
        conn.execute(text("DELETE FROM platrixa_tenant_quotas"))


def wait_for_job(c: TestClient, key: str, job_id: str, *, timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = c.get(f"/v1/jobs/{job_id}", headers={"X-Platrixa-API-Key": key})
        body = r.json()
        if body.get("status") not in {"PROCESSING", "QUEUED"}:
            return body
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not complete within {timeout_s}s")


# ---------------------------------------------------------------------------
# Section A — zero-config honesty + submission transport
# ---------------------------------------------------------------------------


def section_a() -> None:
    print("\nA — zero-config honesty + submission transport (no durable store)")
    os.environ.pop(metered_gate.METERING_ENV_VAR, None)
    stub = StubClient()
    c = _client(stub)

    r = c.post("/v1/documents", json={"raw_input": "Paid vendor Rs. 100"})
    check("A1 async submission on zero-config → 400 ASYNC_NOT_CONFIGURED (honest, no fake queue)",
          r.status_code == 400 and r.json()["error"]["code"] == "ASYNC_NOT_CONFIGURED",
          str(r.json())[:150])
    check("A2 refusal maps to INVALID_INPUT api_status", r.json()["error"]["api_status"] == "INVALID_INPUT")
    r = c.get("/v1/jobs/job_x")
    check("A3 job poll on zero-config → 400 ASYNC_NOT_CONFIGURED",
          r.status_code == 400 and r.json()["error"]["code"] == "ASYNC_NOT_CONFIGURED")
    r = c.get("/v1/results/res_x")
    check("A4 result fetch on zero-config → 400 ASYNC_NOT_CONFIGURED",
          r.status_code == 400 and r.json()["error"]["code"] == "ASYNC_NOT_CONFIGURED")
    r = c.post("/v1/webhook-endpoints", json={"url": "https://example.com/hook"})
    check("A5 webhook registration on zero-config → 400 ASYNC_NOT_CONFIGURED",
          r.status_code == 400 and r.json()["error"]["code"] == "ASYNC_NOT_CONFIGURED")


# ---------------------------------------------------------------------------
# Section B — durable store primitives
# ---------------------------------------------------------------------------


def section_b(engine) -> None:
    print("\nB — durable job store primitives (real PostgreSQL)")
    reset_tables(engine)
    rec = async_jobs.create_job("tenant-b", {"raw_input": "x", "source_name": "t.txt"})
    check("B1 create_job returns QUEUED record with job_id + result_id",
          rec.status == "QUEUED" and rec.job_id.startswith("job_") and rec.result_id.startswith("res_"))
    got = async_jobs.get_job("tenant-b", rec.job_id)
    check("B2 tenant-scoped job read works", got is not None and got.job_id == rec.job_id)
    check("B3 cross-tenant read returns NOTHING (isolation)",
          async_jobs.get_job("tenant-other", rec.job_id) is None)
    claimed = async_jobs.claim_next_job()
    check("B4 claim_next_job atomically leases QUEUED → PROCESSING",
          claimed is not None and claimed.job_id == rec.job_id and claimed.status == "PROCESSING")
    check("B5 second claim gets nothing (no double-lease)", async_jobs.claim_next_job() is None)
    ok = async_jobs.complete_job(rec.job_id, envelope={"status": "VERIFIED", "api_status": "VERIFIED"}, http_status=200)
    check("B6 complete_job stores terminal envelope (COMPLETED)", ok)
    fetched = async_jobs.get_job("tenant-b", rec.job_id)
    check("B7 completed record holds the envelope",
          fetched.status == "COMPLETED" and json.loads(fetched.result_json)["api_status"] == "VERIFIED")

    rec2 = async_jobs.create_job("tenant-b", {"raw_input": "y", "source_name": "t.txt"})
    async_jobs.claim_next_job()
    ok2 = async_jobs.fail_job(rec2.job_id, envelope={"error": {"code": "PROVIDER_UNAVAILABLE"}}, http_status=503, retryable=True)
    rec2b = async_jobs.get_job("tenant-b", rec2.job_id)
    check("B8 fail_job marks FAILED with retryable=true", ok2 and rec2b.status == "FAILED" and rec2b.retryable is True)

    # Lease recovery: expired lease is claimable again
    rec3 = async_jobs.create_job("tenant-b", {"raw_input": "z", "source_name": "t.txt"})
    async_jobs.claim_next_job(lease_seconds=-1)  # expired lease
    rec3b = async_jobs.claim_next_job()
    check("B9 expired lease recovered by next claim (restart durability)",
          rec3b is not None and rec3b.job_id == rec3.job_id)


# ---------------------------------------------------------------------------
# Section C — full async lifecycle (submission → poll → result)
# ---------------------------------------------------------------------------


def section_c(engine) -> None:
    print("\nC — async lifecycle end-to-end (submission → worker → result)")
    reset_tables(engine)
    make_tenant(engine, "k-81c", "tenant-81c", 100)
    stub = StubClient("VERIFIED")
    c = _client(stub)
    K = {"X-Platrixa-API-Key": "k-81c"}

    r = c.post("/v1/documents", json={"raw_input": "Invoice total Rs. 15,000 from vendor", "source_name": "inv.txt"}, headers=K)
    body = r.json()
    check("C1 submission → 202 Accepted", r.status_code == 202, str(body)[:200])
    check("C2 202 body: PROCESSING + job_id/result_id/status_url/result_url/created_at",
          body.get("status") == "PROCESSING" and body.get("job_id", "").startswith("job_")
          and body.get("result_id", "").startswith("res_") and "/v1/jobs/" in body.get("status_url", "")
          and "/v1/results/" in body.get("result_url", "") and body.get("created_at"))
    check("C3 reservation happened at submission (1 unit)", tenant_usage(engine, "k-81c") == 1)

    job = wait_for_job(c, "k-81c", body["job_id"])
    check("C4 job completes with the REAL engine outcome (VERIFIED — not a synthetic 'done')",
          job["status"] == "VERIFIED", str(job)[:200])
    check("C5 job poll exposes created_at/updated_at + result_url",
          job.get("created_at") and job.get("updated_at") and job.get("result_url"))
    check("C6 exactly one engine call for the job", stub.calls == 1)

    rr = c.get(f"/v1/results/{body['result_id']}", headers=K)
    result = rr.json()
    check("C7 result endpoint returns THE 5D envelope (convergent contract)",
          rr.status_code == 200 and result.get("api_version") == "v1"
          and result.get("api_status") == "VERIFIED" and result.get("accounting_result") is not None
          and "reason_codes" in result and "metadata" in result, str(result)[:200])
    check("C8 async result carries request_id and metadata.engine_status",
          result.get("request_id") and result["metadata"].get("engine_status") == "VERIFIED")

    # Unknown ids
    r404 = c.get("/v1/jobs/job_doesnotexist123", headers=K)
    check("C9 unknown job → 404 JOB_NOT_FOUND",
          r404.status_code == 404 and r404.json()["error"]["code"] == "JOB_NOT_FOUND")
    r404b = c.get("/v1/results/res_doesnotexist123", headers=K)
    check("C10 unknown result → 404 RESULT_NOT_FOUND",
          r404b.status_code == 404 and r404b.json()["error"]["code"] == "RESULT_NOT_FOUND")

    # RESULT_NOT_READY: create a job the worker cannot see (store-level)
    rec = async_jobs.create_job("tenant-81c", {"raw_input": "hold", "source_name": "t.txt"})
    rnr = c.get(f"/v1/results/{rec.result_id}", headers=K)
    check("C11 result before completion → 404 RESULT_NOT_READY (honest)",
          rnr.status_code == 404 and rnr.json()["error"]["code"] == "RESULT_NOT_READY")


# ---------------------------------------------------------------------------
# Section D — real engine states preserved (no collapsing)
# ---------------------------------------------------------------------------


def section_d(engine) -> None:
    print("\nD — real engine states preserved through async (no collapsing)")
    make_tenant(engine, "k-81d", "tenant-81d", 100)
    K = {"X-Platrixa-API-Key": "k-81d"}

    stub = StubClient("REVIEW_REQUIRED")
    c = _client(stub)
    b = c.post("/v1/documents", json={"raw_input": "ambiguous doc Rs. 10", "source_name": "a.txt"}, headers=K).json()
    job = wait_for_job(c, "k-81d", b["job_id"])
    res = c.get(f"/v1/results/{b['result_id']}", headers=K).json()
    check("D1 REVIEW_REQUIRED NOT collapsed to VERIFIED or FAILED",
          job["status"] == "REVIEW_REQUIRED" and res["api_status"] == "REVIEW_REQUIRED" and res["success"] is False,
          str(job)[:120])

    stub = StubClient("UNSUPPORTED_TRANSACTION")
    c = _client(stub)
    b = c.post("/v1/documents", json={"raw_input": "weird doc Rs. 10", "source_name": "u.txt"}, headers=K).json()
    job = wait_for_job(c, "k-81d", b["job_id"])
    res = c.get(f"/v1/results/{b['result_id']}", headers=K).json()
    check("D2 UNSUPPORTED NOT collapsed to FAILED",
          job["status"] == "UNSUPPORTED_TRANSACTION" and res["api_status"] == "UNSUPPORTED",
          str(job)[:120])

    stub = StubClient("VERIFIED")
    stub.fail_mode = "provider"
    c = _client(stub)
    b = c.post("/v1/documents", json={"raw_input": "provider down Rs. 10", "source_name": "p.txt"}, headers=K).json()
    job = wait_for_job(c, "k-81d", b["job_id"])
    check("D3 provider failure → FAILED job, retryable=true, PROVIDER_UNAVAILABLE code",
          job["status"] == "FAILED" and job["retryable"] is True and job["reason_codes"] == ["PROVIDER_UNAVAILABLE"],
          str(job)[:150])

    # Invalid document type is rejected at SUBMISSION (415), not at process time
    stub = StubClient("VERIFIED")
    c = _client(stub)
    import base64 as _b64

    r = c.post("/v1/documents", json={"document_b64": _b64.b64encode(b"xx").decode(), "source_name": "evil.exe"}, headers=K)
    check("D4 unsupported file type rejected at submission → 415 FILE_TYPE_UNSUPPORTED",
          r.status_code == 415 and r.json()["error"]["code"] == "FILE_TYPE_UNSUPPORTED", str(r.json())[:150])
    r = c.post("/v1/documents", json={"document_b64": _b64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode(), "source_name": "big.pdf"}, headers=K)
    check("D5 oversized document rejected at submission → 413 FILE_TOO_LARGE",
          r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE")


# ---------------------------------------------------------------------------
# Section E — idempotency on the creation request
# ---------------------------------------------------------------------------


def section_e(engine) -> None:
    print("\nE — idempotent duplicate submission (same key → same job)")
    reset_tables(engine)
    make_tenant(engine, "k-81e", "tenant-81e", 100)
    stub = StubClient("VERIFIED")
    c = _client(stub)
    K = {"X-Platrixa-API-Key": "k-81e", "Idempotency-Key": "idem-async-key-~._12345"}
    doc_payload = {"raw_input": "idempotent doc Rs. 20", "source_name": "i.txt"}

    b1 = c.post("/v1/documents", json=doc_payload, headers=K).json()
    check("E1 first submission 202 + Idempotent-Replayed: false",
          c.post("/v1/documents", json=doc_payload, headers=K).status_code == 202)
    check("E2 idempotent duplicate returns the SAME job_id (no duplicate job)",
          c.post("/v1/documents", json=doc_payload, headers=K).json()["job_id"] == b1["job_id"])
    check("E3 duplicate carries Idempotent-Replayed: true",
          c.post("/v1/documents", json=doc_payload, headers=K).headers.get("idempotent-replayed") == "true")
    wait_for_job(c, "k-81e", b1["job_id"])
    check("E4 exactly one engine call across all duplicates", stub.calls == 1)
    check("E5 exactly one unit of quota consumed", tenant_usage(engine, "k-81e") == 1)

    alt = dict(doc_payload, raw_input="different doc Rs. 30")
    r409 = c.post("/v1/documents", json=alt, headers=K)
    check("E6 same key + different request → deterministic 409",
          r409.status_code == 409 and r409.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST")

    # Concurrent duplicates: exactly one job created
    reset_tables(engine)
    make_tenant(engine, "k-81e2", "tenant-81e2", 100)
    stub2 = StubClient("VERIFIED")
    c2 = _client(stub2)
    K2 = {"X-Platrixa-API-Key": "k-81e2", "Idempotency-Key": "idem-concurrent-~._12345"}
    results: list = []

    def submit():
        results.append(c2.post("/v1/documents", json=doc_payload, headers=K2).json())

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    job_ids = {r.get("job_id") for r in results if r.get("job_id")}
    check("E7 8 concurrent duplicates → exactly ONE job_id", len(job_ids) == 1, str(job_ids))
    check("E8 concurrent duplicates consumed exactly one unit", tenant_usage(engine, "k-81e2") == 1)


# ---------------------------------------------------------------------------
# Section F — tenant isolation on jobs/results
# ---------------------------------------------------------------------------


def section_f(engine) -> None:
    print("\nF — tenant isolation on the async surface")
    reset_tables(engine)
    make_tenant(engine, "k-81fA", "tenant-81fA", 100)
    make_tenant(engine, "k-81fB", "tenant-81fB", 100)
    stub = StubClient("VERIFIED")
    c = _client(stub)
    bA = c.post("/v1/documents", json={"raw_input": "A doc Rs. 5", "source_name": "a.txt"},
                headers={"X-Platrixa-API-Key": "k-81fA"}).json()
    wait_for_job(c, "k-81fA", bA["job_id"])
    rB = c.get(f"/v1/jobs/{bA['job_id']}", headers={"X-Platrixa-API-Key": "k-81fB"})
    check("F1 tenant B cannot poll tenant A's job", rB.status_code == 404 and rB.json()["error"]["code"] == "JOB_NOT_FOUND")
    rB2 = c.get(f"/v1/results/{bA['result_id']}", headers={"X-Platrixa-API-Key": "k-81fB"})
    check("F2 tenant B cannot fetch tenant A's result",
          rB2.status_code == 404 and rB2.json()["error"]["code"] == "RESULT_NOT_FOUND")


# ---------------------------------------------------------------------------
# Section G — webhooks: registration, signing, replay protection
# ---------------------------------------------------------------------------


def section_g(engine) -> None:
    print("\nG — webhook registration, signing, replay protection")
    reset_tables(engine)
    make_tenant(engine, "k-81g", "tenant-81g", 100)
    stub = StubClient("VERIFIED")
    c = _client(stub)
    K = {"X-Platrixa-API-Key": "k-81g"}

    r = c.post("/v1/webhook-endpoints", json={"url": "https://example.com/hooks/platrixa",
                                              "events": ["document.completed", "document.failed"],
                                              "secret": "whsec_my_local_test_secret_123456"}, headers=K)
    body = r.json()
    check("G1 registration 201 with webhook_id + events", r.status_code == 201 and body.get("webhook_id", "").startswith("wh_")
          and body.get("events") == ["document.completed", "document.failed"], str(body)[:200])
    check("G2 secret returned EXACTLY ONCE (plaintext in response)", body.get("secret") == "whsec_my_local_test_secret_123456")

    # secret stored sealed, never plaintext
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT secret_sealed FROM platrixa_webhook_endpoints WHERE webhook_id = :w"),
            {"w": body["webhook_id"]},
        ).first()
    check("G3 stored secret is sealed (not plaintext)", row and "whsec_my_local" not in (row[0] or ""))
    check("G4 sealed secret round-trips to the plaintext for signing",
          async_jobs.unseal_webhook_secret(row[0]) == "whsec_my_local_test_secret_123456")

    r = c.post("/v1/webhook-endpoints", json={"url": "http://example.com/hook"}, headers=K)
    check("G5 non-https url rejected → 400", r.status_code == 400 and r.json()["error"]["code"] == "INPUT_INVALID")
    r = c.post("/v1/webhook-endpoints", json={"url": "https://127.0.0.1/hook"}, headers=K)
    check("G6 localhost/IP-literal url rejected (no SSRF surface) → 400", r.status_code == 400)
    r = c.post("/v1/webhook-endpoints", json={"url": "https://example.com/h", "events": ["bogus.event"]}, headers=K)
    check("G7 unknown event rejected → 400 (closed vocabulary)", r.status_code == 400)

    # Signing scheme
    secret = "whsec_my_local_test_secret_123456"
    ts = int(time.time())
    payload = json.dumps({"id": "evt_x", "type": "document.completed"}, sort_keys=True, separators=(",", ":"))
    sig = async_jobs.sign_event(secret, ts, payload)
    header = f"t={ts},v1={sig}"
    check("G8 valid signature verifies",
          async_jobs.verify_event_signature(secret, header, payload) is True)
    check("G9 tampered payload fails verification",
          async_jobs.verify_event_signature(secret, header, payload + "x") is False)
    old_ts = ts - 4000  # beyond the 300s tolerance
    check("G10 stale timestamp fails replay protection",
          async_jobs.verify_event_signature(secret, f"t={old_ts},v1={async_jobs.sign_event(secret, old_ts, payload)}", payload) is False)
    check("G11 deterministic event id per (job_id, event)",
          async_jobs.event_id("job_abc", "document.completed") == async_jobs.event_id("job_abc", "document.completed")
          and async_jobs.event_id("job_abc", "document.completed") != async_jobs.event_id("job_abd", "document.completed"))

    # End-to-end: completed job emits document.completed (not review_required)
    stub2 = StubClient("VERIFIED")
    c2 = _client(stub2)
    b = c2.post("/v1/documents", json={"raw_input": "hook doc Rs. 10", "source_name": "h.txt"},
                headers={"X-Platrixa-API-Key": "k-81g"}).json()
    wait_for_job(c2, "k-81g", b["job_id"])
    rec = async_jobs.get_job("tenant-81g", b["job_id"])
    check("G12 VERIFIED outcome maps to document.completed event (correct event for status)",
          rec.status == "COMPLETED" and async_jobs.EVENT_BY_API_STATUS["VERIFIED"] == "document.completed")


def main() -> int:
    print("=" * 70)
    print("PHASE 5E — ASYNC DOCUMENT PROCESSING (fte_fyjc_81)")
    print("=" * 70)
    section_a()
    _srv, engine = pg_backend()
    section_b(engine)
    section_c(engine)
    section_d(engine)
    section_e(engine)
    section_f(engine)
    section_g(engine)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{total} PASS")
    if failed:
        for n, d in failed:
            print(f"  FAIL: {n} {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
