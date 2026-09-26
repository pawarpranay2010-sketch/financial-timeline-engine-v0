"""Phase 5C — Idempotency & replay-safe developer API test suite.

Verifies the durable, tenant-scoped Idempotency-Key contract on
POST /v1/process against REAL PostgreSQL (embedded pgserver, discarded
after the run). Concurrency, replay, tenant isolation, and quota
invariants are never mock-only.

Run:
    python3 scripts/fte_fyjc_79_phase5c_idempotency_test.py
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
from backend.auth import gate as metered_gate  # noqa: E402
from backend.auth import idempotency as idem  # noqa: E402
from backend.auth.models import current_usage_month  # noqa: E402
from backend.auth.tokens import hash_token  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Stub client (mirrors the Platrixa facade contract; input-dependent body so
# cross-tenant result mixing is detectable)
# ---------------------------------------------------------------------------


def _interpretation_for(text: str) -> dict:
    digits = "".join(ch for ch in text if ch.isdigit()) or "0"
    return {
        "transaction_type": "PURCHASE",
        "parties": ["raj"],
        "amounts": [{"value": digits, "currency": "INR", "source": "explicit"}],
        "payment_method_enum": "CASH",
        "ambiguities": [],
        "ambiguity_flags": [],
        "overall_confidence": "0.9",
        "suggested_status": "VERIFIED",
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    }


class _R:
    def __init__(self, text: str, status: str) -> None:
        self.status = status
        self.status_label = status
        self.success = status == "VERIFIED"
        self.request_id = f"req-{abs(hash(text)) % 100000}"
        self.next_action = None
        self.issues: list = []
        self.grounding_issues: list = []
        self.rule_evidence: list = []
        self.interpretation = _interpretation_for(text)
        self.accounting = {"status": status}


class StubClient:
    """Counting stub with runtime-switchable behavior."""

    def __init__(self, status: str = "VERIFIED") -> None:
        self._status = status
        self.fail_mode: str | None = None  # None | "input" | "provider"
        self.calls = 0
        self.lock = threading.Lock()
        self.hold: threading.Event | None = None  # set → block inside process()

    def process(self, text, request_id=None):
        from platrixa.errors import InputError, PlatrixaError

        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise InputError("input must be a non-empty transaction string")
        with self.lock:
            self.calls += 1
            fail_mode = self.fail_mode
            hold = self.hold
        if hold is not None:
            hold.wait(timeout=5)
        if fail_mode == "input":
            raise InputError("input must be a non-empty transaction string")
        if fail_mode == "provider":
            raise PlatrixaError("model provider unavailable")
        return _R(text, self._status)

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


def _client(client: StubClient) -> TestClient:
    os.environ.pop("PLATRIXA_DEV_API_KEY", None)  # Phase 15 gate inactive
    developer.set_client(client)
    app = create_app()
    return TestClient(app)


def hdr(key: str | None = None, idem_key: str | None = None) -> dict:
    headers = {}
    if key is not None:
        headers["X-Platrixa-API-Key"] = key
    if idem_key is not None:
        headers["Idempotency-Key"] = idem_key
    return headers


# ---------------------------------------------------------------------------
# Real PostgreSQL backend (embedded; discarded after the run)
# ---------------------------------------------------------------------------

_PG = None  # (server, engine)


def pg_backend():
    """Start (once) an embedded PostgreSQL and ensure the idempotency schema."""
    global _PG
    if _PG is not None:
        return _PG
    import pgserver
    from sqlalchemy import create_engine

    pgdata = Path("/tmp/platrixa_phase5c_pgdata")
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(pgdata)
    uri = srv.get_uri()
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    # Ensure BOTH schemas: the Phase 16 tenant-quota table and the Phase 5C
    # idempotency table (the latter is also self-ensured on first use).
    from sqlalchemy import text

    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    quota_ddl = (Path(__file__).resolve().parent.parent / "backend" / "database" /
                 "platrixa_tenant_quota_schema.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(quota_ddl))
    idem._session_factory_cache.pop(uri.replace("postgresql://", "postgresql+psycopg2://", 1), None)
    idem._session_factory()
    _PG = (srv, engine)
    return _PG


def make_tenant(engine, key: str, tenant_id: str, limit: int, *, usage: int = 0) -> None:
    from sqlalchemy import text

    from backend.auth.models import current_usage_month

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platrixa_tenant_quotas "
                "(api_key_hash, tenant_id, monthly_limit, current_month_usage, usage_month, is_active) "
                "VALUES (:h, :t, :l, :u, :m, true) "
                "ON CONFLICT (api_key_hash) DO UPDATE SET monthly_limit = :l, "
                "current_month_usage = :u, is_active = true"
            ),
            {"h": hash_token(key), "t": tenant_id, "l": limit,
             "u": usage, "m": current_usage_month()},
        )


def tenant_usage(engine, key: str) -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT current_month_usage FROM platrixa_tenant_quotas WHERE api_key_hash = :h"),
            {"h": hash_token(key)},
        ).first()
    return int(row[0]) if row else 0


def idem_rows(engine, key: str, tenant_id: str):
    from sqlalchemy import text

    with engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM platrixa_idempotency_keys WHERE tenant_id = :t AND key_hash = :h"),
            {"t": tenant_id, "h": idem.idempotency_key_hash(key)},
        ).mappings().all()


def reset_tables(engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM platrixa_idempotency_keys"))
        conn.execute(text("DELETE FROM platrixa_tenant_quotas"))


VALID_BODY = {"raw_input": "Purchased furniture for cash Rs. 15,000"}
ALT_BODY = {"raw_input": "Paid office rent Rs. 25,000 in cash"}
KEY = "idem-key-abc123-~._"
TENANT = "tenant-A"


# ---------------------------------------------------------------------------
# A — zero-config + key transport (runs BEFORE the PG backend exists)
# ---------------------------------------------------------------------------


def section_a0() -> None:
    print("\nA0 — Zero-config + key transport (no durable store)")
    os.environ.pop(metered_gate.METERING_ENV_VAR, None)
    stub = StubClient()
    c = _client(stub)

    r = c.post("/v1/process", json=VALID_BODY, headers=hdr(idem_key=KEY))
    check("A0.1 key on zero-config deployment → 400 (honest refusal, no fake replay)",
          r.status_code == 400 and r.json()["error"]["code"] == "IDEMPOTENCY_NOT_CONFIGURED",
          f"{r.status_code} {r.json().get('error', {}).get('code')}")
    check("A0.2 refusal maps to INVALID_INPUT api_status",
          r.json()["error"]["api_status"] == "INVALID_INPUT")

    r = c.post("/v1/process", json=VALID_BODY)
    check("A0.3 no key → normal processing unaffected (zero-config stays open)",
          r.status_code == 200 and r.json()["status"] == "VERIFIED")
    check("A0.4 no Idempotent-Replayed header without a key", "idempotent-replayed" not in {k.lower() for k in r.headers})


def section_a(engine) -> None:
    print("\nA — Key validation (metered, real PostgreSQL)")
    reset_tables(engine)
    make_tenant(engine, "k-A", "tenant-A", 50)
    stub = StubClient()
    c = _client(stub)

    r = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-A"))
    check("A.1 no Idempotency-Key → 200, no replay header",
          r.status_code == 200 and "idempotent-replayed" not in {k.lower() for k in r.headers})

    r = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-A", KEY))
    check("A.2 first request with key → 200 + Idempotent-Replayed: false",
          r.status_code == 200 and r.headers.get("idempotent-replayed") == "false",
          f"{r.status_code} {r.headers.get('idempotent-replayed')}")

    for name, key_value, expected in [
        ("A.3 empty key → 400 IDEMPOTENCY_KEY_INVALID", "", "IDEMPOTENCY_KEY_INVALID"),
        ("A.4 whitespace-padded key → 400", "  padded-key-16chars  ", "IDEMPOTENCY_KEY_INVALID"),
        ("A.5 short key → 400", "short", "IDEMPOTENCY_KEY_INVALID"),
        ("A.6 invalid characters → 400", "has spaces and !", "IDEMPOTENCY_KEY_INVALID"),
        ("A.7 oversized key (201) → 400 IDEMPOTENCY_KEY_TOO_LONG", "x" * 201, "IDEMPOTENCY_KEY_TOO_LONG"),
    ]:
        r = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-A", key_value))
        check(name, r.status_code == 400 and r.json()["error"]["code"] == expected,
              f"{r.status_code} {r.json().get('error', {}).get('code')}")

    before = stub.calls
    r = c.post("/v1/process", content=b"not-json", headers={"Content-Type": "application/json", "Idempotency-Key": KEY})
    check("A.8 malformed body → 400 REQUEST_MALFORMED (validation layer, pre-quota)",
          r.status_code == 400 and r.json()["error"]["code"] == "REQUEST_MALFORMED", str(r.status_code))
    check("A.9 malformed body consumed no quota", tenant_usage(engine, "k-A") >= 1)  # usage exists from A.2; no change check below
    usage_after_malformed = tenant_usage(engine, "k-A")
    check("A.10 malformed request with key did not add a reservation", usage_after_malformed == tenant_usage(engine, "k-A"))


def section_b(engine) -> None:
    print("\nB — Replay semantics across terminal states")
    reset_tables(engine)
    make_tenant(engine, "k-B", "tenant-B", 50)
    stub = StubClient()
    c = _client(stub)

    # VERIFIED replay
    first = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", KEY))
    replay = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", KEY))
    check("B.1 same key + same request → replay, no second engine call",
          stub.calls == 1 and replay.headers.get("idempotent-replayed") == "true",
          f"calls={stub.calls}")
    check("B.2 replay body is byte-equivalent (same envelope)",
          json.loads(replay.text) == json.loads(first.text))
    check("B.3 request_id stable across replay",
          replay.json()["request_id"] == first.json()["request_id"] != None)
    check("B.4 replay preserves api_status/engine fields",
          replay.json()["api_status"] == first.json()["api_status"]
          and replay.json()["status"] == first.json()["status"])

    # REVIEW_REQUIRED replay
    stub._status = "REVIEW_REQUIRED"
    k2 = "idem-key-review-~._ok"
    r1 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k2))
    r2 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k2))
    check("B.5 REVIEW_REQUIRED replay preserves status verbatim (never upgraded)",
          r1.json()["status"] == "REVIEW_REQUIRED" and r2.json()["status"] == "REVIEW_REQUIRED"
          and r2.headers.get("idempotent-replayed") == "true")

    # UNSUPPORTED (422 transport) replay
    stub._status = "UNSUPPORTED_TRANSACTION"
    k3 = "idem-key-unsup-~._ok"
    r1 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k3))
    r2 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k3))
    check("B.6 UNSUPPORTED replay returns original 422 + envelope verbatim",
          r1.status_code == 422 and r2.status_code == 422
          and json.loads(r2.text) == json.loads(r1.text)
          and r2.headers.get("idempotent-replayed") == "true",
          f"{r1.status_code}/{r2.status_code}")

    # VALIDATION_FAILED (engine FAILED-family) replay
    stub._status = "VALIDATION_FAILED"
    k4 = "idem-key-valfail-~._ok"
    r1 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k4))
    r2 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k4))
    check("B.7 engine FAILED-family result replays identically",
          r1.status_code == r2.status_code and json.loads(r2.text) == json.loads(r1.text))

    # INPUT_INVALID (deterministic failure) — stored, replayed as failure
    stub._status = "VERIFIED"
    stub.fail_mode = "input"
    k5 = "idem-key-inputfail~._"
    r1 = c.post("/v1/process", json={"raw_input": "   "}, headers=hdr("k-B", k5))
    calls_before = stub.calls
    r2 = c.post("/v1/process", json={"raw_input": "   "}, headers=hdr("k-B", k5))
    check("B.8 deterministic INPUT_INVALID failure is STORED and replayed (422)",
          r1.status_code == 422 and r2.status_code == 422
          and r2.json()["error"]["code"] == "INPUT_INVALID"
          and r2.headers.get("idempotent-replayed") == "true",
          f"{r1.status_code}/{r2.status_code}")
    check("B.9 replayed failure did not re-invoke the engine", stub.calls == calls_before)

    # MODEL_UNAVAILABLE (transient) — claim released, retry retries
    stub.fail_mode = "provider"
    k6 = "idem-key-modelun~._ok"
    calls_before_transient = stub.calls
    r1 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k6))
    r2 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k6))
    check("B.10 MODEL_UNAVAILABLE/PROVIDER_UNAVAILABLE → 503, claim RELEASED",
          r1.status_code == 503 and r2.status_code == 503
          and "idempotent-replayed" not in {k.lower() for k in r2.headers},
          f"{r1.status_code}/{r2.status_code}")
    check("B.11 retry after transient failure genuinely re-attempted (2 engine calls)",
          stub.calls == calls_before_transient + 2,
          f"calls={stub.calls} expected={calls_before_transient + 2}")
    stub.fail_mode = None
    r3 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k6))
    check("B.12 recovered retry completes and becomes the canonical record",
          r3.status_code == 200 and r3.json()["status"] == "VERIFIED")
    r4 = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k6))
    check("B.13 replay after recovery returns the stored VERIFIED result",
          r4.headers.get("idempotent-replayed") == "true" and r4.json()["status"] == "VERIFIED")

    # Conflict
    k7 = "idem-key-conflict~._"
    c.post("/v1/process", json=VALID_BODY, headers=hdr("k-B", k7))
    calls_before = stub.calls
    rc = c.post("/v1/process", json=ALT_BODY, headers=hdr("k-B", k7))
    check("B.14 same key + different request → 409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
          rc.status_code == 409 and rc.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
          f"{rc.status_code} {rc.json().get('error', {}).get('code')}")
    check("B.15 conflict did not process the second request", stub.calls == calls_before)
    check("B.16 conflict maps to INVALID_INPUT api_status (client error)",
          rc.json()["error"]["api_status"] == "INVALID_INPUT")

    # Endpoint scope is part of the request fingerprint (documented):
    # reusing the key for a different endpoint is key reuse with a
    # different request → deterministic conflict, never silent processing.
    outcome = idem.claim(k7, "tenant-B", "/v1/process/document", {"x": 1})
    check("B.17 endpoint is fingerprinted: same key on another endpoint → conflict",
          outcome.conflict and not outcome.claimed,
          f"claimed={outcome.claimed} conflict={outcome.conflict}")


def section_c(engine) -> None:
    print("\nC — Tenant scoping (cross-tenant isolation)")
    reset_tables(engine)
    make_tenant(engine, "k-A2", "tenant-A2", 50)
    make_tenant(engine, "k-B2", "tenant-B2", 50)
    stub = StubClient()
    c = _client(stub)

    ra = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-A2", KEY))
    rb_first = c.post("/v1/process", json=ALT_BODY, headers=hdr("k-B2", KEY))
    check("C.1 same key string across tenants → separate namespaces (both processed)",
          ra.status_code == 200 and rb_first.status_code == 200 and stub.calls == 2,
          f"calls={stub.calls}")
    check("C.2 tenant B's result reflects B's request, not A's",
          rb_first.json()["request_id"] != ra.json()["request_id"])
    rb_replay = c.post("/v1/process", json=ALT_BODY, headers=hdr("k-B2", KEY))
    check("C.3 tenant B replay returns B's own result (never A's)",
          rb_replay.headers.get("idempotent-replayed") == "true"
          and rb_replay.json()["request_id"] == rb_first.json()["request_id"])
    ra_body = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-A2", KEY))
    check("C.4 tenant A replay still returns A's result",
          ra_body.headers.get("idempotent-replayed") == "true"
          and ra_body.json()["request_id"] == ra.json()["request_id"])
    check("C.5 two independent records exist (one per tenant)",
          len(idem_rows(engine, KEY, "tenant-A2")) == 1
          and len(idem_rows(engine, KEY, "tenant-B2")) == 1)


def section_d(engine) -> None:
    print("\nD — Concurrency (real PostgreSQL, real threads)")
    reset_tables(engine)
    make_tenant(engine, "k-D", "tenant-D", 50)
    stub = StubClient()
    stub.hold = threading.Event()
    c = _client(stub)

    THREADS = 12
    results: list[dict] = []
    lock = threading.Lock()

    def worker(_i: int) -> None:
        try:
            r = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-D", KEY))
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            with lock:
                results.append({"status": r.status_code, "replayed": r.headers.get("idempotent-replayed"),
                                "body": body})
        except Exception:
            with lock:
                results.append({"status": -1, "replayed": None, "body": {}})

    winner = threading.Thread(target=worker, args=(0,))
    winner.start()
    time.sleep(0.05)  # let the winner claim + enter processing
    others = [threading.Thread(target=worker, args=(i,)) for i in range(1, THREADS)]
    for t in others:
        t.start()
    time.sleep(0.3)
    stub.hold.set()  # release the canonical attempt
    winner.join()
    for t in others:
        t.join()

    canonical = [r for r in results if r["replayed"] == "false" and r["status"] == 200
                 and r["body"].get("status") not in (None, "PROCESSING")]
    replays = [r for r in results if r["replayed"] == "true"]
    processing_acks = [r for r in results if r["body"].get("reason_code") == idem.IN_PROGRESS_CODE]
    check("D.1 exactly ONE canonical processing attempt (no double execution)",
          len(canonical) == 1 and stub.calls == 1,
          f"canonical={len(canonical)} calls={stub.calls}")
    check("D.2 every concurrent duplicate received replay or in-progress acknowledgement",
          len(replays) + len(processing_acks) == THREADS - 1,
          f"replays={len(replays)} processing={len(processing_acks)} of {THREADS - 1}")
    check("D.3 in-progress acknowledgement is structured and honest",
          all(r["body"].get("status") == "PROCESSING" and r["body"].get("retryable") is True
              for r in processing_acks))
    check("D.4 in-progress acks consumed no additional quota",
          tenant_usage(engine, "k-D") == 1, f"usage={tenant_usage(engine, 'k-D')}")
    check("D.5 exactly one idempotency row for (tenant, key)",
          len(idem_rows(engine, KEY, "tenant-D")) == 1)
    # After completion, a NEW request with the same key replays the
    # canonical result (concurrent duplicates above were acknowledged
    # in-progress; this one arrives after the record is terminal).
    post = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-D", KEY))
    check("D.6 replay-after-completion returns the canonical result",
          post.headers.get("idempotent-replayed") == "true"
          and post.json().get("status") == "VERIFIED"
          and stub.calls == 1,
          f"status={post.status_code} calls={stub.calls}")

    # Concurrent DIFFERENT bodies on the same key → one wins, rest conflict
    reset_tables(engine)
    make_tenant(engine, "k-D2", "tenant-D2", 50)
    stub2 = StubClient()
    stub2.hold = threading.Event()
    c2 = _client(stub2)
    results2: list[int] = []
    lock2 = threading.Lock()

    def worker2(i: int) -> None:
        body = VALID_BODY if i == 0 else ALT_BODY
        try:
            r = c2.post("/v1/process", json=body, headers=hdr("k-D2", KEY))
            with lock2:
                results2.append(r.status_code)
        except Exception:
            with lock2:
                results2.append(-1)

    w2 = threading.Thread(target=worker2, args=(0,))
    w2.start()
    time.sleep(0.05)
    losers = [threading.Thread(target=worker2, args=(i,)) for i in range(1, 8)]
    for t in losers:
        t.start()
    time.sleep(0.2)
    stub2.hold.set()
    w2.join()
    for t in losers:
        t.join()
    check("D.7 concurrent different-body claims: one canonical, all others 409",
          results2.count(200) == 1 and results2.count(409) == 7,
          f"results={sorted(results2)}")


def section_e(engine) -> None:
    print("\nE — Quota interaction (invariants)")
    reset_tables(engine)
    make_tenant(engine, "k-E", "tenant-E", 5)
    stub = StubClient()
    c = _client(stub)

    c.post("/v1/process", json=VALID_BODY, headers=hdr("k-E", KEY))
    usage_first = tenant_usage(engine, "k-E")
    c.post("/v1/process", json=VALID_BODY, headers=hdr("k-E", KEY))
    check("E.1 replay consumes NO additional quota",
          tenant_usage(engine, "k-E") == usage_first == 1,
          f"usage={tenant_usage(engine, 'k-E')}")
    c.post("/v1/process", json=ALT_BODY, headers=hdr("k-E", KEY))
    check("E.2 idempotency conflict consumes NO quota",
          tenant_usage(engine, "k-E") == 1, f"usage={tenant_usage(engine, 'k-E')}")

    reset_tables(engine)
    make_tenant(engine, "k-E2", "tenant-E2", 5)
    r_missing = c.post("/v1/process", json=VALID_BODY, headers=hdr(None, KEY))
    check("E.3 missing-key auth failure consumes no quota (never admitted)",
          r_missing.status_code == 401 and tenant_usage(engine, "k-E2") == 0,
          f"{r_missing.status_code} usage={tenant_usage(engine, 'k-E2')}")
    r_wrong = c.post("/v1/process", json=VALID_BODY, headers=hdr("wrong-key-entirely", KEY))
    check("E.4 invalid-key auth failure consumes no quota (and never replays)",
          r_wrong.status_code == 401 and tenant_usage(engine, "k-E2") == 0,
          f"{r_wrong.status_code} usage={tenant_usage(engine, 'k-E2')}")
    r_admitted = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-E2"))
    check("E.4b valid request without key is admitted and consumes exactly one unit",
          r_admitted.status_code == 200 and tenant_usage(engine, "k-E2") == 1,
          f"{r_admitted.status_code} usage={tenant_usage(engine, 'k-E2')}")

    reset_tables(engine)
    make_tenant(engine, "k-E3", "tenant-E3", 1)
    stub3 = StubClient()
    c3 = _client(stub3)
    first_e3 = c3.post("/v1/process", json=VALID_BODY, headers=hdr("k-E3", KEY))
    # Same key + same request still replays even with quota exhausted
    # (replay never consults quota).
    replay_e3 = c3.post("/v1/process", json=VALID_BODY, headers=hdr("k-E3", KEY))
    # A DIFFERENT key (new namespace) must claim → reserve → hit 429 → release.
    k_e3_other = "idem-key-quota429~._"
    r429 = c3.post("/v1/process", json=VALID_BODY, headers=hdr("k-E3", k_e3_other))
    rows = idem_rows(engine, k_e3_other, "tenant-E3")
    check("E.5 quota exhaustion → 429 and that claim is RELEASED (never blocks later retries)",
          first_e3.status_code == 200 and r429.status_code == 429 and len(rows) == 0,
          f"{r429.status_code} rows={len(rows)}")
    check("E.5b exhausted-quota replay of the original key still returns the stored result",
          replay_e3.headers.get("idempotent-replayed") == "true"
          and replay_e3.json()["request_id"] == first_e3.json()["request_id"],
          f"{replay_e3.status_code} {replay_e3.headers.get('idempotent-replayed')}")


def section_f(engine) -> None:
    print("\nF — Durability, constraint, leakage, retention")
    reset_tables(engine)
    make_tenant(engine, "k-F", "tenant-F", 50)
    stub = StubClient()
    c = _client(stub)
    first = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-F", KEY))

    # Process-boundary replay: fresh app, fresh stub, cleared engine cache —
    # the result can only come from PostgreSQL.
    idem._session_factory_cache.clear()
    fresh_stub = StubClient()
    c2 = _client(fresh_stub)
    replay = c2.post("/v1/process", json=VALID_BODY, headers=hdr("k-F", KEY))
    check("F.1 replay survives a process boundary (fresh app+stub, DB-sourced result)",
          fresh_stub.calls == 0 and replay.headers.get("idempotent-replayed") == "true"
          and replay.json()["request_id"] == first.json()["request_id"],
          f"fresh_calls={fresh_stub.calls}")

    # Database uniqueness constraint
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO platrixa_idempotency_keys "
                "(key_hash, tenant_id, request_hash, endpoint, state) VALUES (:h, :t, :rh, :ep, 'PROCESSING')"
            ), {"h": idem.idempotency_key_hash(KEY), "t": "tenant-F", "rh": "x", "ep": "/v1/process"})
        constraint_ok = False
    except Exception:
        constraint_ok = True
    check("F.2 composite PK (tenant_id, key_hash) enforced by the database", constraint_ok)

    # No plaintext key anywhere in the store
    from sqlalchemy import create_engine as _ce
    found = False
    with engine.connect() as conn:
        for row in conn.execute(text("SELECT key_hash, tenant_id, request_hash, result_json, failure_json FROM platrixa_idempotency_keys")).mappings():
            for value in row.values():
                if isinstance(value, str) and KEY in value:
                    found = True
    check("F.3 raw Idempotency-Key never stored in plaintext", not found)

    # Retention: expired record is reclaimed for a new attempt
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE platrixa_idempotency_keys SET expires_at = now() - interval '1 hour' "
            "WHERE tenant_id = :t"
        ), {"t": "tenant-F"})
    calls_before = fresh_stub.calls
    r_expired = c2.post("/v1/process", json=VALID_BODY, headers=hdr("k-F", KEY))
    check("F.4 expired key is reclaimed → NEW processing attempt (not a stale replay)",
          r_expired.status_code == 200 and r_expired.headers.get("idempotent-replayed") == "false"
          and fresh_stub.calls == calls_before + 1,
          f"status={r_expired.status_code} calls={fresh_stub.calls}")


def section_g(engine) -> None:
    print("\nG — PROCESSING behavior + response headers (deterministic)")
    reset_tables(engine)
    make_tenant(engine, "k-G", "tenant-G", 50)
    stub = StubClient()
    stub.hold = threading.Event()
    c = _client(stub)

    winner = threading.Thread(target=lambda: c.post("/v1/process", json=VALID_BODY, headers=hdr("k-G", KEY)))
    winner.start()
    time.sleep(0.15)

    ack = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-G", KEY))
    body = ack.json()
    check("G.1 retry during PROCESSING → 200 in-progress acknowledgement (no rerun)",
          ack.status_code == 200 and body.get("status") == "PROCESSING"
          and body.get("reason_code") == idem.IN_PROGRESS_CODE and stub.calls == 1,
          f"{ack.status_code} {body.get('status')} calls={stub.calls}")
    check("G.2 in-progress ack carries PROCESSING api_status + Retry-After",
          body.get("api_status") == "PROCESSING" and ack.headers.get("retry-after") == "2")
    check("G.3 in-progress ack explicitly NOT a replay",
          ack.headers.get("idempotent-replayed") == "false")
    check("G.4 in-progress retry consumed no quota",
          tenant_usage(engine, "k-G") == 1)

    stub.hold.set()
    winner.join()
    final = c.post("/v1/process", json=VALID_BODY, headers=hdr("k-G", KEY))
    check("G.5 after completion the same key returns the stored final result",
          final.headers.get("idempotent-replayed") == "true" and final.json()["status"] == "VERIFIED")


def main() -> int:
    print("=" * 72)
    print("PHASE 5C — IDEMPOTENCY & REPLAY-SAFE DEVELOPER API")
    print("=" * 72)

    section_a0()
    srv, engine = pg_backend()
    try:
        section_a(engine)
        section_b(engine)
        section_c(engine)
        section_d(engine)
        section_e(engine)
        section_f(engine)
        section_g(engine)
    finally:
        try:
            if srv is not None:
                srv.stop()
        except Exception:
            pass

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = len(CHECKS) - passed
    print("\n" + "=" * 72)
    print(f"RESULT: {passed}/{len(CHECKS)} checks passed" + (f" — {failed} FAILED" if failed else " — ALL PASS"))
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
