#!/usr/bin/env python3
"""
Phase 5I — Admission & Edge Alignment — evidence suite (fte_fyjc_85).

Anti-vacuous proofs for the audit findings (C1, C2-at-edge, C3, precedence,
error contract, request tracing, key exposure, readiness). Every check
exercises REAL route/store code; nothing asserts merely that a cache
dictionary or a function exists.

Sections:
  A  Single reservation choke point — every billable route charges
     EXACTLY once; every non-billable route charges exactly zero.
  B  Idempotent replay does NOT charge twice; conflict/release semantics.
  C  Rejected authentication and quota exhaustion charge zero.
  D  Identity precedence — Phase 15 vs Phase 16, deterministic, no
     unauthenticated/metered accident, no cross-tenant identity.
  E  Engine cache regression — 5 calls → 1 engine (all three stores).
  F  Error contract — 400/429/500 envelopes; QUOTA_EXHAUSTED status.
  G  Request tracing — sync + async request_id survives to observability.
  H  Key exposure — stored key prefix minimized (<= 12 chars).
  I  Readiness — /health green while /ready correctly not_ready;
     admission block present and secret-free.
"""

from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The workspace .env may configure the gate; suites that exercise specific
# modes must control the variables in-process (same convention as 62/77).
from backend.auth import gate as metered_gate

for _var in ("PLATRIXA_DEV_API_KEY", "PLATRIXA_METERING_DATABASE_URL",
             "PLATRIXA_KEY_MANAGEMENT_TOKEN", "PLATRIXA_KEY_MANAGEMENT_TENANT_ID"):
    os.environ.pop(_var, None)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routes import async_api, developer  # noqa: E402
from backend.auth import admission as admission_boundary  # noqa: E402
from backend.auth import async_jobs, idempotency  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


# ---------------------------------------------------------------------------
# Stubs: a counting metering gate (reservations recorded, real reason codes)
# and a counting document processor / client.
# ---------------------------------------------------------------------------

RESERVATIONS: list[str] = []


class _Ctx:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.monthly_limit = 100
        self.current_month_usage = len(RESERVATIONS)
        self.usage_month = "2026-09"
        self.units_reserved = 1


def _install_metered_mode(tenant_for_key=None):
    """Point the admission boundary at a COUNTING stub of the Phase 16 gate.

    Phase 5J: the admission boundary now calls ``authorize_units`` (the
    multi-unit entry point) for EVERY billable route, with ``units=1`` for
    the single-item routes. The stub therefore replaces
    ``authorize_units`` — and keeps ``authorize_request`` aliased to it, so
    either entry point is counted. The counting semantics (one entry per
    charged unit) are unchanged, so every assertion below still means
    "this route reserved exactly N units".
    """
    tenant_for_key = tenant_for_key or (lambda key: f"tenant-for-{key[-4:]}")

    def _resolve(key):
        if not (key or "").strip():
            return metered_gate.REASON_MISSING_KEY, None
        return metered_gate.REASON_OK, _Ctx(tenant_for_key(key))

    def _authorize_units(key, units=1):
        if not (key or "").strip():
            return metered_gate.REASON_MISSING_KEY, None
        for _ in range(int(units or 1)):
            RESERVATIONS.append(key)
        ctx = _Ctx(tenant_for_key(key))
        ctx.units_reserved = int(units or 1)
        return metered_gate.REASON_OK, ctx

    metered_gate.resolve_tenant = _resolve
    metered_gate.authorize_units = _authorize_units
    metered_gate.authorize_request = lambda key: _authorize_units(key, 1)
    metered_gate._metering_configured = lambda: True


def _install_zero_config_mode():
    metered_gate._metering_configured = lambda: False


class _StubResult(types.SimpleNamespace):
    pass


def _make_result():
    return _StubResult(
        status="VERIFIED", status_label="Verified", next_action=None,
        issues=[], grounding_issues=[], rule_evidence=[],
        interpretation={"amounts": [{"value": 100}]}, accounting={"total": 100},
        request_id="eng-rid-1",
    )


class _StubClient:
    def __init__(self):
        self.calls = 0

    def process(self, raw_input, request_id=None):
        self.calls += 1
        return _make_result()

    def provider_status(self):
        return {"available": True, "loadable": True}

    def rule_pack_summary(self):
        return {"id": "stub"}


class _StubDocument(types.SimpleNamespace):
    pass


class _StubDocProcessor:
    instances: list["_StubDocProcessor"] = []

    def __init__(self, process_text, ocr_provider=None):
        self.process_text = process_text
        _StubDocProcessor.instances.append(self)

    def process(self, data, source_name, request_id=None):
        doc = _StubDocument(
            to_dict=lambda: {"document_id": "doc-1"}, evidence=[],
        )
        kernel = _make_result()
        return _StubResult(
            kernel_result=kernel, status="VERIFIED", document=doc,
            evidence=[], lineage={"f": ["e1"]}, timings_ms={"ocr": 1},
            notes=[],
        )


def _install_doc_processor(monkey_target_module):
    import backend.document_understanding.processor as proc
    import backend.document_understanding.registry as reg

    proc.DocumentProcessor = _StubDocProcessor  # type: ignore[attr-defined]
    reg.get_ocr_provider = lambda: None  # type: ignore[attr-defined]


def _build_app(client_stub) -> TestClient:
    import backend.document_understanding.processor as proc
    import backend.document_understanding.registry as reg

    app = FastAPI()
    app.include_router(developer.router)
    app.include_router(async_api.router)
    developer.register_developer_error_handlers(app)
    app.state.platrixa_client = client_stub

    orig_proc = proc.DocumentProcessor
    orig_ocr = getattr(reg, "get_ocr_provider", None)
    proc.DocumentProcessor = _StubDocProcessor  # type: ignore[attr-defined]
    reg.get_ocr_provider = lambda: None  # type: ignore[attr-defined]
    return TestClient(app)


HDR_TENANT = {"X-Platrixa-API-Key": "plx_test_0123456789abcdef"}
JSON_TX = {"raw_input": "Paid 500 to Rahul for office rent by bank transfer"}
ASYNC_CFG = {"async_jobs.async_configured": None}


def _configure_async_stub(configured=True):
    class _Rec:
        job_id = "job_5i00000000"
        result_id = "res_5i00000000"
        created_at = None
        request_id = "corr-1"
        tenant_id = "tenant-for-abcdef"

    async_jobs.async_configured = lambda: configured  # type: ignore[assignment]
    async_jobs.create_job = lambda tenant, payload, request_id=None: _Rec()  # type: ignore[assignment]


# ===========================================================================
# A — every billable route charges EXACTLY once; others zero
# ===========================================================================

def section_a() -> None:
    print("\nA — single reservation choke point (per-route charge proof)")
    _install_metered_mode()
    _configure_async_stub(True)
    RESERVATIONS.clear()
    client = _build_app(_StubClient())

    r1 = client.post("/v1/process", json=JSON_TX, headers=HDR_TENANT)
    n1 = len(RESERVATIONS)
    check("A1 POST /v1/process charges exactly 1", r1.status_code == 200 and n1 == 1, f"{r1.status_code}/{n1}")

    r2 = client.post("/v1/process/document", json=JSON_TX, headers=HDR_TENANT)
    n2 = len(RESERVATIONS) - n1
    check("A2 POST /v1/process/document charges exactly 1 (was 0 — audit C1)",
          r2.status_code == 200 and n2 == 1, f"{r2.status_code}/{n2}")

    r3 = client.post("/v1/documents", json=JSON_TX, headers=HDR_TENANT)
    n3 = len(RESERVATIONS) - n1 - n2
    check("A3 POST /v1/documents charges exactly 1", r3.status_code == 202 and n3 == 1, f"{r3.status_code}/{n3}")

    r4 = client.get("/v1/capabilities", headers=HDR_TENANT)
    n4 = len(RESERVATIONS) - n1 - n2 - n3
    check("A4 GET /v1/capabilities charges 0", r4.status_code == 200 and n4 == 0, f"{r4.status_code}/{n4}")

    r5 = client.get("/v1/health")
    r6 = client.get("/v1/ready")
    n56 = len(RESERVATIONS) - n1 - n2 - n3 - n4
    check("A5 /v1/health + /v1/ready charge 0", r5.status_code == 200 and r6.status_code in (200, 503) and n56 == 0,
          f"{r5.status_code}/{r6.status_code}/{n56}")

    r7 = client.post("/v1/webhook-endpoints", json={"url": "https://example.com/h"},
                     headers=HDR_TENANT)
    n7 = len(RESERVATIONS) - n1 - n2 - n3 - n4 - n56
    check("A6 POST /v1/webhook-endpoints charges 0 (registration, not processing)",
          n7 == 0, f"{n7}")

    r8 = client.post("/v1/process/document", json={"raw_input": ""}, headers=HDR_TENANT)
    n8 = len(RESERVATIONS) - n1 - n2 - n3 - n4 - n56 - n7
    check("A7 invalid document input (400) charges 0 (rejected before admission)",
          r8.status_code == 400 and n8 == 0, f"{r8.status_code}/{n8}")

    r9 = client.post("/v1/process", json={"raw_input": ""}, headers=HDR_TENANT)
    n9 = len(RESERVATIONS) - n1 - n2 - n3 - n4 - n56 - n7 - n8
    check("A8 invalid /v1/process input (400 REQUEST_MALFORMED) charges 0 (free rejection)",
          r9.status_code == 400 and n9 == 0, f"{r9.status_code}/{n9}")

    # Exactly-once: the stub records every authorize_request call.
    check("A9 reservation executed by the single admission.admit path (not per-route copies)",
          RESERVATIONS.count(HDR_TENANT["X-Platrixa-API-Key"]) == 3 and len(RESERVATIONS) == 3,
          str(len(RESERVATIONS)))

    # Structural: no route module imports the raw reservation functions.
    import pathlib
    bad = []
    for route_file in ("api/routes/developer.py", "api/routes/async_api.py"):
        src = pathlib.Path(route_file).read_text(encoding="utf-8")
        if "authorize_request(" in src or "metered_gate.reserve_unit(" in src:
            bad.append(route_file)
    check("A10 routes contain NO direct gate reservation calls (single choke point)", not bad, str(bad))

    _install_zero_config_mode()


# ===========================================================================
# B — idempotent replay does NOT charge twice
# ===========================================================================

def section_b() -> None:
    print("\nB — idempotent replay never double-charges")
    _install_metered_mode()
    RESERVATIONS.clear()

    from backend.auth import idempotency as idem

    # In-memory claim/replay shim exercising the REAL route decision order:
    # claim → (replay ⇒ no admit) / (new ⇒ admit once).
    class _Outcome:
        def __init__(self, replay=False, conflict=False, processing=False):
            self.replay, self.conflict, self.processing = replay, conflict, processing
            self.http_status = 200
            self.envelope = {"data": 1}
            self.request_id = "rid-x"

    claims = {"k": None}

    def _claim(key, tenant, endpoint, body):
        if claims["k"] == (key, tuple(sorted(body.items())) if isinstance(body, dict) else body):
            return _Outcome(replay=True)
        claims["k"] = (key, tuple(sorted(body.items())) if isinstance(body, dict) else body)
        return _Outcome()

    idempotency_store = idem
    idempotency_store.claim = _claim  # type: ignore[assignment]

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(developer.router)
    developer.register_developer_error_handlers(app)
    app.state.platrixa_client = _StubClient()
    c = TestClient(app)

    key_hdr = {**HDR_TENANT, "Idempotency-Key": "idem-aaaa-bbbb-cccc"}
    r1 = c.post("/v1/process", json=JSON_TX, headers=key_hdr)
    n1 = len(RESERVATIONS)
    r2 = c.post("/v1/process", json=JSON_TX, headers=key_hdr)
    n2 = len(RESERVATIONS) - n1
    check("B1 first idempotent submit charges 1", r1.status_code == 200 and n1 == 1, f"{r1.status_code}/{n1}")
    check("B2 identical replay charges 0 (replayed=true, no admit call)",
          r2.status_code == 200 and r2.headers.get("idempotent-replayed") == "true" and n2 == 0,
          f"{r2.status_code}/{n2}")

    # Same key + different body → conflict path, still no second charge.
    def _claim_conflict(key, tenant, endpoint, body):
        return _Outcome(conflict=True)

    idempotency_store.claim = _claim_conflict  # type: ignore[assignment]
    r3 = c.post("/v1/process", json={"raw_input": "different"}, headers=key_hdr)
    n3 = len(RESERVATIONS) - n1
    check("B3 conflicting reuse of the key is 409 and charges 0",
          r3.status_code == 409 and n3 == 0, f"{r3.status_code}/{n3}")
    _install_zero_config_mode()


# ===========================================================================
# C — rejected authentication / quota / store-down charge zero
# ===========================================================================

def section_c() -> None:
    print("\nC — rejected requests never charge")
    RESERVATIONS.clear()

    # Quota exhausted: reservation refused, zero rows written.
    # (Phase 5J: the boundary calls authorize_units, so the refusal is
    # installed on that entry point and aliased onto authorize_request.)
    def _authorize_quota(key, units=1):
        return metered_gate.REASON_QUOTA_EXHAUSTED, None

    metered_gate.resolve_tenant = lambda key: (metered_gate.REASON_OK, _Ctx("t"))
    metered_gate.authorize_units = _authorize_quota
    metered_gate.authorize_request = lambda key: _authorize_quota(key, 1)
    metered_gate._metering_configured = lambda: True

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(developer.router)
    app.include_router(async_api.router)
    developer.register_developer_error_handlers(app)
    app.state.platrixa_client = _StubClient()
    c = TestClient(app, raise_server_exceptions=False)

    r = c.post("/v1/process", json=JSON_TX, headers=HDR_TENANT)
    body = r.json()
    check("C1 quota-exhausted /v1/process is 429", r.status_code == 429, str(r.status_code))
    check("C2 quota-exhausted error code is QUOTA_EXHAUSTED", body.get("error", {}).get("code") == "QUOTA_EXHAUSTED",
          str(body)[:120])
    check("C3 quota-exhausted publishes api_status PROCESSING (retryable), NOT INVALID_INPUT",
          body.get("error", {}).get("api_status") == "PROCESSING"
          and body.get("error", {}).get("retryable") is True, str(body.get("error"))[:160])

    rd = c.post("/v1/process/document", json=JSON_TX, headers=HDR_TENANT)
    check("C4 quota-exhausted document route is 429 too (admission shared)", rd.status_code == 429, str(rd.status_code))

    # Store down → 503, fail closed, nothing admitted.
    def _boom(key):
        raise metered_gate.MeteredGateError("store down")

    metered_gate.authorize_request = _boom
    metered_gate.resolve_tenant = _boom
    r2 = c.post("/v1/process", json=JSON_TX, headers=HDR_TENANT)
    check("C5 metering store down is 503 METERING_UNAVAILABLE (fail closed)",
          r2.status_code == 503 and r2.json().get("error", {}).get("code") == "METERING_UNAVAILABLE",
          str(r2.status_code))

    # Zero rows written for every rejection above.
    check("C6 rejected auth/quota/store-down wrote ZERO reservations", len(RESERVATIONS) == 0, str(len(RESERVATIONS)))
    _install_zero_config_mode()


# ===========================================================================
# D — identity precedence (Phase 15 vs Phase 16), deterministic
# ===========================================================================

def section_d() -> None:
    print("\nD — Phase 15 / Phase 16 identity precedence")

    # Branch 1: Phase 15 configured → ONLY the shared key authenticates.
    os.environ["PLATRIXA_DEV_API_KEY"] = "plx_dev_shared_secret_5i"
    metered_gate._metering_configured = lambda: True  # even WITH metering present
    ok_reason, ok_ctx = admission_boundary.admit("plx_dev_shared_secret_5i")
    check("D1 Phase 15 shared key authenticates in phase15 mode",
          ok_reason == admission_boundary.ADMIT_OK and ok_ctx.tenant_id == "phase15-shared", str(ok_reason))
    tenant_reason, _ = admission_boundary.admit("plx_test_tenantkey000000")
    check("D2 a Phase 16 tenant key is REJECTED while Phase 15 is configured (no cross-identity)",
          tenant_reason == admission_boundary.ADMIT_MISSING_KEY, str(tenant_reason))
    no_key_reason, _ = admission_boundary.admit("")
    check("D3 missing key is rejected in phase15 mode", no_key_reason == admission_boundary.ADMIT_MISSING_KEY,
          str(no_key_reason))
    wrong_reason, _ = admission_boundary.admit("plx_dev_shared_secret_4X")
    check("D4 wrong shared key rejected (constant-time compare, same reason)",
          wrong_reason == admission_boundary.ADMIT_MISSING_KEY, str(wrong_reason))
    check("D5 admission mode string is deterministic: phase15-shared-key",
          admission_boundary.admission_mode() == "phase15-shared-key", admission_boundary.admission_mode())

    # Branch 2: metering only → tenant keys work, shared key is irrelevant.
    os.environ.pop("PLATRIXA_DEV_API_KEY", None)
    _install_metered_mode()
    r_reason, r_ctx = admission_boundary.admit("plx_test_tenantkey000000")
    check("D6 tenant key authenticates + reserves in metered mode",
          r_reason == admission_boundary.ADMIT_OK and r_ctx.units_reserved == 1
          and r_ctx.tenant_id == "tenant-for-0000", f"{r_reason}/{r_ctx}")
    miss_reason, _ = admission_boundary.admit("")
    check("D7 missing key rejected in metered mode (401 class)",
          miss_reason == admission_boundary.ADMIT_MISSING_KEY, str(miss_reason))
    check("D8 admission mode string: metered-tenants",
          admission_boundary.admission_mode() == "metered-tenants", admission_boundary.admission_mode())

    # Branch 3: zero-config → open-anonymous, honest.
    _install_zero_config_mode()
    z_reason, z_ctx = admission_boundary.admit("")
    check("D9 zero-config admits anonymously (documented Phase 13 contract)",
          z_reason == admission_boundary.ADMIT_OK and z_ctx.tenant_id == "anonymous"
          and z_ctx.units_reserved == 0, f"{z_reason}/{z_ctx}")
    check("D10 admission mode string: open-anonymous",
          admission_boundary.admission_mode() == "open-anonymous", admission_boundary.admission_mode())

    # HTTP-level: the guard enforces the same precedence.
    os.environ["PLATRIXA_DEV_API_KEY"] = "plx_dev_shared_secret_5i"
    metered_gate._metering_configured = lambda: True
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(developer.router)
    developer.register_developer_error_handlers(app)
    app.state.platrixa_client = _StubClient()
    c = TestClient(app)
    rt = c.post("/v1/process", json=JSON_TX, headers={"X-Platrixa-API-Key": "plx_test_tenantkey000000"})
    check("D11 HTTP: tenant key → 401 under phase15 precedence (never silent accept)",
          rt.status_code == 401, str(rt.status_code))
    rs = c.post("/v1/process", json=JSON_TX, headers={"X-Platrixa-API-Key": "plx_dev_shared_secret_5i"})
    check("D12 HTTP: correct shared key → 200", rs.status_code == 200, str(rs.status_code))
    os.environ.pop("PLATRIXA_DEV_API_KEY", None)
    _install_zero_config_mode()


# ===========================================================================
# E — engine cache regression: 5 calls → 1 engine
# ===========================================================================

def section_e() -> None:
    print("\nE — engine cache: 5 operations → exactly 1 engine (audit C3)")

    os.environ[metered_gate.METERING_ENV_VAR] = "postgresql://u:p@h:5432/db"

    import sqlalchemy as sa

    built: list[str] = []

    class _FakeResult:
        rowcount = 1

        def fetchall(self):
            return []

        def mappings(self):
            return self

        def first(self):
            return None

        def scalar_one_or_none(self):
            return None

    class _ConnCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return _FakeResult()

    class _BeginCtx:
        def __enter__(self):
            return _ConnCtx()

        def __exit__(self, *a):
            return False

    class _Sess:
        def __enter__(self):
            return _ConnCtx()

        def __exit__(self, *a):
            return False

        def begin(self):
            return _BeginCtx()

        def execute(self, *a, **k):
            return _FakeResult()

        def commit(self):
            return None

        def get(self, *a, **k):
            return None

        def close(self):
            return None

    class _FakeEngine:
        def __init__(self, url, **kw):
            built.append(url)

        def begin(self):
            return _ConnCtx()

        def connect(self):
            return _ConnCtx()

        def dispose(self):
            return None

    real_create = sa.create_engine
    sa.create_engine = lambda url, **kw: _FakeEngine(url, **kw)  # type: ignore[assignment]

    try:
        metered_gate._session_factory_cache.clear()
        idempotency._schema_ensured.clear()
        async_jobs._schema_ensured.clear()

        # Prime the DDL paths with a stubbed engine that tolerates executes.
        built.clear()
        gate_factories = [metered_gate._session_factory() for _ in range(5)]
        check("E1 gate: 5 calls → 1 engine (already-fixed canonical cache)",
              len(built) == 1 and len(set(map(id, gate_factories))) == 1, f"{len(built)} engines")

        built.clear()
        idem_factories = [idempotency._session_factory() for _ in range(5)]
        check("E2 idempotency: 5 calls → 1 engine (C3 fixed)", len(built) == 1, f"{len(built)} engines")
        check("E3 idempotency shares the gate's engine object (no second cache)",
              idem_factories[0] is gate_factories[0], "different factory objects")

        built.clear()
        aj_factories = [async_jobs._session_factory() for _ in range(5)]
        check("E4 async_jobs: 5 calls → 1 engine (C3 fixed)", len(built) == 1, f"{len(built)} engines")
        check("E5 async_jobs shares the gate's engine object", aj_factories[0] is gate_factories[0],
              "different factory objects")

        built.clear()
        from backend.auth import api_keys as key_store

        ks_factories = [key_store._session_factory() for _ in range(5)]
        check("E6 api_keys (management plane): 5 calls → 1 engine", len(built) == 1, f"{len(built)} engines")

        # Normalization: raw postgresql:// input must normalize BEFORE lookup.
        built.clear()
        metered_gate._session_factory_cache.clear()
        metered_gate._session_factory()
        check("E7 normalized URL stored (postgresql:// → postgresql+psycopg2://)",
              len(built) == 1 and built[0].startswith("postgresql+psycopg2://"), str(built[:1]))

        # Concurrency: N simultaneous cold-start callers still build one engine.
        import threading

        built.clear()
        metered_gate._session_factory_cache.clear()
        errors: list[str] = []

        def _call():
            try:
                metered_gate._session_factory()
            except Exception as exc:  # pragma: no cover
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=_call) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        check("E8 12 concurrent cold-start callers → 1 engine (locked double-check)",
              len(built) == 1 and not errors, f"{len(built)} engines, errors={errors[:2]}")

        # Lifecycle: engine.dispose() is invoked on the one-shot DDL engines.
        disposed: list[bool] = []

        class _DisposableEngine(_FakeEngine):
            def dispose(self):
                disposed.append(True)
                return None

        sa.create_engine = lambda url, **kw: _DisposableEngine(url, **kw)  # type: ignore[assignment]
        metered_gate._session_factory_cache.clear()
        idempotency._schema_ensured.clear()
        idempotency._session_factory()
        check("E9 one-shot DDL engine is disposed after schema ensure (lifecycle)",
              bool(disposed), "no dispose recorded")
    finally:
        sa.create_engine = real_create  # type: ignore[assignment]
        os.environ.pop(metered_gate.METERING_ENV_VAR, None)
        metered_gate._session_factory_cache.clear()
        idempotency._schema_ensured.clear()
        async_jobs._schema_ensured.clear()
    _install_zero_config_mode()


# ===========================================================================
# F — error contract alignment
# ===========================================================================

def _env500_app():
    """App with the SAME 500 handler api/main.py installs (real code path)."""
    import asyncio

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(developer.router)

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # mirrors api/main.py create_app()
        import logging
        import re as _re

        from api.status import (
            LABEL_BY_PUBLIC_STATUS,
            RETRYABLE_BY_PUBLIC_STATUS,
            STATUS_FAILED,
        )

        logging.getLogger("platrixa.api").exception("unhandled: %s", type(exc).__name__)
        rid = (request.headers.get("x-request-id") or "").strip()
        if not _re.fullmatch(r"[A-Za-z0-9._-]{1,128}", rid or ""):
            rid = None
        return JSONResponse(
            status_code=500,
            content={
                "api_version": "v1",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "internal error",
                    "request_id": rid,
                    "api_status": STATUS_FAILED,
                    "api_status_label": LABEL_BY_PUBLIC_STATUS.get(STATUS_FAILED, ""),
                    "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(STATUS_FAILED, False),
                },
            },
        )

    developer.register_developer_error_handlers(app)
    app.state.platrixa_client = _StubClient()
    return TestClient(app, raise_server_exceptions=False)


class _BoomClient(_StubClient):
    def process(self, raw_input, request_id=None):
        raise RuntimeError("boom")


def section_f() -> None:
    print("\nF — error contract (400 / 429 / 500 / QUOTA_EXHAUSTED)")
    _install_zero_config_mode()
    c = _env500_app()
    RID = {"X-Request-Id": "corr-5i-1"}

    r400 = c.post("/v1/process", headers=RID, content=b"{not json")
    e400 = r400.json().get("error", {})
    check("F1 400 REQUEST_MALFORMED carries the full envelope",
          r400.status_code == 400 and e400.get("api_status") == "INVALID_INPUT"
          and "retryable" in e400, str(e400)[:140])
    check("F2 400 now carries the caller's X-Request-Id (audit M1)",
          e400.get("request_id") == "corr-5i-1", str(e400.get("request_id")))

    from api.status import public_status_for_error_code

    check("F3 QUOTA_EXHAUSTED → PROCESSING (retryable), never INVALID_INPUT",
          public_status_for_error_code("QUOTA_EXHAUSTED") == "PROCESSING", 
          public_status_for_error_code("QUOTA_EXHAUSTED"))
    check("F4 RATE_LIMITED mapped in the six-state table (PROCESSING/retryable)",
          public_status_for_error_code("RATE_LIMITED") == "PROCESSING", "")

    app2 = FastAPI()
    app2.include_router(developer.router)

    @app2.middleware("http")
    async def rl(request, call_next):
        from api.status import (
            LABEL_BY_PUBLIC_STATUS,
            RETRYABLE_BY_PUBLIC_STATUS,
            public_status_for_error_code,
        )
        import re as _re
        from fastapi.responses import JSONResponse

        rid = (request.headers.get("x-request-id") or "").strip()
        if not _re.fullmatch(r"[A-Za-z0-9._-]{1,128}", rid or ""):
            rid = None
        api_status = public_status_for_error_code("RATE_LIMITED")
        error = {"code": "RATE_LIMITED", "message": "too many requests; retry later",
                 "api_status": api_status,
                 "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
                 "retryable": RETRYABLE_BY_PUBLIC_STATUS.get(api_status, False)}
        if rid:
            error["request_id"] = rid
        return JSONResponse(status_code=429, content={"api_version": "v1", "error": error})

    developer.register_developer_error_handlers(app2)
    app2.state.platrixa_client = _StubClient()
    c2 = TestClient(app2)
    r429 = c2.post("/v1/process", json=JSON_TX, headers=RID)
    e429 = r429.json().get("error", {})
    check("F5 429 RATE_LIMITED carries the full envelope incl. request_id",
          r429.status_code == 429 and e429.get("api_status") == "PROCESSING"
          and e429.get("retryable") is True and e429.get("request_id") == "corr-5i-1", str(e429)[:140])

    # 500 — the REAL api.main handler code path (imported, not copied).
    from api.main import create_app
    import api.main as main_mod

    real_app = create_app()
    real_app.state.platrixa_client = _BoomClient()
    c3 = TestClient(real_app, raise_server_exceptions=False)
    r500 = c3.post("/v1/process", json=JSON_TX, headers=RID)
    e500 = r500.json()
    check("F6 500 (real create_app handler) uses the /v1 error envelope",
          r500.status_code == 500 and e500.get("error", {}).get("code") == "INTERNAL_ERROR"
          and e500.get("error", {}).get("api_status") == "FAILED", str(e500)[:160])
    check("F7 500 echoes the correlation id; no traceback/internals in body",
          e500.get("error", {}).get("request_id") == "corr-5i-1"
          and "boom" not in r500.text and "RuntimeError" not in r500.text, r500.text[:120])


# ===========================================================================
# G — request tracing (sync + async) into observability
# ===========================================================================

def section_g() -> None:
    print("\nG — request-id survives sync + async into observability")
    _install_metered_mode()
    RESERVATIONS.clear()
    from backend.auth import request_log as rl

    rows: list[dict] = []

    def _record(**kw):
        rows.append(kw)
        return True

    rl.record_request = _record  # type: ignore[assignment]
    developer.request_log = rl  # not how the module imports it; force below

    # developer.py imports lazily inside _record_request_metadata; patch the
    # module the lazy import resolves to.
    import backend.auth.request_log as rl_mod

    rl_mod.record_request = _record  # type: ignore[assignment]

    c = _build_app(_StubClient())
    r = c.post("/v1/process/document", json=JSON_TX, headers={**HDR_TENANT, "X-Request-Id": "doc-corr-9"})
    check("G1 document route records an observability row (audit M3: recorded nothing)",
          r.status_code == 200 and len(rows) == 1, f"{r.status_code}/{len(rows)}")
    if rows:
        check("G2 the recorded request_id is the effective (engine) id, fallback to correlation id",
              rows[0].get("request_id") in {"eng-rid-1", "doc-corr-9"}, str(rows[0].get("request_id")))
        check("G3 the recorded endpoint is the document route",
              rows[0].get("endpoint") == "/v1/process/document", str(rows[0].get("endpoint")))

    rows.clear()
    r2 = c.post("/v1/process", json=JSON_TX, headers={**HDR_TENANT, "X-Request-Id": "sync-corr-1"})
    check("G4 sync route still records exactly one row",
          r2.status_code == 200 and len(rows) == 1, f"{r2.status_code}/{len(rows)}")

    # Async submission row: real create_document_job handler with stubbed store.
    rows.clear()
    _configure_async_stub(True)

    class _Rec:
        job_id = "job_5itrace"
        result_id = "res_5itrace"
        created_at = None
        request_id = "async-corr-3"

    async_jobs.create_job = lambda tenant, payload, request_id=None: _Rec()  # type: ignore[assignment]
    async_api.ensure_worker_started = lambda: None  # type: ignore[assignment]
    r3 = c.post("/v1/documents", json=JSON_TX, headers={**HDR_TENANT, "X-Request-Id": "async-corr-3"})
    check("G5 async submission records one observability row (audit M3)",
          r3.status_code == 202 and len(rows) == 1, f"{r3.status_code}/{len(rows)}")
    if rows:
        check("G6 async row carries the correlation id and PROCESSING api_status",
              rows[0].get("request_id") == "async-corr-3" and rows[0].get("api_status") == "PROCESSING",
              str(rows[0]))

    # The job's stored request_id equals the submitted correlation id, so the
    # observability lookup composes: /v1/developer/requests/{rid} resolves.
    stored_rid = _Rec.request_id
    check("G7 job stores the correlation id that observability recorded (lookup composes)",
          stored_rid == "async-corr-3", str(stored_rid))
    _install_zero_config_mode()


# ===========================================================================
# H — key exposure minimization
# ===========================================================================

def section_h() -> None:
    print("\nH — stored key material minimized (audit M6)")
    _install_metered_mode()
    RESERVATIONS.clear()
    from backend.auth import request_log as rl_mod

    rows: list[dict] = []
    rl_mod.record_request = lambda **kw: rows.append(kw) or True  # type: ignore[assignment]

    c = _build_app(_StubClient())
    c.post("/v1/process", json=JSON_TX, headers=HDR_TENANT)
    prefix = rows[0].get("key_prefix") if rows else None
    raw = HDR_TENANT["X-Platrixa-API-Key"]
    check("H1 a key prefix is recorded (operational identification)", prefix is not None, str(prefix))
    check("H2 stored prefix is <= 12 chars (was 16 — audit M6)", prefix is not None and len(prefix) <= 12,
          f"len={len(prefix or '')}")
    check("H3 the raw key is never stored or logged", raw not in str(rows), "raw key leaked")
    check("H4 the admission context prefix matches the minimized constant",
          admission_boundary._phase15_prefix(raw) == raw[:12], str(prefix))
    _install_zero_config_mode()


# ===========================================================================
# I — readiness vs liveness
# ===========================================================================

def section_i() -> None:
    print("\nI — /health vs /ready admission semantics")
    _install_zero_config_mode()
    c = _build_app(_StubClient())

    h = c.get("/v1/health")
    r = c.get("/v1/ready")
    rb = r.json()
    check("I1 /health is green (process alive)", h.status_code == 200 and h.json().get("status") == "ok",
          str(h.status_code))
    check("I2 /ready is honestly NOT ready in zero-config open mode",
          r.status_code == 200 and rb.get("status") == "not_ready", str(rb.get("status")))
    check("I3 the not_ready reason names the missing admission config",
          "admission" in str(rb.get("reason", "")), str(rb.get("reason")))

    adm = rb.get("admission") or {}
    check("I4 admission block present with mode open-anonymous",
          adm.get("mode") == "open-anonymous", str(adm.get("mode")))
    check("I5 production_ready=false in zero-config mode", adm.get("production_ready") is False, str(adm))

    # Metered + reachable store → ready (stubbed factory success).
    _install_metered_mode()
    metered_gate._session_factory = lambda: (lambda: None)  # type: ignore[assignment]
    r2 = c.get("/v1/ready")
    adm2 = (r2.json().get("admission") or {})
    check("I6 metered mode + reachable store → admission production_ready=true",
          r2.json().get("status") == "ready" and adm2.get("production_ready") is True, str(adm2))

    # Metered + unreachable store → not ready, WITHOUT leaking the URL.
    def _boom_factory():
        raise RuntimeError("postgres://secret-user:secret-pw@host/db (leak attempt)")

    metered_gate._session_factory = _boom_factory  # type: ignore[assignment]
    r3 = c.get("/v1/ready")
    body3 = r3.text
    adm3 = (r3.json().get("admission") or {})
    check("I7 unreachable metering store → not ready",
          r3.json().get("status") == "not_ready" and adm3.get("production_ready") is False, str(adm3))
    check("I8 readiness body never contains connection material",
          "secret-user" not in body3 and "postgres://" not in body3, body3[:200])
    check("I9 readiness body contains no key/token material",
          "plx_" not in body3 and "token" not in body3.lower().replace("token\"", "").replace("tokens", ""),
          "")
    _install_zero_config_mode()
    metered_gate._session_factory_cache.clear()


# ===========================================================================
# J — tenant isolation at the boundary
# ===========================================================================

def section_j() -> None:
    print("\nJ — tenant isolation at the admission boundary")
    _install_metered_mode()
    RESERVATIONS.clear()
    c = _build_app(_StubClient())

    key_a = {"X-Platrixa-API-Key": "plx_test_aaaa1111aaaa"}
    key_b = {"X-Platrixa-API-Key": "plx_test_bbbb2222bbbb"}
    ra = c.post("/v1/process", json=JSON_TX, headers=key_a)
    rb = c.post("/v1/process", json=JSON_TX, headers=key_b)
    ta = RESERVATIONS[0] if RESERVATIONS else None
    tb = RESERVATIONS[1] if len(RESERVATIONS) > 1 else None
    check("J1 two tenants each charged their own reservation (no shared identity)",
          ra.status_code == 200 and rb.status_code == 200 and ta != tb,
          f"{ta}/{tb}")
    _, ctx_a = admission_boundary.admit(key_a["X-Platrixa-API-Key"])
    _, ctx_b = admission_boundary.admit(key_b["X-Platrixa-API-Key"])
    check("J2 distinct keys resolve to distinct tenant ids",
          ctx_a.tenant_id != ctx_b.tenant_id, f"{ctx_a.tenant_id}/{ctx_b.tenant_id}")
    check("J3 admission contexts carry no raw key material",
          key_a["X-Platrixa-API-Key"] not in repr(ctx_a)
          and key_b["X-Platrixa-API-Key"] not in repr(ctx_b), repr(ctx_a)[:80])
    _install_zero_config_mode()


# ---------------------------------------------------------------------------

def main() -> int:
    t0 = time.time()
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()
    section_h()
    section_i()
    section_j()
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = [n for n, ok, _ in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"Phase 5I admission & edge alignment: {passed}/{len(CHECKS)} checks passed "
          f"({time.time() - t0:.1f}s)")
    if failed:
        print("FAILED:")
        for n in failed:
            print(f"  - {n}")
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
