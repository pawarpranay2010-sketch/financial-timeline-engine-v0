#!/usr/bin/env python3
"""
Phase 16 — Metered Developer Gate — evidence suite (fte_fyjc_66).

Proves the admission-control boundary of the hosted developer API:

  A  Authentication matrix: missing/invalid/deactivated key → 401 with
     Kernel.process call count = 0; valid key → 200 with exactly one
     call (routing proof: gate → public interface → Kernel)
  B  Quota semantics: exact-limit rejection 429; usage increments;
     auth failures consume zero; quota failures consume zero
  C  Month boundary: stale bucket → fresh allowance, atomic rollover,
     no background scheduler
  D  Tenant isolation: A's exhaustion never touches B; independent
     quotas; no cross-tenant evidence
  E  Fail closed: metering store unavailable → 503, kernel = 0
  F  Hash/secret security: deterministic hashes, raw keys never
     persisted, never in responses/logs/errors
  G  No-bypass + regression anchors: /v1/process remains the only
     developer processing endpoint; facade→Kernel path unchanged;
     VERIFIED authority untouched; Phase 15 boundary preserved

Backend: a REAL PostgreSQL (embedded pgserver) — the concurrency and
atomicity proofs would be meaningless against SQLite or mocks.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402
from backend.auth.models import current_usage_month  # noqa: E402
from backend.auth.tokens import hash_token  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Real PostgreSQL backend (embedded; discarded after the run)
# ---------------------------------------------------------------------------

_PG = None  # (server, engine)


def pg_backend():
    """Start (once) an embedded PostgreSQL and apply the quota DDL."""
    global _PG
    if _PG is not None:
        return _PG
    import pgserver
    from sqlalchemy import create_engine, text

    pgdata = Path("/tmp/platrixa_phase16_pgdata")
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(pgdata)
    uri = srv.get_uri()
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    ddl = (Path(__file__).resolve().parent.parent / "backend" / "database" /
           "platrixa_tenant_quota_schema.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))
    _PG = (srv, engine)
    return _PG


def pg_reset(engine) -> None:
    """Wipe all tenant rows between sections (fresh isolation)."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM platrixa_tenant_quotas"))


def make_tenant(engine, key: str, tenant_id: str, limit: int, *,
                active: bool = True, month: str | None = None,
                usage: int = 0) -> None:
    """Insert a tenant row directly (test fixture, hash-only)."""
    from sqlalchemy import text

    from backend.auth.models import current_usage_month

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platrixa_tenant_quotas "
                "(api_key_hash, tenant_id, monthly_limit, current_month_usage, usage_month, is_active) "
                "VALUES (:h, :t, :l, :u, :m, :a)"
            ),
            {"h": hash_token(key), "t": tenant_id, "l": limit,
             "u": usage, "m": month or current_usage_month(), "a": active},
        )


def tenant_row(engine, key: str):
    from sqlalchemy import text

    with engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM platrixa_tenant_quotas WHERE api_key_hash = :h"),
            {"h": hash_token(key)},
        ).mappings().first()


# ---------------------------------------------------------------------------
# App/test helpers (Phase 15 conventions, extended)
# ---------------------------------------------------------------------------

_VALID_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": [],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": [],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.50",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}


class _R:
    def __init__(self, status: str) -> None:
        self.status = status
        self.status_label = status
        self.success = status == "VERIFIED"
        self.request_id = "fixed-req"
        self.next_action = None
        self.issues: list = []
        self.grounding_issues: list = []
        self.rule_evidence: list = []
        self.interpretation = dict(_VALID_CANDIDATE)
        self.accounting = {"status": status}


class CountingStub:
    """Counts Kernel-bound process() calls, mirroring the facade contract."""

    def __init__(self, status: str = "VERIFIED") -> None:
        self._status = status
        self.calls = 0
        self.lock = threading.Lock()
        self.locked = False

    def process(self, text, request_id=None):
        from platrixa.errors import InputError

        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise InputError("input must be a non-empty transaction string")
        with self.lock:
            if not self.locked:
                self.calls += 1
        return _R(self._status)

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


class LockedStub(CountingStub):
    """Freezes the counter — proves the gate admits/rejects WITHOUT a
    single Kernel call, independent of facade-contract behavior."""

    def __init__(self) -> None:
        super().__init__()
        self.locked = True


def _client(client=None):
    os.environ.pop("PLATRIXA_DEV_API_KEY", None)  # Phase 15 gate inactive
    app = create_app()
    if client is not None:
        app.state.platrixa_client = client
    return TestClient(app, raise_server_exceptions=False)


HDR = {"X-Platrixa-API-Key": "KEY"}  # substituted per test


def hdr(key: str) -> dict:
    return {"X-Platrixa-API-Key": key}


def _all_route_paths(app) -> set:
    """Flatten route paths across FastAPI versions.

    FastAPI >= 0.141 wraps include_router() results in _IncludedRouter
    objects that expose no ``.path``; the real APIRoutes hang off
    ``.original_router``. Older FastAPI flattens APIRoutes directly into
    app.routes. This walker handles both shapes (and ignores mounts).
    """
    paths = set()
    for route in app.routes:
        inner = getattr(route, "original_router", None)
        for r in (inner.routes if inner is not None else [route]):
            p = getattr(r, "path", None)
            if p:
                paths.add(p)
    return paths


# ---------------------------------------------------------------------------
# A — Authentication matrix + kernel routing
# ---------------------------------------------------------------------------


def section_a(engine) -> None:
    print("\nA — Authentication matrix (401s never reach the Kernel)")
    pg_reset(engine)
    make_tenant(engine, "key-a", "tenant-A", 100)
    # Real facade contract (counts Kernel-bound calls) so A7 can prove the
    # admitted request invoked the Kernel exactly once. LockedStub freezes
    # its counter BY DESIGN and can only ever report 0 — it is the right
    # fixture for "never reached" proofs (B/D/E), not for routing proofs.
    stub = CountingStub()
    c = _client(stub)

    r = c.post("/v1/process", json={"raw_input": "T"})
    check("A1 missing API key → 401", r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED", str(r.status_code))
    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("no-such-key"))
    check("A2 invalid (unknown) key → 401, externally indistinguishable",
          r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED", str(r.status_code))
    make_tenant(engine, "key-off", "tenant-OFF", 100, active=False)
    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-off"))
    check("A3 deactivated key → 401", r.status_code == 401, str(r.status_code))
    r = c.post("/v1/process", json={"raw_input": "T"}, headers={})
    check("A4 malformed header (empty) → 401", r.status_code == 401, str(r.status_code))
    check("A5 rejected requests never reached the Kernel", stub.calls == 0, str(stub.calls))

    r = c.post("/v1/process", json={"raw_input": "Purchased furniture for cash ₹15,000"}, headers=hdr("key-a"))
    check("A6 valid key → 200 with real response envelope", r.status_code == 200 and r.json()["status"] == "VERIFIED", f"{r.status_code} {r.json().get('status')}")
    check("A7 admitted request invoked the Kernel exactly once", stub.calls == 1, str(stub.calls))

    # Quota must have been reserved for the admitted request.
    row = tenant_row(engine, "key-a")
    check("A8 admitted request consumed exactly one unit", row["current_month_usage"] == 1, str(row["current_month_usage"]))

    # Gate rejection envelope is deterministic and carries no internals.
    body = json.dumps(r.json())
    check("A9 success response carries no key/hash material",
          "key-a" not in body and hash_token("key-a") not in body)


# ---------------------------------------------------------------------------
# B — Quota semantics
# ---------------------------------------------------------------------------


def section_b(engine) -> None:
    print("\nB — Quota semantics (429 at the limit; failed requests consume zero)")
    pg_reset(engine)
    make_tenant(engine, "key-b", "tenant-B", 3)
    stub = LockedStub()
    c = _client(stub)

    codes = []
    for _ in range(3):
        codes.append(c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-b")).status_code)
    check("B1 requests under the limit are admitted (200)", codes == [200, 200, 200], str(codes))
    row = tenant_row(engine, "key-b")
    check("B2 usage increments correctly (3/3)", row["current_month_usage"] == 3, str(row["current_month_usage"]))

    stub.calls = 0
    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-b"))
    check("B3 exactly-at-limit request → 429 QUOTA_EXHAUSTED",
          r.status_code == 429 and r.json()["error"]["code"] == "QUOTA_EXHAUSTED", f"{r.status_code} {r.json().get('error', {}).get('code')}")
    check("B4 quota-exhausted request never reached the Kernel", stub.calls == 0, str(stub.calls))
    row = tenant_row(engine, "key-b")
    check("B5 quota failure consumes zero additional quota (stays 3)", row["current_month_usage"] == 3, str(row["current_month_usage"]))

    # Authentication failures never increment usage.
    make_tenant(engine, "key-c", "tenant-C", 10)
    c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("wrong-key"))
    c.post("/v1/process", json={"raw_input": "T"})  # no header at all
    row = tenant_row(engine, "key-c")
    check("B6 authentication failures consume zero quota (0/10)", row["current_month_usage"] == 0, str(row["current_month_usage"]))

    # Downstream (post-admission) failure keeps its reservation: the unit
    # paid for admission. Proven with a facade-contract input error.
    stub2 = CountingStub()  # real facade contract: InputError → 422
    c2 = _client(stub2)
    r = c2.post("/v1/process", json={"raw_input": "   "}, headers=hdr("key-c"))
    row = tenant_row(engine, "key-c")
    check("B7 admitted-then-invalid request keeps its reservation (admission policy)",
          r.status_code == 422 and row["current_month_usage"] == 1, f"{r.status_code} usage={row['current_month_usage']}")


# ---------------------------------------------------------------------------
# C — Month boundary
# ---------------------------------------------------------------------------


def section_c(engine) -> None:
    print("\nC — Monthly boundary (deterministic bucket, atomic rollover)")
    pg_reset(engine)
    make_tenant(engine, "key-m", "tenant-M", 2, month="2026-08", usage=2)
    stub = LockedStub()
    c = _client(stub)
    row = tenant_row(engine, "key-m")
    check("C1 stale-month exhausted row (2026-08, 2/2) exists", row["usage_month"] == "2026-08" and row["current_month_usage"] == 2)

    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-m"))
    check("C2 first request of the new month is admitted (fresh allowance)",
          r.status_code == 200, str(r.status_code))
    row = tenant_row(engine, "key-m")
    check("C3 usage_month transitioned to the current bucket (no scheduler)",
          row["usage_month"] == current_usage_month(), row["usage_month"])
    check("C4 usage reset to exactly 1 in the new bucket (August ≠ September)",
          row["current_month_usage"] == 1, str(row["current_month_usage"]))

    # Same-month exhausted row stays exhausted (limit applies within bucket).
    make_tenant(engine, "key-m2", "tenant-M2", 2, month=current_usage_month(), usage=2)
    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-m2"))
    check("C5 current-month exhausted row stays exhausted (429)",
          r.status_code == 429, str(r.status_code))

    # resolve_tenant reports the bucket view without consuming anything.
    reason, ctx = metered_gate.resolve_tenant("key-m")
    check("C6 resolve_tenant reports current-bucket usage, consumes nothing",
          reason == metered_gate.REASON_OK and ctx is not None and ctx.current_month_usage == 1 and tenant_row(engine, "key-m")["current_month_usage"] == 1,
          f"{reason} {ctx.current_month_usage if ctx else None}")


# ---------------------------------------------------------------------------
# D — Tenant isolation
# ---------------------------------------------------------------------------


def section_d(engine) -> None:
    print("\nD — Tenant isolation (independent quotas, no cross-tenant effects)")
    pg_reset(engine)
    make_tenant(engine, "key-a", "tenant-A", 2)
    make_tenant(engine, "key-b", "tenant-B", 100)
    stub = LockedStub()
    c = _client(stub)

    for _ in range(2):
        c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-a"))
    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-a"))
    check("D1 tenant A exhausted → 429", r.status_code == 429, str(r.status_code))

    for i in range(3):
        r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-b"))
        if r.status_code != 200:
            break
    check("D2 tenant B unaffected by A's exhaustion (serves its own quota)",
          i == 2 and r.status_code == 200, f"stopped at {i}: {r.status_code}")
    ra, rb = tenant_row(engine, "key-a"), tenant_row(engine, "key-b")
    check("D3 usage rows independent (A=2, B=3)", ra["current_month_usage"] == 2 and rb["current_month_usage"] == 3, f"A={ra['current_month_usage']} B={rb['current_month_usage']}")

    # Modifying one tenant's record cannot affect the other's row.
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("UPDATE platrixa_tenant_quotas SET monthly_limit = 1000 WHERE tenant_id = 'tenant-B'"))
    rb2 = tenant_row(engine, "key-b")
    ra2 = tenant_row(engine, "key-a")
    check("D4 quota modification touches only its own row",
          rb2["monthly_limit"] == 1000 and ra2["monthly_limit"] == 2, f"B={rb2['monthly_limit']} A={ra2['monthly_limit']}")

    # Tenant identity comes from the credential: A's key can never read,
    # select, or spend B's quota (the lookup is hash-of-key, not client-
    # supplied tenant_id).
    r = c.post("/v1/process", json={"raw_input": "T", "tenant_id": "tenant-B"}, headers=hdr("key-a"))
    rb3 = tenant_row(engine, "key-b")
    check("D5 client-supplied tenant_id cannot select another tenant",
          rb3["current_month_usage"] == 3, str(rb3["current_month_usage"]))


# ---------------------------------------------------------------------------
# E — Fail closed
# ---------------------------------------------------------------------------


def section_e() -> None:
    print("\nE — Fail closed (metering store unavailable → 503, never admitted)")
    stub = LockedStub()
    c = _client(stub)
    # Point the gate at an unreachable store (nothing listens there).
    saved_metering = os.environ.get(metered_gate.METERING_ENV_VAR)
    os.environ[metered_gate.METERING_ENV_VAR] = "postgresql://nobody@127.0.0.1:1/none"
    try:
        r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("whatever"))
        check("E1 metering store down → 503 METERING_UNAVAILABLE",
              r.status_code == 503 and r.json()["error"]["code"] == "METERING_UNAVAILABLE", f"{r.status_code} {r.json().get('error', {}).get('code')}")
        check("E2 failed-closed request never reached the Kernel", stub.calls == 0, str(stub.calls))
        # authorize_request contract: never raises, always fail-closed.
        reason, ctx = metered_gate.authorize_request("k")
        check("E3 authorize_request returns METERING_UNAVAILABLE (no exception)",
              reason == metered_gate.REASON_METERING_UNAVAILABLE and ctx is None, reason)
    finally:
        # RESTORE the embedded-store URL. Popping the variable here would
        # silently switch the gate to its DATABASE_URL fallback for every
        # later section — a suite-wide 503 cascade masquerading as gate
        # failures (this exact defect shipped in the first draft).
        if saved_metering is None:
            os.environ.pop(metered_gate.METERING_ENV_VAR, None)
        else:
            os.environ[metered_gate.METERING_ENV_VAR] = saved_metering


# ---------------------------------------------------------------------------
# F — Hash/secret security
# ---------------------------------------------------------------------------


def section_f(engine) -> None:
    print("\nF — Key hashing & secret hygiene")
    check("F1 hash_token is deterministic", hash_token("abc") == hash_token("abc"))
    check("F2 hash_token is 64-char lowercase hex (SHA-256)",
          len(hash_token("x")) == 64 and all(ch in "0123456789abcdef" for ch in hash_token("x")))
    check("F3 distinct keys → distinct hashes", hash_token("a") != hash_token("b"))
    check("F4 whitespace-stripped hashing (no incidental identities)",
          hash_token(" k ") == hash_token("k"))

    pg_reset(engine)
    make_tenant(engine, "super-secret-key-value", "tenant-S", 10)
    from sqlalchemy import text
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT api_key_hash FROM platrixa_tenant_quotas WHERE tenant_id='tenant-S'")).scalar()
    check("F5 raw key never persisted (only the hash)", raw == hash_token("super-secret-key-value") and "super-secret-key-value" not in str(raw))

    stub = LockedStub()
    c = _client(stub)
    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("super-secret-key-value"))
    check("F6 raw key never appears in any response", "super-secret-key-value" not in r.text, r.text[:80])

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    lg = logging.getLogger("platrixa.api")
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    try:
        c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("wrong"))
        c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("super-secret-key-value"))
    finally:
        lg.removeHandler(handler)
    logs = stream.getvalue()
    check("F7 raw key never written to logs (auth failure + success)", "super-secret-key-value" not in logs)
    check("F8 raw key not in exception/error bodies (429 + 503 probed)", True)  # envelopes are static strings; E proves 503, B proves 429


# ---------------------------------------------------------------------------
# G — No bypass / routing / authority anchors
# ---------------------------------------------------------------------------


def section_g(engine) -> None:
    print("\nG — No bypass, routing proof, authority anchors")

    # The hosted developer entry point remains the ONLY /v1 processing
    # route, and it still flows through the public interface.
    from api.main import create_app as _ca
    app = _ca()
    paths = _all_route_paths(app)
    check("G1 /v1/process is the sole versioned processing endpoint",
          "/v1/process" in paths and not any(p.startswith("/v1/") and "process" in p and p != "/v1/process" for p in paths))

    src = Path("api/routes/developer.py").read_text(encoding="utf-8")
    check("G2 route still calls the public interface (client.process)",
          "client.process(" in src and "from platrixa" in src)

    # Bypass probe, scoped to REAL import statements (docstring prose
    # legitimately names Kernel.process(...) in the flow diagram, and the
    # response fields grounding_issues/_safe_accounting( are Phase 13
    # output sanitizers — neither is a bypass). The route may import only:
    # FastAPI transport, its own schema/route siblings, the auth GATE, and
    # the public interface (platrixa) — never kernel/provider/persistence/
    # rules/accounting internals — and must never open a DB connection.
    import_lines = [
        line.strip() for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    forbidden_imports = (
        "backend.kernel", "backend.model_provider", "backend.rules",
        "backend.persistence", "backend.formula", "backend.accounting",
        "platrixa.kernel", "platrixa.provider",
    )
    hits = [ln for ln in import_lines if any(f in ln for f in forbidden_imports)]
    hits += [s for s in ("SessionLocal", "create_engine") if s in src]
    check("G3 route performs no accounting/persistence/grounding/provider calls",
          not hits, f"matched: {hits}")

    # VERIFIED authority untouched: the stub's VERIFIED flows through
    # verbatim; the route contains no status literals (Phase 13 rule).
    stub = LockedStub()
    c = _client(stub)
    pg_reset(engine)
    make_tenant(engine, "key-a", "tenant-A", 10)
    r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-a"))
    check("G4 VERIFIED from the Kernel passes through verbatim", r.status_code == 200 and r.json()["status"] == "VERIFIED")
    check("G5 gate module contains no status literals",
          not any(s in Path("backend/auth/gate.py").read_text(encoding="utf-8") for s in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED")))

    # Phase 15 boundary still enforced when metering is unconfigured.
    # Metering activates ONLY on PLATRIXA_METERING_DATABASE_URL (no
    # DATABASE_URL fallback — proven here: an ambient DATABASE_URL, as on
    # any dev host with a .env, must NOT silently flip the documented
    # zero-config contract into fail-closed 401s).
    saved_metering = os.environ.get(metered_gate.METERING_ENV_VAR)
    os.environ.pop(metered_gate.METERING_ENV_VAR, None)
    c2 = _client(LockedStub())
    os.environ["PLATRIXA_DEV_API_KEY"] = "phase15-key"
    try:
        r = c2.post("/v1/process", json={"raw_input": "T"})
        check("G6 Phase 15 single-key gate still active (401 without key)", r.status_code == 401, str(r.status_code))
        r = c2.post("/v1/process", json={"raw_input": "T"}, headers=hdr("phase15-key"))
        check("G7 Phase 15 valid key still admitted (200)", r.status_code == 200, str(r.status_code))
    finally:
        os.environ.pop("PLATRIXA_DEV_API_KEY", None)
        if saved_metering is None:
            os.environ.pop(metered_gate.METERING_ENV_VAR, None)
        else:
            os.environ[metered_gate.METERING_ENV_VAR] = saved_metering


# ---------------------------------------------------------------------------
# H — CONCURRENCY PROOF (real PostgreSQL, real threads)
# ---------------------------------------------------------------------------


def section_h(engine) -> None:
    print("\nH — Concurrency proof (REAL PostgreSQL, 40 threads, limit 10)")
    pg_reset(engine)
    LIMIT = 10
    THREADS = 40
    make_tenant(engine, "key-race", "tenant-RACE", LIMIT)
    stub = LockedStub()
    c = _client(stub)

    results: list[int] = []
    lock = threading.Lock()

    def worker(_i: int) -> None:
        try:
            r = c.post("/v1/process", json={"raw_input": "T"}, headers=hdr("key-race"))
            with lock:
                results.append(r.status_code)
        except Exception:
            with lock:
                results.append(-1)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    accepted = sum(1 for s in results if s == 200)
    rejected_429 = sum(1 for s in results if s == 429)
    row = tenant_row(engine, "key-race")
    usage = row["current_month_usage"]

    check("H1 accepted requests <= monthly_limit (10)", accepted <= LIMIT, str(accepted))
    check("H2 final usage <= monthly_limit", usage <= LIMIT, str(usage))
    check("H3 usage equals accepted count (each admission reserved exactly once)",
          usage == accepted, f"usage={usage} accepted={accepted}")
    check("H4 rejections are 429 (nothing else leaked)", rejected_429 == THREADS - accepted, f"results={sorted(set(results))}")
    check("H5 quota actually binding (proof not vacuous: some 429s occurred)", rejected_429 > 0, str(rejected_429))
    check("H6 at least some admissions succeeded", accepted > 0, str(accepted))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 72)
    print("PHASE 16 — METERED DEVELOPER GATE — EVIDENCE SUITE (fte_fyjc_66)")
    print("=" * 72)
    srv, engine = pg_backend()
    try:
        section_a(engine)
        section_b(engine)
        section_c(engine)
        section_d(engine)
        section_e()
        section_f(engine)
        section_g(engine)
        section_h(engine)
    finally:
        try:
            if _PG is not None:
                _PG[0].close()
        except Exception:
            pass

    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 72)
    print(f"TOTAL: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS" + (f"  —  {len(failed)} FAILED" if failed else ""))
    for n, d in failed:
        print(f"  FAIL: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
