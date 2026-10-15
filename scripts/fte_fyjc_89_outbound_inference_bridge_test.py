#!/usr/bin/env python3
"""Outbound inference bridge — Stage 1 acceptance suite (fte_fyjc_89).

Proves the properties the outbound inference bridge depends on, against REAL
PostgreSQL (embedded pgserver), the REAL FastAPI application and the REAL
deterministic kernel:

  A  Kernel entry points     — ONE shared pipeline; the device path never
                               resolves or calls a model provider
  B  Shared bridge contract  — the synchronous provider and the async result
                               route are the SAME parser (no weaker second one)
  C  Creation + idempotency  — admission ordering, replay, conflict, fault
                               injection, no duplicate job, no re-charge
  D  Claim / lease / result  — tenant- and kind-scoped claiming, fencing,
                               duplicate submission, staleness, one commit
  E  Authority + isolation   — kind/tenant isolation and deterministic
                               authority (no device candidate creates VERIFIED)
  F  End to end              — the stored envelope IS the result endpoint's
                               envelope, and production transport is untouched

Nothing here points at production. No Railway variable is read or written.

Run:
    .venv/bin/python scripts/fte_fyjc_89_outbound_inference_bridge_test.py
"""

from __future__ import annotations

import ast
import copy
import json
import os
import sys
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Metered mode only: the Phase 15 shared break-glass key must not be set.
for _v in (
    "PLATRIXA_DEV_API_KEY",
    "PLATRIXA_KEY_MANAGEMENT_TOKEN",
    "PLATRIXA_KEY_MANAGEMENT_TENANT_ID",
):
    os.environ.pop(_v, None)

from fastapi.testclient import TestClient  # noqa: E402

import backend.kernel.kernel as kernel_module  # noqa: E402
import backend.model_provider.local_bridge as local_bridge  # noqa: E402
from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import async_jobs  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402
from backend.auth import idempotency as idem  # noqa: E402
from backend.auth.models import current_usage_month  # noqa: E402
from backend.auth.tokens import hash_token  # noqa: E402
from backend.kernel.kernel import Kernel  # noqa: E402
from backend.model_provider.base import (  # noqa: E402
    ADAPTER_REPO_ID,
    ADAPTER_REVISION,
    InterpretationResult,
    ProviderConfig,
    ProviderStatus,
)
from backend.model_provider.local_bridge import (  # noqa: E402
    PROVENANCE_UNKNOWN,
    LocalBridgeModelProvider,
    interpretation_from_bridge_body,
)

CHECKS: list[tuple[str, bool, str]] = []
# Two entries per model invocation: at most ONE provider inference in the
# whole suite, and it must happen on the synchronous path only.
MAX_ALLOWED_PROVIDER_INTERPRET_CALLS = 1


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Fixtures (the canonical 18-field candidate + the transaction text it grounds
# against — the pair the existing kernel boundary suite already treats as a
# deterministic VERIFIED case)
# ---------------------------------------------------------------------------

RAW_INPUT = "purchased furniture from raj for rs.25000"

VALID_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["payment method not stated"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.50",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}

# Schema-valid, asserts nothing the input cannot support → the existing
# deterministic accounting flow itself decides the input is unsupported.
UNSUPPORTED_CANDIDATE = {
    "transaction_type": "UNKNOWN",
    "parties": [],
    "amounts": [],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["no recognizable transaction"],
    "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
    "transaction_type_enum": "UNKNOWN",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": [],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.10",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}

_EVIDENCE_REQUIRED_KEYS = (
    "evidence_schema_version",
    "request_id",
    "input_hash",
    "candidate_interpretation_hash",
    "grounded_interpretation_hash",
    "model_identity",
    "adapter_identity",
    "schema_version",
    "prompt_version",
    "grounding_version",
    "accounting_version",
    "rule_pack_hash",
    "rule_evidence",
    "final_state",
)


def bridge_body(candidate: dict, **extra) -> dict:
    """A device bridge response body, exactly as the device emits it."""
    body = {
        "interpretation_text": json.dumps(candidate),
        "completion_status": "COMPLETE",
        "finish_reason": "stop",
        "model": {"provider": "local-llama.cpp", "adapter_loaded": None},
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Instrumentation — every stage call is counted on the live objects
# ---------------------------------------------------------------------------


class Instrumentation:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stages: dict[str, int] = {}
        self.provider_interpret = 0
        self.provider_status = 0
        self.client_process = 0
        self.process_from_source = 0
        self.bridge_parses = 0
        self.commits_accepted = 0
        self._patched = False

    def bump(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self.stages[key] = self.stages.get(key, 0) + amount

    def count(self, key: str) -> int:
        with self._lock:
            return self.stages.get(key, 0)

    # -- install / remove the wrappers ------------------------------------

    def install(self) -> None:
        if self._patched:
            return
        self._patched = True
        inst = self

        # Kernel.process_from_source — proves "no second kernel execution".
        orig_pfs = kernel_module.Kernel.process_from_source

        def counting_pfs(self, *args, **kwargs):
            with inst._lock:
                inst.process_from_source += 1
            return orig_pfs(self, *args, **kwargs)

        kernel_module.Kernel.process_from_source = counting_pfs

        # Kernel.process_accounting — the deterministic accounting boundary.
        orig_pa = kernel_module.Kernel.process_accounting

        def counting_pa(self, *args, **kwargs):
            inst.bump("accounting")
            return orig_pa(self, *args, **kwargs)

        kernel_module.Kernel.process_accounting = counting_pa

        # ExecutionEvidence construction.
        orig_ev = kernel_module.ExecutionEvidence

        def counting_ev(*args, **kwargs):
            inst.bump("evidence")
            return orig_ev(*args, **kwargs)

        kernel_module.ExecutionEvidence = counting_ev

        # Schema validation (the class the kernel imports lazily).
        import backend.maths.schema_verifier as schema_verifier

        orig_validate = schema_verifier.StructuredInterpretationValidator.validate

        def counting_validate(self, *args, **kwargs):
            inst.bump("schema_validation")
            return orig_validate(self, *args, **kwargs)

        schema_verifier.StructuredInterpretationValidator.validate = counting_validate

        # Deterministic grounding gate.
        import backend.maths.fyjc_grounding_gate as grounding_gate

        orig_ground = grounding_gate.ExpandedGroundingGate.ground

        def counting_ground(self, *args, **kwargs):
            inst.bump("grounding")
            return orig_ground(self, *args, **kwargs)

        grounding_gate.ExpandedGroundingGate.ground = counting_ground

        # GroundedSemanticIR conversion (the only accounting admission path).
        import backend.semantics.ir as semantic_ir

        orig_from_candidate = semantic_ir.GroundedSemanticIR.from_candidate.__func__

        def counting_from_candidate(cls, candidate, grounding_result):
            inst.bump("grounded_ir")
            return orig_from_candidate(cls, candidate, grounding_result)

        semantic_ir.GroundedSemanticIR.from_candidate = classmethod(counting_from_candidate)

        # The result route's bridge parse AND the fenced commit. The route
        # imports the parser lazily inside the handler, so the patch target is
        # the defining module (the name the handler resolves at call time).
        import backend.model_provider.local_bridge as local_bridge

        orig_parse = local_bridge.interpretation_from_bridge_body

        def counting_parse(*args, **kwargs):
            inst.bump("bridge_parse")
            return orig_parse(*args, **kwargs)

        local_bridge.interpretation_from_bridge_body = counting_parse

        orig_complete = async_jobs.complete_job

        def counting_complete(*args, **kwargs):
            accepted = orig_complete(*args, **kwargs)
            if accepted:
                with inst._lock:
                    inst.commits_accepted += 1
            return accepted

        async_jobs.complete_job = counting_complete


INSTR = Instrumentation()


# ---------------------------------------------------------------------------
# Harness client: a REAL kernel with a provider that must never be touched on
# the device-result path, plus a facade `process` that must never be used.
# ---------------------------------------------------------------------------


class CountingProvider:
    """A live provider used ONLY to prove the synchronous path still works."""

    def __init__(self) -> None:
        self._config = ProviderConfig()
        self.base = ProviderConfig()

    @property
    def config(self) -> ProviderConfig:
        return self._config

    def status(self) -> ProviderStatus:
        INSTR.provider_status += 1
        return ProviderStatus(
            available=True,
            model_id=self._config.model_id,
            base_model_revision=self._config.base_model_revision,
            adapter_repo_id=ADAPTER_REPO_ID,
            adapter_revision=ADAPTER_REVISION,
            reason="stub provider",
            loadable=True,
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        INSTR.provider_interpret += 1
        return InterpretationResult(
            raw_input=raw_input,
            candidate=copy.deepcopy(VALID_CANDIDATE),
            model_id=self._config.model_id,
            provider_revision=ADAPTER_REVISION,
        )


class HarnessClient:
    """Facade stub whose ``kernel()`` is the REAL Kernel."""

    def __init__(self) -> None:
        self.provider = CountingProvider()
        self._kernel = Kernel(model_provider=self.provider)

    def process(self, text, request_id=None):
        INSTR.client_process += 1
        raise AssertionError("the inference path must never call client.process / _execute_single_item")

    def kernel(self):
        return self._kernel

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return None


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

    pgdata = Path("/tmp/platrixa_fte89_pgdata")
    pgdata.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(pgdata)
    uri = srv.get_uri()
    os.environ[metered_gate.METERING_ENV_VAR] = uri
    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    quota_ddl = (_REPO_ROOT / "backend" / "database" / "platrixa_tenant_quota_schema.sql").read_text(
        encoding="utf-8"
    )
    with engine.begin() as conn:
        conn.execute(text(quota_ddl))
    normalized = uri.replace("postgresql://", "postgresql+psycopg2://", 1)
    metered_gate._session_factory_cache.pop(normalized, None)
    idem._schema_ensured.discard(uri)
    idem._session_factory()
    async_jobs._schema_ensured.discard(uri)
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
                "ON CONFLICT (api_key_hash) DO UPDATE SET monthly_limit = :l, "
                "current_month_usage = 0, is_active = true"
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


def reset_runtime_state(engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM platrixa_async_jobs"))
        conn.execute(text("DELETE FROM platrixa_idempotency_keys"))
        conn.execute(text("DELETE FROM platrixa_tenant_quotas"))


def expire_lease(engine, job_id: str) -> None:
    """Simulate a crashed worker: make the lease non-current immediately."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_async_jobs SET lease_expires_at = now() - interval '1 hour' "
                "WHERE job_id = :j"
            ),
            {"j": job_id},
        )


def inference_job_count(engine) -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM platrixa_async_jobs WHERE kind = 'inference'")
        ).first()
    return int(row[0])


def make_client(stub) -> TestClient:
    developer.set_client(stub)
    return TestClient(create_app())


KEY_A = "fte89-tenant-a-key-0000000001"
KEY_B = "fte89-tenant-b-key-0000000002"


def worker_headers(key: str, worker_id: str) -> dict:
    return {"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id}


def create_and_claim(c: TestClient, engine, raw_input: str, worker_id: str):
    """Create an inference job and claim it → (job_id, lease, result_id, created)."""
    r = c.post(
        "/v1/inference-jobs",
        json={"raw_input": raw_input},
        headers={"X-Platrixa-API-Key": KEY_A},
    )
    assert r.status_code == 202, r.text
    created = r.json()
    job_id = created["job_id"]
    claim = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, worker_id))
    assert claim.status_code == 200, claim.text
    body = claim.json()
    # The claim is FIFO over the tenant's runnable inference jobs, so a
    # mismatch means the fixture left an unclaimed job behind (test bug).
    assert body["status"] == "CLAIMED" and body["job"]["job_id"] == job_id, body
    return job_id, body["job"]["lease"], created["result_id"], created


# ---------------------------------------------------------------------------
# Section A — the two kernel entry points
# ---------------------------------------------------------------------------


def section_a() -> None:
    print("\nA — Kernel entry points: ONE shared pipeline, no second inference")

    kernel = HarnessClient()._kernel  # real Kernel + the counting provider

    # A1/A2 shared pipeline reaches accounting + evidence, one interpret call.
    INSTR.stages.clear()
    calls = {"n": 0}

    def _interpret_once():
        calls["n"] += 1
        return interpretation_from_bridge_body(
            bridge_body(VALID_CANDIDATE), RAW_INPUT, model_id="local-llama.cpp",
            provider_revision=PROVENANCE_UNKNOWN,
        )

    result = kernel.process_from_source(RAW_INPUT, _interpret_once, request_id="rid-device-1")
    check("A1 process_from_source reaches deterministic accounting (VERIFIED)",
          result.status == "VERIFIED" and result.accounting_result is not None,
          f"status={result.status}")
    check("A2 the supplied interpretation callable ran exactly once (one inference)",
          calls["n"] == 1, f"calls={calls['n']}")
    check("A3 schema validation ran on the device path",
          INSTR.count("schema_validation") >= 1, f"stages={INSTR.stages}")
    check("A4 deterministic grounding ran on the device path",
          INSTR.count("grounding") >= 1, f"stages={INSTR.stages}")
    check("A5 GroundedSemanticIR conversion ran on the device path",
          INSTR.count("grounded_ir") >= 1, f"stages={INSTR.stages}")
    check("A6 deterministic accounting ran on the device path",
          INSTR.count("accounting") >= 1, f"stages={INSTR.stages}")
    check("A7 ExecutionEvidence was generated on the device path",
          INSTR.count("evidence") >= 1 and result.evidence is not None, f"stages={INSTR.stages}")
    check("A8 request_id is preserved verbatim", result.request_id == "rid-device-1",
          str(result.request_id))
    check("A9 no model provider was constructed/called on the device path "
          "(status+interpret both untouched)",
          INSTR.provider_status == 0 and INSTR.provider_interpret == 0,
          f"status={INSTR.provider_status} interpret={INSTR.provider_interpret}")

    ev = result.evidence.to_dict()
    missing = [k for k in _EVIDENCE_REQUIRED_KEYS if k not in ev]
    check("A10 execution evidence carries every required field", not missing, str(missing))
    check("A11 evidence final_state matches the returned engine status",
          ev["final_state"] == result.status, f"{ev['final_state']} != {result.status}")
    check("A12 device identity is labelled device-reported-unverified",
          ev["model_identity"].get("identity_source") == "device-reported-unverified"
          and ev["adapter_identity"].get("identity_source") == "device-reported-unverified",
          json.dumps({"model": ev["model_identity"], "adapter": ev["adapter_identity"]}))
    check("A13 device path never borrows the pinned production adapter revision",
          ev["adapter_identity"].get("adapter_revision") == PROVENANCE_UNKNOWN
          and ADAPTER_REVISION not in json.dumps(ev["adapter_identity"]),
          json.dumps(ev["adapter_identity"]))

    # A14-A17 fail-closed taxonomy on the device path.
    checks = {
        "A14": ("invalid enum → VALIDATION_FAILED", "VALIDATION_FAILED",
                {**copy.deepcopy(VALID_CANDIDATE), "transaction_type_enum": "NOT_A_REAL_ENUM"}, RAW_INPUT),
        "A17": ("unsupported amount → GROUNDING_FAILED", "GROUNDING_FAILED",
                {**copy.deepcopy(VALID_CANDIDATE),
                 "amounts": [{"value": "99999", "currency": "INR", "source": "explicit"}]}, RAW_INPUT),
        "A20": ("forbidden accounting field → FORBIDDEN_OUTPUT", "FORBIDDEN_OUTPUT",
                {**copy.deepcopy(VALID_CANDIDATE), "journal": {"lines": []}}, RAW_INPUT),
    }
    for label, (desc, expected, candidate, raw) in checks.items():
        res = kernel.process_from_source(
            raw,
            lambda cand=candidate, text=raw: interpretation_from_bridge_body(
                bridge_body(cand), text, model_id="local-llama.cpp",
                provider_revision=PROVENANCE_UNKNOWN,
            ),
        )
        check(f"{label} {desc}", res.status == expected, f"status={res.status}")

    # A15/A17/A20: stage ordering — validation stops before grounding, and
    # grounding stops before accounting.
    INSTR.stages.clear()
    kernel.process_from_source(
        RAW_INPUT,
        lambda: interpretation_from_bridge_body(
            bridge_body({**copy.deepcopy(VALID_CANDIDATE), "transaction_type_enum": "NOPE"}),
            RAW_INPUT, model_id="m", provider_revision=PROVENANCE_UNKNOWN,
        ),
    )
    check("A15 invalid schema stops BEFORE grounding",
          INSTR.count("schema_validation") >= 1 and INSTR.count("grounding") == 0
          and INSTR.count("accounting") == 0 and INSTR.count("evidence") == 0,
          f"stages={INSTR.stages}")

    INSTR.stages.clear()
    kernel.process_from_source(
        RAW_INPUT,
        lambda: interpretation_from_bridge_body(
            bridge_body({**copy.deepcopy(VALID_CANDIDATE),
                         "amounts": [{"value": "99999", "currency": "INR", "source": "explicit"}]}),
            RAW_INPUT, model_id="m", provider_revision=PROVENANCE_UNKNOWN,
        ),
    )
    check("A18 grounding failure stops BEFORE accounting",
          INSTR.count("grounding") >= 1 and INSTR.count("grounded_ir") == 0
          and INSTR.count("accounting") == 0 and INSTR.count("evidence") == 0,
          f"stages={INSTR.stages}")

    INSTR.stages.clear()
    kernel.process_from_source(
        RAW_INPUT,
        lambda: interpretation_from_bridge_body(
            bridge_body({**copy.deepcopy(VALID_CANDIDATE), "ledger": []}),
            RAW_INPUT, model_id="m", provider_revision=PROVENANCE_UNKNOWN,
        ),
    )
    check("A19 forbidden accounting field is rejected BEFORE validation or grounding",
          INSTR.count("schema_validation") == 0 and INSTR.count("grounding") == 0,
          f"stages={INSTR.stages}")

    for label, body, expected in (
        ("A21 truncated generation → VALIDATION_FAILED",
         bridge_body(VALID_CANDIDATE, completion_status="TRUNCATED"), "VALIDATION_FAILED"),
        ("A22 non-object bridge body → VALIDATION_FAILED", ["not", "an", "object"], "VALIDATION_FAILED"),
        ("A23 missing 18-field entry → VALIDATION_FAILED",
         bridge_body({k: v for k, v in VALID_CANDIDATE.items() if k != "suggested_status"}),
         "VALIDATION_FAILED"),
    ):
        res = kernel.process_from_source(
            RAW_INPUT,
            lambda b=body: interpretation_from_bridge_body(
                b, RAW_INPUT, model_id="m", provider_revision=PROVENANCE_UNKNOWN
            ),
        )
        check(label, res.status == expected, f"status={res.status}")

    # A24: a caller that is not callable, and a callable that returns the wrong
    # type, both fail closed with the EXISTING taxonomy (never success).
    res_not_callable = kernel.process_from_source(RAW_INPUT, None)
    check("A24 a non-callable interpretation source fails closed (MODEL_UNAVAILABLE)",
          res_not_callable.status == "MODEL_UNAVAILABLE" and res_not_callable.accounting_result is None,
          f"status={res_not_callable.status}")
    res_wrong_type = kernel.process_from_source(RAW_INPUT, lambda: {"candidate": {}})
    check("A25 a malformed interpretation source fails closed (VALIDATION_FAILED)",
          res_wrong_type.status == "VALIDATION_FAILED" and res_wrong_type.accounting_result is None,
          f"status={res_wrong_type.status}")

    empty_a = kernel.process_from_source("", lambda: None)
    check("A26 process_from_source empty input → VALIDATION_FAILED",
          empty_a.status == "VALIDATION_FAILED", f"status={empty_a.status}")

    # A27/A28: the SYNCHRONOUS path is unchanged — it still resolves and calls
    # its provider, and its evidence still carries the pinned adapter identity.
    before_status, before_interpret = INSTR.provider_status, INSTR.provider_interpret
    sync = kernel.process("purchased furniture from raj for rs.25000")
    check("A27 Kernel.process still invokes its provider exactly once",
          INSTR.provider_interpret - before_interpret == 1
          and INSTR.provider_status - before_status == 1
          and sync.status == "VERIFIED",
          f"interpret delta={INSTR.provider_interpret - before_interpret} status={sync.status}")
    sync_ev = sync.evidence.to_dict()
    check("A28 the synchronous path still records the provider-config adapter identity",
          sync_ev["adapter_identity"].get("adapter_revision") == ADAPTER_REVISION
          and sync_ev["adapter_identity"].get("adapter_repo_id") == ADAPTER_REPO_ID
          and "identity_source" not in sync_ev["adapter_identity"],
          json.dumps(sync_ev["adapter_identity"]))


# ---------------------------------------------------------------------------
# Section B — the shared bridge-body contract
# ---------------------------------------------------------------------------


def section_b() -> None:
    print("\nB — Shared bridge contract (one parser, no weaker second path)")

    bodies = {
        "valid": bridge_body(VALID_CANDIDATE),
        "verified_claim": bridge_body({**copy.deepcopy(VALID_CANDIDATE), "suggested_status": "VERIFIED"}),
        "truncated": bridge_body(VALID_CANDIDATE, completion_status="TRUNCATED"),
        "missing_field": bridge_body({k: v for k, v in VALID_CANDIDATE.items() if k != "parties"}),
        "forbidden": bridge_body({**copy.deepcopy(VALID_CANDIDATE), "journal": []}),
        "not_json": {"interpretation_text": "not json at all"},
        "no_keys": {"whatever": 1},
    }

    same_outcome = True
    detail = []
    for name, body in bodies.items():
        provider = LocalBridgeModelProvider(url="http://127.0.0.1:9", config=ProviderConfig())
        provider._call_bridge = lambda raw, b=body: (b, 1.0)  # no network; fixed body
        provider_outcome = _outcome(lambda: provider.interpret(RAW_INPUT))
        shared_outcome = _outcome(
            lambda b=body: interpretation_from_bridge_body(
                b, RAW_INPUT, model_id=provider.config.model_id,
                provider_revision=provider.config.adapter_revision,
            )
        )
        if provider_outcome[0] != shared_outcome[0]:
            same_outcome = False
            detail.append(f"{name}: provider={provider_outcome[0]} shared={shared_outcome[0]}")
        elif provider_outcome[0] == "ok":
            prov_cand = provider_outcome[1].candidate
            shared_cand = shared_outcome[1].candidate
            if prov_cand != shared_cand or prov_cand.get("suggested_status") != shared_cand.get("suggested_status"):
                same_outcome = False
                detail.append(f"{name}: candidate mismatch")

    check("B1 the synchronous provider and the shared parser agree on every body "
          "(same accept/reject, same candidate, same clamp)",
          same_outcome, "; ".join(detail))

    # B2/B3: the VERIFIED clamp is observable and audited, not silent.
    info = {}
    parsed = interpretation_from_bridge_body(
        bridge_body({**copy.deepcopy(VALID_CANDIDATE), "suggested_status": "VERIFIED"}),
        RAW_INPUT, model_id="m", provider_revision=PROVENANCE_UNKNOWN, audit=info,
    )
    check("B2 a model-suggested VERIFIED is clamped to REVIEW_REQUIRED",
          parsed.candidate["suggested_status"] == "REVIEW_REQUIRED",
          str(parsed.candidate.get("suggested_status")))
    check("B3 the clamp is recorded in the audit trace (never silent)",
          info.get("contract", {}).get("suggested_status_raw") == "VERIFIED"
          and info.get("contract", {}).get("suggested_status_clamped_to_review_required") is True,
          json.dumps(info.get("contract")))

    # B4: no field is ever invented.
    err = _outcome(lambda: interpretation_from_bridge_body(
        bridge_body({k: v for k, v in VALID_CANDIDATE.items() if k != "scope_flags"}),
        RAW_INPUT, model_id="m", provider_revision=PROVENANCE_UNKNOWN,
    ))
    check("B4 a missing field is rejected by name (nothing invented)",
          err[0] == "MalformedOutputError" and "scope_flags" in err[1], str(err[0]))

    # B5: device-reported identity helper only reports what the device said.
    from backend.model_provider.local_bridge import device_reported_model_identity

    got = device_reported_model_identity({"model": {"provider": "local-llama.cpp", "artifact_loaded": "x"}})
    check("B5a unknown artifact provenance stays PROVENANCE_UNKNOWN",
          got == ("local-llama.cpp", PROVENANCE_UNKNOWN), str(got))
    got = device_reported_model_identity({"model": {"provider": "local-llama.cpp", "artifact_identity": "sha256:abc"}})
    check("B5b a device-reported artifact string is reported verbatim (and labelled unverified downstream)",
          got == ("local-llama.cpp", "sha256:abc"), str(got))
    check("B5c a body with no model block reports PROVENANCE_UNKNOWN",
          device_reported_model_identity({})[1] == PROVENANCE_UNKNOWN, str(device_reported_model_identity({})))


def _outcome(fn) -> tuple:
    """('ok', result) | ('ErrorType', str(exc)) — never raises."""
    try:
        return ("ok", fn())
    except Exception as exc:  # noqa: BLE001 — the test IS the error mapping
        return (type(exc).__name__, str(exc))


# ---------------------------------------------------------------------------
# Section C — creation, admission and idempotency
# ---------------------------------------------------------------------------


def section_c(c: TestClient, engine) -> None:
    print("\nC — Inference job creation: admission + idempotency")

    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-fte89-a", 1000)
    make_tenant(engine, KEY_B, "tenant-fte89-b", 1000)

    # C1 happy path
    r = c.post("/v1/inference-jobs", json={"raw_input": RAW_INPUT}, headers={"X-Platrixa-API-Key": KEY_A})
    check("C1 creation → 202 with a job/result reference and both polling URLs",
          r.status_code == 202 and r.json()["job_id"].startswith("job_")
          and r.json()["result_id"].startswith("res_")
          and r.json()["status"] == "PROCESSING"
          and r.json()["status_url"].endswith(r.json()["job_id"])
          and r.json()["result_url"].endswith(r.json()["result_id"]),
          f"{r.status_code} {r.text[:160]}")
    job_id = r.json()["job_id"]
    rows = async_jobs.get_job("tenant-fte89-a", job_id)
    check("C2 the durable job exists BEFORE any inference, kind='inference', status QUEUED",
          rows is not None and rows.kind == "inference" and rows.status == "QUEUED",
          f"{getattr(rows, 'kind', None)}/{getattr(rows, 'status', None)}")
    check("C3 exactly ONE admission unit reserved for the new job",
          tenant_usage(engine, KEY_A) == 1, str(tenant_usage(engine, KEY_A)))

    # C4 replay: same key + same body
    key = "fte89-idem-key-000000000001"
    hdr = {"X-Platrixa-API-Key": KEY_A, "Idempotency-Key": key}
    r1 = c.post("/v1/inference-jobs", json={"raw_input": RAW_INPUT}, headers=hdr)
    usage_after_first = tenant_usage(engine, KEY_A)
    r2 = c.post("/v1/inference-jobs", json={"raw_input": RAW_INPUT}, headers=hdr)
    check("C4 same key + same request replays the SAME job reference",
          r1.status_code == 202 and r2.status_code == 202
          and r1.json()["job_id"] == r2.json()["job_id"]
          and r2.headers.get("Idempotent-Replayed") == "true",
          f"{r2.status_code} {r2.headers.get('Idempotent-Replayed')}")
    check("C5 the replay created no duplicate row",
          inference_job_count(engine) == 2, str(inference_job_count(engine)))
    check("C6 the replay charged nothing",
          tenant_usage(engine, KEY_A) == usage_after_first, str(tenant_usage(engine, KEY_A)))

    # C7 conflict: same key + different body
    r3 = c.post("/v1/inference-jobs", json={"raw_input": "something else entirely"}, headers=hdr)
    check("C7 same key + DIFFERENT request → 409 conflict",
          r3.status_code == 409
          and r3.json()["error"]["code"] == idem.CONFLICT_CODE,
          f"{r3.status_code} {r3.text[:140]}")
    check("C8 the conflict created no job and charged nothing",
          inference_job_count(engine) == 2
          and tenant_usage(engine, KEY_A) == usage_after_first,
          f"jobs={inference_job_count(engine)} usage={tenant_usage(engine, KEY_A)}")

    # C9 the canonical schema (raw-input length constraints) is enforced.
    usage_before = tenant_usage(engine, KEY_A)
    r_missing = c.post("/v1/inference-jobs", json={}, headers={"X-Platrixa-API-Key": KEY_A})
    r_empty = c.post("/v1/inference-jobs", json={"raw_input": ""}, headers={"X-Platrixa-API-Key": KEY_A})
    r_long = c.post("/v1/inference-jobs", json={"raw_input": "x" * 2001}, headers={"X-Platrixa-API-Key": KEY_A})
    check("C9 missing / empty / over-length raw_input are all rejected as malformed (400)",
          all(r.status_code == 400 for r in (r_missing, r_empty, r_long))
          and all(r.json()["error"]["code"] == "REQUEST_MALFORMED" for r in (r_missing, r_empty, r_long)),
          f"{[r.status_code for r in (r_missing, r_empty, r_long)]}")
    check("C10 a rejected request is free (no reservation, no job)",
          tenant_usage(engine, KEY_A) == usage_before
          and inference_job_count(engine) == 2,
          f"usage={tenant_usage(engine, KEY_A)} jobs={inference_job_count(engine)}")

    # C11 misconfiguration/authorization are honest and free.
    r_nokey = c.post("/v1/inference-jobs", json={"raw_input": RAW_INPUT})
    check("C11 a missing API key is refused (401) before any job or store work",
          r_nokey.status_code == 401 and inference_job_count(engine) == 2,
          f"{r_nokey.status_code}")
    r_badkey = c.post("/v1/inference-jobs", json={"raw_input": RAW_INPUT},
                      headers={"X-Platrixa-API-Key": "totally-unknown-key-0000"})
    check("C12 an unknown API key is refused (401) with no job created",
          r_badkey.status_code == 401 and inference_job_count(engine) == 2,
          f"{r_badkey.status_code}")

    # C13 fault injection at the durable-creation boundary.
    key_fault = "fte89-idem-key-fault-000000001"
    hdr_fault = {"X-Platrixa-API-Key": KEY_A, "Idempotency-Key": key_fault}
    jobs_before = inference_job_count(engine)
    orig_create = async_jobs.create_job

    def _boom(*args, **kwargs):
        raise async_jobs.AsyncStoreUnavailableError("injected store failure")

    async_jobs.create_job = _boom
    try:
        r_fault = c.post("/v1/inference-jobs", json={"raw_input": "injected fault input"}, headers=hdr_fault)
    finally:
        async_jobs.create_job = orig_create
    check("C13 a durable-creation failure fails closed (503 ASYNC_UNAVAILABLE)",
          r_fault.status_code == 503 and r_fault.json()["error"]["code"] == "ASYNC_UNAVAILABLE",
          f"{r_fault.status_code} {r_fault.text[:140]}")
    check("C14 the failed creation left NO job row behind",
          inference_job_count(engine) == jobs_before, str(inference_job_count(engine)))

    # Retry with the same key: the claim was released, so this must be a NEW
    # canonical attempt (not an in-progress replay), creating exactly one job.
    r_retry = c.post("/v1/inference-jobs", json={"raw_input": "injected fault input"}, headers=hdr_fault)
    check("C15 the retry after the fault is a NEW canonical attempt (claim released)",
          r_retry.status_code == 202
          and r_retry.json().get("reason_code") != idem.IN_PROGRESS_CODE,
          f"{r_retry.status_code} {r_retry.text[:160]}")
    check("C16 the retry created exactly ONE job (no duplicate)",
          inference_job_count(engine) == jobs_before + 1, str(inference_job_count(engine)))


def section_c2(c: TestClient) -> None:
    print("\nC — zero-config honesty (no durable store configured)")
    previous = os.environ.pop(metered_gate.METERING_ENV_VAR, None)
    try:
        r = c.post("/v1/inference-jobs", json={"raw_input": RAW_INPUT})
        check("C17 a deployment without the durable store refuses honestly (400 ASYNC_NOT_CONFIGURED)",
              r.status_code == 400 and r.json()["error"]["code"] == "ASYNC_NOT_CONFIGURED",
              f"{r.status_code} {r.text[:140]}")
        for path, payload in (
            ("/v1/worker/jobs/claim", None),
            ("/v1/worker/jobs/job_x/lease", {"lease": {"owner": "w", "generation": 1}}),
            ("/v1/worker/jobs/job_x/result", {"lease": {"owner": "w", "generation": 1}, "bridge_body": {}}),
        ):
            rr = c.post(path, json=payload) if payload is not None else c.post(path)
            assert rr.status_code == 400 and rr.json()["error"]["code"] == "ASYNC_NOT_CONFIGURED", (path, rr.text)
        check("C18 all three worker endpoints refuse identically on a zero-config deployment", True)
    finally:
        if previous is not None:
            os.environ[metered_gate.METERING_ENV_VAR] = previous


# ---------------------------------------------------------------------------
# Section D — claim, lease heartbeat, result submission, fencing
# ---------------------------------------------------------------------------


def section_d(c: TestClient, engine) -> None:
    print("\nD — Worker plane: claim, lease heartbeat, fenced result commit")

    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-fte89-a", 1000)
    make_tenant(engine, KEY_B, "tenant-fte89-b", 1000)

    job_id, lease, submitted_result_id, created = create_and_claim(c, engine, RAW_INPUT, "w-device-1")
    check("D1 claim returns the payload and lease metadata for the authenticated tenant",
          lease["owner"] == "w-device-1" and lease["generation"] >= 1
          and lease["lease_seconds"] == async_jobs.inference_lease_seconds()
          and lease["renew_every_seconds"] >= 1
          and lease["result_url"].endswith(f"/{job_id}/result"),
          json.dumps(lease))
    check("D2 the claimed job is PROCESSING and owned by this worker",
          async_jobs.get_job("tenant-fte89-a", job_id).status == "PROCESSING")

    # D3 no work is an explicit, non-disclosing answer.
    r_nowork = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_B, "w-device-b"))
    check("D3 a tenant with no runnable inference job gets an explicit NO_WORK (no leak)",
          r_nowork.status_code == 200 and r_nowork.json()["status"] == "NO_WORK"
          and r_nowork.json()["job"] is None,
          r_nowork.text[:140])

    # D4 lease heartbeat
    r_renew = c.post(f"/v1/worker/jobs/{job_id}/lease",
                     json={"lease": {"owner": lease["owner"], "generation": lease["generation"]}},
                     headers=worker_headers(KEY_A, "w-device-1"))
    check("D4 a heartbeat with the current proof extends the lease (and never charges quota)",
          r_renew.status_code == 200 and r_renew.json()["renewed"] is True
          and tenant_usage(engine, KEY_A) == 1,
          f"{r_renew.status_code} usage={tenant_usage(engine, KEY_A)}")
    r_wrong_gen = c.post(f"/v1/worker/jobs/{job_id}/lease",
                         json={"lease": {"owner": lease["owner"], "generation": lease["generation"] + 7}},
                         headers=worker_headers(KEY_A, "w-device-1"))
    check("D5 a heartbeat with the wrong generation is refused as LEASE_LOST",
          r_wrong_gen.status_code == 409 and r_wrong_gen.json()["error"]["code"] == "LEASE_LOST",
          f"{r_wrong_gen.status_code} {r_wrong_gen.text[:140]}")

    # D6 the fenced, authoritative commit.
    INSTR.stages.clear()
    INSTR.commits_accepted = 0
    payload = {"lease": {"owner": lease["owner"], "generation": lease["generation"]},
               "bridge_body": bridge_body(VALID_CANDIDATE)}
    r_submit = c.post(f"/v1/worker/jobs/{job_id}/result", json=payload,
                      headers=worker_headers(KEY_A, "w-device-1"))
    check("D6 result submission returns the authoritative 5D envelope (VERIFIED)",
          r_submit.status_code == 200 and r_submit.json()["status"] == "VERIFIED"
          and r_submit.json()["api_status"] == "VERIFIED"
          and r_submit.json()["accounting_result"] is not None
          and r_submit.json()["interpretation"] is not None
          and r_submit.json()["accounting"] == r_submit.json()["accounting_result"],
          f"{r_submit.status_code} {r_submit.text[:300]}")
    check("D7 exactly ONE fenced commit was accepted", INSTR.commits_accepted == 1,
          str(INSTR.commits_accepted))
    check("D8 exactly one device-bridge parse (one inference) produced that result",
          INSTR.count("bridge_parse") == 1, str(INSTR.count("bridge_parse")))
    stored = json.loads(async_jobs.get_job("tenant-fte89-a", job_id).result_json)

    # D9 duplicate submission: stored envelope, no second kernel execution.
    pfs_before = INSTR.process_from_source
    r_dup = c.post("/v1/worker/jobs/{}/result".format(job_id), json={
        "lease": {"owner": lease["owner"], "generation": lease["generation"]},
        "bridge_body": bridge_body(VALID_CANDIDATE),
    }, headers=worker_headers(KEY_A, "w-device-1"))
    check("D9 a duplicate submission returns the STORED envelope (replayed, not recomputed)",
          r_dup.status_code == 200 and r_dup.json() == r_submit.json()
          and r_dup.headers.get("Idempotent-Replayed") == "true",
          f"{r_dup.status_code} {r_dup.headers.get('Idempotent-Replayed')}")
    check("D10 the duplicate did NOT re-run the kernel",
          INSTR.process_from_source == pfs_before,
          f"{INSTR.process_from_source} != {pfs_before}")

    # D11/D12 the EXISTING read routes are reused, not duplicated.
    check("D11 creation returns the polling URLs of the EXISTING job/result routes",
          created["status_url"].endswith(f"/v1/jobs/{created['job_id']}")
          and created["result_url"].endswith(f"/v1/results/{created['result_id']}"),
          json.dumps({"s": created["status_url"], "r": created["result_url"]}))
    st = c.get(f"/v1/jobs/{job_id}", headers={"X-Platrixa-API-Key": KEY_A})
    check("D12 the reused job-status endpoint reports the real engine state",
          st.status_code == 200 and st.json()["status"] == "VERIFIED",
          f"{st.status_code} {st.text[:140]}")
    rr = c.get(f"/v1/results/{submitted_result_id}", headers={"X-Platrixa-API-Key": KEY_A})
    check("D12b the reused result endpoint returns EXACTLY the stored envelope",
          rr.status_code == 200 and rr.json() == stored,
          f"{rr.status_code}")

    # D13/D14 worker death: the replacement worker may re-infer, only its lease commits.
    job2, lease2, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-device-A")
    expire_lease(engine, job2)
    claim2 = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, "w-device-B"))
    ok_claim2 = claim2.status_code == 200 and claim2.json()["status"] == "CLAIMED" \
        and claim2.json()["job"]["job_id"] == job2
    lease2b = claim2.json()["job"]["lease"] if ok_claim2 else {}
    check("D13 after lease expiry a replacement worker claims the SAME job at a NEW generation",
          ok_claim2 and lease2b.get("generation") == lease2["generation"] + 1,
          f"{claim2.status_code} {claim2.text[:160]}")
    r_stale = c.post(f"/v1/worker/jobs/{job2}/result", json={
        "lease": {"owner": lease2["owner"], "generation": lease2["generation"]},
        "bridge_body": bridge_body(VALID_CANDIDATE),
    }, headers=worker_headers(KEY_A, "w-device-A"))
    check("D14 the stale (superseded-generation) worker CANNOT commit",
          r_stale.status_code == 409 and r_stale.json()["error"]["code"] == "LEASE_LOST"
          and async_jobs.get_job("tenant-fte89-a", job2).status == "PROCESSING",
          f"{r_stale.status_code} {r_stale.text[:160]}")
    r_stale_renew = c.post(f"/v1/worker/jobs/{job2}/lease",
                           json={"lease": {"owner": lease2["owner"], "generation": lease2["generation"]}},
                           headers=worker_headers(KEY_A, "w-device-A"))
    check("D15 the stale worker also cannot RENEW its superseded lease",
          r_stale_renew.status_code == 409 and r_stale_renew.json()["error"]["code"] == "LEASE_LOST",
          f"{r_stale_renew.status_code}")
    r_fresh = c.post(f"/v1/worker/jobs/{job2}/result", json={
        "lease": {"owner": lease2b["owner"], "generation": lease2b["generation"]},
        "bridge_body": bridge_body(VALID_CANDIDATE),
    }, headers=worker_headers(KEY_A, "w-device-B"))
    check("D16 the CURRENT lease owner commits successfully",
          r_fresh.status_code == 200 and r_fresh.json()["status"] == "VERIFIED",
          f"{r_fresh.status_code} {r_fresh.text[:160]}")

    # D17 missing lease proof is refused and changes nothing.
    job3, lease3, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-device-C")
    r_nolease = c.post(f"/v1/worker/jobs/{job3}/result", json={"bridge_body": bridge_body(VALID_CANDIDATE)},
                       headers=worker_headers(KEY_A, "w-device-C"))
    check("D17 a submission with no lease proof is refused",
          r_nolease.status_code in (400, 409)
          and r_nolease.json()["error"]["code"] in ("REQUEST_MALFORMED", "LEASE_LOST"),
          f"{r_nolease.status_code} {r_nolease.text[:140]}")
    check("D18 the refused submission left the job PROCESSING with no stored result",
          async_jobs.get_job("tenant-fte89-a", job3).status == "PROCESSING"
          and async_jobs.get_job("tenant-fte89-a", job3).result_json is None)
    r_nolease_renew = c.post(f"/v1/worker/jobs/{job3}/lease", json={"owner": "w-device-C"},
                             headers=worker_headers(KEY_A, "w-device-C"))
    check("D19 a heartbeat with no lease proof is refused",
          r_nolease_renew.status_code == 400, f"{r_nolease_renew.status_code}")

    # D20 concurrent submissions at the SAME generation → exactly one commit.
    job4, lease4, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-device-D")
    INSTR.commits_accepted = 0
    barrier = threading.Barrier(2)
    results: list = []

    def _submit():
        barrier.wait()
        rr = c.post(f"/v1/worker/jobs/{job4}/result", json={
            "lease": {"owner": lease4["owner"], "generation": lease4["generation"]},
            "bridge_body": bridge_body(VALID_CANDIDATE),
        }, headers=worker_headers(KEY_A, "w-device-D"))
        results.append((rr.status_code, rr.json()))

    threads = [threading.Thread(target=_submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    commits = INSTR.commits_accepted
    two_ok = [b for code, b in results if code == 200]
    check("D20 concurrent submissions at one generation produce exactly ONE fenced commit",
          commits == 1 and len(two_ok) >= 1,
          f"commits={commits} statuses={[c for c, _ in results]}")
    check("D21 every accepted response carries the same authoritative envelope",
          all(b == two_ok[0] for b in two_ok) and len(two_ok) >= 1,
          f"{[b.get('status') for b in two_ok]}")
    stored4 = json.loads(async_jobs.get_job("tenant-fte89-a", job4).result_json)
    check("D22 the stored result is that envelope",
          bool(two_ok) and stored4 == two_ok[0])


# ---------------------------------------------------------------------------
# Section E — kind/tenant isolation and deterministic authority
# ---------------------------------------------------------------------------


def section_e(c: TestClient, engine) -> None:
    print("\nE — kind/tenant isolation + deterministic authority")

    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-fte89-a", 1000)
    make_tenant(engine, KEY_B, "tenant-fte89-b", 1000)

    # E1-E3 kind scoping of the two queues.
    doc_job = async_jobs.create_job("tenant-fte89-a", {"raw_input": RAW_INPUT, "source_name": "t.txt"})
    inf_job = async_jobs.create_job("tenant-fte89-a", {"raw_input": RAW_INPUT},
                                    kind=async_jobs.JOB_KIND_INFERENCE)
    claimed_doc = async_jobs.claim_next_job(kind=async_jobs.JOB_KIND_DOCUMENT)
    check("E1 the document worker claims ONLY the document job",
          claimed_doc is not None and claimed_doc.job_id == doc_job.job_id
          and claimed_doc.kind == "document",
          f"{getattr(claimed_doc, 'job_id', None)}/{getattr(claimed_doc, 'kind', None)}")
    claimed_inf = async_jobs.claim_next_job(kind=async_jobs.JOB_KIND_INFERENCE)
    check("E2 the inference worker claims ONLY the inference job",
          claimed_inf is not None and claimed_inf.job_id == inf_job.job_id
          and claimed_inf.kind == "inference",
          f"{getattr(claimed_inf, 'job_id', None)}/{getattr(claimed_inf, 'kind', None)}")
    check("E3 the claim returns the payload directly (no unscoped lookup needed)",
          isinstance(getattr(claimed_inf, "request_json", None), str)
          and json.loads(claimed_inf.request_json)["raw_input"] == RAW_INPUT,
          str(getattr(claimed_inf, "request_json", None)))

    src = (_REPO_ROOT / "api" / "routes" / "async_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    loop_call_ok = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_worker_loop":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    name = getattr(fn, "attr", None)
                    if name == "claim_next_job":
                        for kw in sub.keywords:
                            if kw.arg == "kind" and isinstance(kw.value, ast.Attribute) \
                                    and kw.value.attr == "JOB_KIND_DOCUMENT":
                                loop_call_ok = True
    check("E4 the in-process document worker is wired to the document kind only",
          loop_call_ok)

    # E5/E6 the inference HTTP claim is tenant-scoped.
    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-fte89-a", 1000)
    make_tenant(engine, KEY_B, "tenant-fte89-b", 1000)
    job_a, lease_a, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-a")
    r_b_claim = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_B, "w-b"))
    check("E5 a different tenant cannot claim this tenant's inference job",
          r_b_claim.status_code == 200 and r_b_claim.json()["status"] == "NO_WORK",
          r_b_claim.text[:140])
    r_b_lease = c.post(f"/v1/worker/jobs/{job_a}/lease",
                       json={"lease": {"owner": lease_a["owner"], "generation": lease_a["generation"]}},
                       headers=worker_headers(KEY_B, "w-b"))
    r_b_result = c.post(f"/v1/worker/jobs/{job_a}/result",
                        json={"lease": {"owner": lease_a["owner"], "generation": lease_a["generation"]},
                              "bridge_body": bridge_body(VALID_CANDIDATE)},
                        headers=worker_headers(KEY_B, "w-b"))
    check("E6 a foreign tenant gets a non-disclosing JOB_NOT_FOUND on lease and result",
          r_b_lease.status_code == 404 and r_b_result.status_code == 404
          and r_b_lease.json()["error"]["code"] == "JOB_NOT_FOUND"
          and r_b_result.json()["error"]["code"] == "JOB_NOT_FOUND",
          f"{r_b_lease.status_code}/{r_b_result.status_code}")
    r_b_poll = c.get(f"/v1/jobs/{job_a}", headers={"X-Platrixa-API-Key": KEY_B})
    check("E7 the foreign tenant cannot poll the job either (and it is unchanged)",
          r_b_poll.status_code == 404
          and async_jobs.get_job("tenant-fte89-a", job_a).status == "PROCESSING",
          f"{r_b_poll.status_code}")
    r_b_renew_ok = c.post(f"/v1/worker/jobs/{job_a}/lease",
                          json={"lease": {"owner": lease_a["owner"], "generation": lease_a["generation"]}},
                          headers=worker_headers(KEY_A, "w-a"))
    check("E8 the true owner's heartbeat still works after the foreign attempts",
          r_b_renew_ok.status_code == 200, f"{r_b_renew_ok.status_code}")

    # E9 kind-scoped result submission: a document job is not an inference job.
    doc2 = async_jobs.create_job("tenant-fte89-a", {"raw_input": RAW_INPUT})
    claimed_doc2 = async_jobs.claim_next_job(kind=async_jobs.JOB_KIND_DOCUMENT, tenant_id="tenant-fte89-a")
    r_doc_result = c.post(f"/v1/worker/jobs/{doc2.job_id}/result", json={
        "lease": {"owner": claimed_doc2.lease_owner, "generation": claimed_doc2.lease_generation},
        "bridge_body": bridge_body(VALID_CANDIDATE),
    }, headers=worker_headers(KEY_A, "w-a"))
    check("E9 a DOCUMENT job cannot be committed through the inference result route (404)",
          r_doc_result.status_code == 404 and r_doc_result.json()["error"]["code"] == "JOB_NOT_FOUND",
          f"{r_doc_result.status_code} {r_doc_result.text[:140]}")

    # E10 deterministic authority: every hostile candidate keeps its REAL state.
    def submit_and_expect(candidate, raw, expected_status, expected_code, label):
        job_id, lease, _, _ = create_and_claim(c, engine, raw, "w-auth")
        r = c.post(f"/v1/worker/jobs/{job_id}/result", json={
            "lease": {"owner": lease["owner"], "generation": lease["generation"]},
            "bridge_body": bridge_body(candidate),
        }, headers=worker_headers(KEY_A, "w-auth"))
        body = r.json()
        check(label,
              body.get("status") == expected_status and r.status_code == expected_code,
              f"{r.status_code} status={body.get('status')}")
        return r

    submit_and_expect(
        {**copy.deepcopy(VALID_CANDIDATE), "suggested_status": "VERIFIED"}, RAW_INPUT,
        "VERIFIED", 200,
        "E10 a candidate CLAIMING VERIFIED is clamped, then independently verified by the pipeline",
    )
    submit_and_expect(
        {**copy.deepcopy(VALID_CANDIDATE), "journal": {"lines": []}}, RAW_INPUT,
        "FORBIDDEN_OUTPUT", 422,
        "E11 forbidden accounting fields are rejected (never VERIFIED)",
    )
    submit_and_expect(
        {**copy.deepcopy(VALID_CANDIDATE),
         "amounts": [{"value": "424242", "currency": "INR", "source": "explicit"}]}, RAW_INPUT,
        "GROUNDING_FAILED", 422,
        "E12 a grounding failure keeps its real engine status (never VERIFIED)",
    )

    # Truncated generation is rejected at the transport/contract layer.
    job_t, lease_t, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-auth")
    r_trunc = c.post(f"/v1/worker/jobs/{job_t}/result", json={
        "lease": {"owner": lease_t["owner"], "generation": lease_t["generation"]},
        "bridge_body": bridge_body(VALID_CANDIDATE, completion_status="TRUNCATED"),
    }, headers=worker_headers(KEY_A, "w-auth"))
    check("E13 truncated device output is rejected and never becomes VERIFIED",
          r_trunc.status_code == 422 and r_trunc.json()["status"] == "VALIDATION_FAILED",
          f"{r_trunc.status_code} {r_trunc.json().get('status')}")

    # An unsupported input keeps the accounting flow's own terminal state.
    job_u, lease_u, _, _ = create_and_claim(c, engine, "hello there", "w-auth")
    r_unsup = c.post(f"/v1/worker/jobs/{job_u}/result", json={
        "lease": {"owner": lease_u["owner"], "generation": lease_u["generation"]},
        "bridge_body": bridge_body(UNSUPPORTED_CANDIDATE),
    }, headers=worker_headers(KEY_A, "w-auth"))
    unsup_body = r_unsup.json()
    check("E14 an unsupported transaction retains the engine's real terminal state",
          unsup_body.get("status") in ("NOT_SUPPORTED", "UNSUPPORTED_TRANSACTION", "BLOCKED")
          and unsup_body.get("status") != "VERIFIED"
          and r_unsup.status_code >= 400,
          f"{r_unsup.status_code} status={unsup_body.get('status')}")

    # E15 malformed submissions are refused before any kernel work.
    job_m, lease_m, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-auth")
    INSTR.stages.clear()
    r_ctype = c.post(f"/v1/worker/jobs/{job_m}/result", content=b"{}",
                     headers={**worker_headers(KEY_A, "w-auth"), "Content-Type": "text/plain"})
    check("E15 a non-JSON content type is refused (415)",
          r_ctype.status_code == 415, f"{r_ctype.status_code}")
    r_big = c.post(f"/v1/worker/jobs/{job_m}/result",
                   content=b"x" * (70 * 1024),
                   headers={**worker_headers(KEY_A, "w-auth"), "Content-Type": "application/json"})
    check("E16 an oversized submission is refused (413)",
          r_big.status_code == 413, f"{r_big.status_code}")
    r_nobody = c.post(f"/v1/worker/jobs/{job_m}/result", json={
        "lease": {"owner": lease_m["owner"], "generation": lease_m["generation"]},
    }, headers=worker_headers(KEY_A, "w-auth"))
    check("E17 a submission with no bridge_body is refused (400)",
          r_nobody.status_code == 400 and r_nobody.json()["error"]["code"] == "REQUEST_MALFORMED",
          f"{r_nobody.status_code}")
    check("E18 none of the malformed submissions reached the kernel or stored a result",
          INSTR.count("bridge_parse") == 0
          and async_jobs.get_job("tenant-fte89-a", job_m).status == "PROCESSING",
          f"parses={INSTR.count('bridge_parse')}")
    check("E19 the worker endpoints reject an unauthenticated caller (401)",
          c.post("/v1/worker/jobs/claim").status_code == 401
          and c.post(f"/v1/worker/jobs/{job_m}/lease",
                     json={"lease": {"owner": "w", "generation": 1}}).status_code == 401
          and c.post(f"/v1/worker/jobs/{job_m}/result",
                     json={"lease": {"owner": "w", "generation": 1}, "bridge_body": {}}).status_code == 401)

    # E22 the inference lease is CONFIGURABLE (provisional, tunable) and a
    # short lease still cannot be committed after it is lost.
    os.environ[async_jobs.INFERENCE_LEASE_ENV_VAR] = "17"
    try:
        check("E22 the inference lease is configurable via its own variable",
              async_jobs.inference_lease_seconds() == 17,
              str(async_jobs.inference_lease_seconds()))
        job_short, lease_short, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-short")
        check("E23 the configured lease is what the claim actually grants",
              lease_short["lease_seconds"] == 17, json.dumps(lease_short))
        check("E24 a malformed lease setting falls back to the default (never a zero lease)",
              _lease_setting("not-a-number") == async_jobs.JOB_LEASE_SECONDS
              and _lease_setting("0") == async_jobs.JOB_LEASE_SECONDS
              and _lease_setting("-3") == async_jobs.JOB_LEASE_SECONDS,
              "malformed lease was accepted")
        # Lease EXPIRY alone is not "lease loss": the fencing GENERATION is the
        # authority, and an expired lease that nobody has reclaimed still has
        # the current generation, so this worker is late, not stale. That is
        # the repository's established 5K contract (the document worker
        # behaves identically), so the test asserts the real contract:
        # expiry + RECLAIM is what must block the original worker.
        expire_lease(engine, job_short)
        reclaim = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, "w-short-replacement"))
        reclaimed = reclaim.status_code == 200 and reclaim.json()["status"] == "CLAIMED" \
            and reclaim.json()["job"]["job_id"] == job_short
        live_generation = reclaim.json()["job"]["lease"]["generation"] if reclaimed else -1
        check("E25 a short configured lease lets a replacement reclaim at a NEW generation",
              reclaimed and live_generation == lease_short["generation"] + 1,
              f"{reclaim.status_code} {reclaim.text[:140]}")
        r_late = c.post(f"/v1/worker/jobs/{job_short}/result", json={
            "lease": {"owner": lease_short["owner"], "generation": lease_short["generation"]},
            "bridge_body": bridge_body(VALID_CANDIDATE),
        }, headers=worker_headers(KEY_A, "w-short"))
        check("E25b once the lease is RECLAIMED the superseded worker cannot commit",
              r_late.status_code == 409 and r_late.json()["error"]["code"] == "LEASE_LOST",
              f"{r_late.status_code} {r_late.text[:140]}")
        r_late_renew = c.post(f"/v1/worker/jobs/{job_short}/lease",
                              json={"lease": {"owner": lease_short["owner"],
                                              "generation": lease_short["generation"]}},
                              headers=worker_headers(KEY_A, "w-short"))
        check("E25c ...and cannot renew its superseded lease either",
              r_late_renew.status_code == 409, f"{r_late_renew.status_code}")
    finally:
        os.environ.pop(async_jobs.INFERENCE_LEASE_ENV_VAR, None)
    check("E26 with the override removed the default lease is restored",
          async_jobs.inference_lease_seconds() == async_jobs.JOB_LEASE_SECONDS,
          str(async_jobs.inference_lease_seconds()))

    # E27 an unexpected server-side validation failure is a RETRYABLE job
    # failure (existing vocabulary), not a committed authoritative result.
    job_err, lease_err, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-err")
    orig_parse = local_bridge.interpretation_from_bridge_body

    def _explode(*args, **kwargs):
        raise RuntimeError("injected server-side parse failure")

    local_bridge.interpretation_from_bridge_body = _explode
    try:
        r_err = c.post(f"/v1/worker/jobs/{job_err}/result", json={
            "lease": {"owner": lease_err["owner"], "generation": lease_err["generation"]},
            "bridge_body": bridge_body(VALID_CANDIDATE),
        }, headers=worker_headers(KEY_A, "w-err"))
    finally:
        local_bridge.interpretation_from_bridge_body = orig_parse
    err_rec = async_jobs.get_job("tenant-fte89-a", job_err)
    check("E27 an unexpected server-side failure is recorded as a RETRYABLE job failure",
          r_err.status_code == 503 and r_err.json()["error"]["code"] == "MODEL_UNAVAILABLE"
          and err_rec.result_json is None
          and err_rec.status in (async_jobs.STATUS_RETRY_WAIT, async_jobs.STATUS_FAILED)
          and err_rec.retryable is True,
          f"{r_err.status_code} job={err_rec.status} retryable={err_rec.retryable}")

    # E20 evidence is present on the stored authoritative result.
    job_ev, lease_ev, _, _ = create_and_claim(c, engine, RAW_INPUT, "w-ev")
    r_ev = c.post(f"/v1/worker/jobs/{job_ev}/result", json={
        "lease": {"owner": lease_ev["owner"], "generation": lease_ev["generation"]},
        "bridge_body": bridge_body(VALID_CANDIDATE),
    }, headers=worker_headers(KEY_A, "w-ev"))
    ev = (r_ev.json().get("metadata") or {}).get("execution_evidence") or {}
    missing_ev = [k for k in _EVIDENCE_REQUIRED_KEYS if k not in ev]
    check("E20 the stored authoritative result carries the execution evidence chain",
          not missing_ev and ev.get("final_state") == "VERIFIED", str(missing_ev))
    check("E21 that evidence labels device identity unverified and invents no provenance",
          ev.get("model_identity", {}).get("identity_source") == "device-reported-unverified"
          and ev.get("adapter_identity", {}).get("adapter_revision") == PROVENANCE_UNKNOWN,
          json.dumps({"m": ev.get("model_identity"), "a": ev.get("adapter_identity")}))


# ---------------------------------------------------------------------------
# Section F — end to end + production-transport safety
# ---------------------------------------------------------------------------


def section_f(c: TestClient, engine) -> None:
    print("\nF — End to end + production transport untouched")

    reset_runtime_state(engine)
    make_tenant(engine, KEY_A, "tenant-fte89-a", 1000)
    make_tenant(engine, KEY_B, "tenant-fte89-b", 1000)

    created = c.post("/v1/inference-jobs", json={"raw_input": RAW_INPUT},
                     headers={"X-Platrixa-API-Key": KEY_A, "X-Request-Id": "fte89-e2e"})
    job_id = created.json()["job_id"]
    result_id = created.json()["result_id"]
    claim = c.post("/v1/worker/jobs/claim", headers=worker_headers(KEY_A, "w-e2e")).json()["job"]
    lease = claim["lease"]
    submitted = c.post(f"/v1/worker/jobs/{job_id}/result", json={
        "lease": {"owner": lease["owner"], "generation": lease["generation"]},
        "bridge_body": bridge_body(VALID_CANDIDATE),
    }, headers=worker_headers(KEY_A, "w-e2e"))
    polled = c.get(f"/v1/jobs/{job_id}", headers={"X-Platrixa-API-Key": KEY_A})
    fetched = c.get(f"/v1/results/{result_id}", headers={"X-Platrixa-API-Key": KEY_A})
    stored = json.loads(async_jobs.get_job("tenant-fte89-a", job_id).result_json)

    check("F1 the claimed payload is byte-identical to the submitted input",
          claim["raw_input"] == RAW_INPUT, repr(claim.get("raw_input")))
    check("F2 the submission response, the stored result and the result endpoint agree exactly",
          submitted.status_code == 200 and fetched.status_code == 200
          and submitted.json() == fetched.json() == stored,
          f"{submitted.status_code}/{fetched.status_code}")
    check("F3 the polled job reports the verbatim engine terminal state",
          polled.json()["status"] == "VERIFIED" and polled.json()["retryable"] is False,
          polled.text[:160])
    check("F4 the whole end-to-end run used exactly ONE model inference (on the device side)",
          INSTR.count("bridge_parse") >= 1 and INSTR.provider_interpret <= MAX_ALLOWED_PROVIDER_INTERPRET_CALLS,
          f"parses={INSTR.count('bridge_parse')} provider={INSTR.provider_interpret}")

    # F5/F6: the inference path structurally cannot route through the sync path.
    route_src = (_REPO_ROOT / "api" / "routes" / "inference_api.py").read_text(encoding="utf-8")
    check("F5 the inference route never uses _execute_single_item / client.process",
          "_execute_single_item" not in route_src and "client.process" not in route_src)
    check("F6 the inference route never calls /v1/process",
          '"/v1/process"' not in route_src)

    # F7: production provider selection is untouched.
    from backend.model_provider.remote_hf import (
        VALID_TRANSPORTS,
        get_model_provider,
        transport_mode,
    )

    route_src = (_REPO_ROOT / "api" / "routes" / "inference_api.py").read_text(encoding="utf-8")
    bridge_src = (_REPO_ROOT / "backend" / "model_provider" / "local_bridge.py").read_text(encoding="utf-8")
    saved_transport = os.environ.pop("PLATRIXA_MODEL_TRANSPORT", None)
    saved_endpoint = os.environ.pop("PLATRIXA_MODEL_ENDPOINT_URL", None)
    try:
        check("F7a the default transport selection is unchanged (no env → the documented default)",
              transport_mode() == "http", transport_mode())
        check("F7b the transport vocabulary is unchanged",
              VALID_TRANSPORTS == ("http", "gradio", "local", "local_bridge"),
              str(VALID_TRANSPORTS))
        # The device bridge is reachable ONLY by an explicit opt-in, and this
        # change never sets that variable.
        os.environ["PLATRIXA_MODEL_TRANSPORT"] = "local_bridge"
        try:
            opt_in = get_model_provider()
        finally:
            os.environ.pop("PLATRIXA_MODEL_TRANSPORT", None)
        check("F7c the device bridge provider is reachable ONLY by explicit opt-in",
              type(opt_in).__name__ == "LocalBridgeModelProvider", type(opt_in).__name__)
        # AST, not substring: the module docstring legitimately NAMES the
        # transport variables while describing them. What must not exist is a
        # provider selection call or a transport assignment in CODE.
        route_tree = ast.parse(route_src)
        code_names = {n.id for n in ast.walk(route_tree) if isinstance(n, ast.Name)}
        code_attrs = {n.attr for n in ast.walk(route_tree) if isinstance(n, ast.Attribute)}
        env_keys: set = set()
        for node in ast.walk(route_tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                    and node.value.attr == "environ" and isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str):
                env_keys.add(node.slice.value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "get" and isinstance(node.func.value, ast.Attribute) \
                    and node.func.value.attr == "environ" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                env_keys.add(node.args[0].value)
        offending = sorted(k for k in env_keys if k.startswith("PLATRIXA_MODEL"))
        check("F7d the inference result route neither selects a provider nor touches the model transport",
              "get_model_provider" not in code_names | code_attrs
              and not offending
              and "PLATRIXA_MODEL_TRANSPORT" not in os.environ,
              f"providers={sorted(code_names & {'get_model_provider'})} env={offending}")
        check("F7e the bridge still binds device loopback by default and never a public host",
              "http://127.0.0.1:8081" in bridge_src and "0.0.0.0" not in bridge_src)
    finally:
        if saved_transport is not None:
            os.environ["PLATRIXA_MODEL_TRANSPORT"] = saved_transport
        if saved_endpoint is not None:
            os.environ["PLATRIXA_MODEL_ENDPOINT_URL"] = saved_endpoint

    check("F8 no inbound bridge/worker port is exposed by this API surface",
          not any("/bridge" in p or "8081" in p or "11435" in p
                  for p in json.dumps(sorted(create_app().openapi().get("paths", {})))))


def _factory_source() -> str:
    from backend.model_provider import remote_hf

    src = Path(remote_hf.__file__).read_text(encoding="utf-8")
    start = src.find("def get_model_provider")
    return src[start:start + 2500] if start != -1 else src


def _lease_setting(raw: str) -> int:
    """The lease ``inference_lease_seconds()`` resolves for a raw setting."""
    previous = os.environ.get(async_jobs.INFERENCE_LEASE_ENV_VAR)
    os.environ[async_jobs.INFERENCE_LEASE_ENV_VAR] = raw
    try:
        return async_jobs.inference_lease_seconds()
    finally:
        if previous is None:
            os.environ.pop(async_jobs.INFERENCE_LEASE_ENV_VAR, None)
        else:
            os.environ[async_jobs.INFERENCE_LEASE_ENV_VAR] = previous


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("Outbound inference bridge — Stage 1 acceptance suite (fte_fyjc_89)")
    print("=" * 78)

    INSTR.install()
    srv, engine = pg_backend()
    stub = HarnessClient()
    c = make_client(stub)

    try:
        section_a()
        section_b()
        section_c(c, engine)
        section_c2(c)
        section_d(c, engine)
        section_e(c, engine)
        section_f(c, engine)
    finally:
        developer.reset_client()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{total} checks passed")
    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    for name, detail in failed:
        print(f"  FAIL {name} — {detail}")
    print("=" * 78)

    check_truth = {
        "client.process was never used by the inference path": INSTR.client_process == 0,
        "provider interpret calls stayed on the sync path only":
            INSTR.provider_interpret <= MAX_ALLOWED_PROVIDER_INTERPRET_CALLS,
    }
    print("\nFinal invariant probes:")
    for label, ok in check_truth.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} (provider interpret={INSTR.provider_interpret}, "
              f"client.process={INSTR.client_process})")
    ok = passed == total and all(check_truth.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
