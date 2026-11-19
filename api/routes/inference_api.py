"""Platrixa — outbound inference bridge API (inference jobs + device worker).

THE ARCHITECTURE THIS MODULE IMPLEMENTS
=======================================

    Client → POST /v1/inference-jobs
        ↓ 202 {job_id, result_id, status_url, result_url}   ← polling handle
    Railway API: authenticate → idempotency claim → reservation → durable
                 inference job (kind='inference') — ALL BEFORE any inference
        ↓
    device worker → POST /v1/worker/jobs/claim
        ↓ 200 {job: {job_id, raw_input, lease{owner, generation}}}
    device worker → its OWN loopback bridge (127.0.0.1:8081 → llama-server)
        ↓ the UNMODIFIED bridge response body
        (lease heartbeat: POST /v1/worker/jobs/{job_id}/lease while running)
        ↓
    device worker → POST /v1/worker/jobs/{job_id}/result   (lease-fenced)
        ↓
    Railway deterministic Kernel.process_from_source()
        interpretation mapping → CandidateSemanticIR → schema validation →
        deterministic grounding → GroundedSemanticIR → deterministic
        accounting → downgrade-only rules → ExecutionEvidence → KernelResult
        ↓
    the authoritative 5D envelope is persisted and the job COMPLETED in ONE
    fenced statement; the SAME envelope is what GET /v1/results/{result_id}
    returns.

THE AUTHORITY RULE (unchanged, and the reason this module exists)
-----------------------------------------------------------------
The model PROPOSES an interpretation; Platrixa's deterministic pipeline
decides the authoritative outcome. A candidate that arrives from an
authenticated device worker is validated by the SAME code as an inline
provider result — there is no second, weaker validator and no path in which
device-reported metadata can create VERIFIED. `suggested_status ==
"VERIFIED"` is clamped to REVIEW_REQUIRED before validation, forbidden
accounting fields are rejected, truncated generation is rejected, and
grounding/accounting failures keep their real engine terminal states.

NO MACHINE CALLS ANOTHER MACHINE'S LOOPBACK
-------------------------------------------
The Railway API never calls the device bridge (a device's 127.0.0.1 is not
reachable from Railway, and port 8081 is never exposed). The device worker
reaches its own bridge over its own loopback and makes only OUTBOUND HTTPS
requests to this API.

WHAT IS *NOT* HERE
------------------
No production transport is switched: `PLATRIXA_MODEL_ENDPOINT_URL` /
`PLATRIXA_MODEL_TRANSPORT` selection and every existing route are untouched,
and the document worker still claims only document jobs. Inference jobs are
created only by an explicit POST to /v1/inference-jobs.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.results import build_process_result
from api.routes.async_api import (
    _async_not_ready,
    _job_urls,
    _transport_status_for,
)
from api.routes.developer import (
    _error_response,
    _get_client,
    _log,
    _record_request_metadata,
    _resolve_idempotency_tenant,
    _safe_accounting,
    _safe_candidate,
    _sanitize_request_id,
)
from api.schemas import DeveloperJobAcceptedResponse, KernelProcessRequest
from backend.auth import admission as admission_boundary
from backend.auth import async_jobs, batch_jobs, transaction_quota
from backend.auth import idempotency as idempotency_store
from backend.auth import queue_capacity

# NOTE: `backend.model_provider.local_bridge` (and therefore
# `backend.model_provider.remote_hf`, `requests`, …) is imported INSIDE the
# result handler, never at module scope. Importing this route module must not
# pull the model/provider graph into the API process: the health path is
# verified to load zero model/provider/torch modules in a clean interpreter
# (scripts/fte_fyjc_55_fastapi_boundary_test.py, check 26).

logger = logging.getLogger("platrixa.api")

router = APIRouter(tags=["developer-inference"])

API_VERSION = "v1"

# The route-level body bound for a worker result submission. Deliberately the
# same 64 KiB the /v1 app-level guard already enforces: a device bridge
# response (raw model text for <=512 generated tokens + envelope) is far
# smaller, so this is a fail-closed ceiling, not a target.
MAX_INFERENCE_RESULT_BYTES = 64 * 1024

# Worker identity is OPTIONAL input and is treated as untrusted: it is only
# ever a lease-owner label, never an authorization decision (the tenant always
# comes from the authenticated API key). Anything that is not a bounded
# identifier is discarded in favour of a server-generated owner.
_WORKER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
WORKER_ID_HEADER = "x-platrixa-worker-id"
LEASE_OWNER_MAX_LENGTH = 128


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _tenant_api_key_guard(request: Request) -> None:
    """Authenticate the caller with the EXISTING admission primitive.

    Identical to the async read/management guard: it authenticates WITHOUT
    reserving. Worker endpoints are not billable — the single admission unit
    for an inference job is reserved exactly once, at creation. A caller that
    fails here never reaches a handler, the queue, or the kernel.
    """
    from api.routes.developer import _gate_http_exception

    provided = request.headers.get("x-platrixa-api-key", "")
    reason, _adm = admission_boundary.authenticate_only(provided)
    if reason != admission_boundary.ADMIT_OK:
        raise _gate_http_exception(reason)


def _enforce_tenant_rate_limit(tenant: str):
    """Consume ONE durable per-tenant request unit for this call.

    Returns a response to send when the tenant is over its configured rate,
    or None when the call may continue. The counter lives in the metering
    store, so a restart or a second API process cannot reset or duplicate it
    — unlike the per-IP in-memory limiter in ``api/rate_limit.py``.

    A store failure fails closed (503): the queue itself is unreachable in
    that state, so admitting the request would only fail later and later.
    """
    from backend.auth import tenant_rate_limit
    from backend.auth.async_jobs import AsyncStoreUnavailableError

    if not tenant_rate_limit.enabled():
        return None
    try:
        tenant_rate_limit.consume(tenant)
    except tenant_rate_limit.TenantRateLimited as exc:
        response = _error_response(
            429,
            tenant_rate_limit.TENANT_RATE_LIMITED,
            "this tenant exceeded its request rate for the current window",
            request_id=None,
        )
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        response.headers["X-RateLimit-Limit"] = str(exc.limit)
        return response
    except AsyncStoreUnavailableError:
        return _async_not_ready(
            "ASYNC_UNAVAILABLE", None, "async store unavailable; request not admitted"
        )
    return None


def _sanitize_worker_id(value: Any) -> Optional[str]:
    """Return a bounded worker-identity label, or None when unusable."""
    raw = (value or "").strip() if isinstance(value, str) else ""
    if raw and _WORKER_ID_RE.match(raw):
        return raw
    return None


def _claim_owner(request: Request) -> str:
    """The lease owner for a NEW claim.

    A well-formed worker-supplied identity is used so a device can keep ONE
    stable lease name across claims; otherwise a unique per-claim owner is
    derived server-side. Either way the owner is a label, the tenant is
    server-derived, and stale writes are prevented by the lease GENERATION
    fence — never by trusting a header.
    """
    supplied = _sanitize_worker_id(request.headers.get(WORKER_ID_HEADER, ""))
    if supplied:
        return supplied
    return f"{async_jobs.worker_id()}-{secrets.token_hex(4)}"


def _lease_proof(body: Dict[str, Any]) -> Optional[tuple]:
    """Extract (owner, generation) from a worker submission, or None.

    Both values are REQUIRED for any commit or renewal. A missing/garbage
    proof is refused — there is no default and no fallback: an unproven
    caller is exactly the stale writer the fence exists to stop.
    """
    lease = body.get("lease")
    if not isinstance(lease, dict):
        return None
    owner = lease.get("owner")
    generation = lease.get("generation")
    if not isinstance(owner, str):
        return None
    owner = owner.strip()
    if not owner or len(owner) > LEASE_OWNER_MAX_LENGTH:
        return None
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        return None
    return owner, generation


# ---------------------------------------------------------------------------
# POST /v1/inference-jobs — create a durable inference job
# ---------------------------------------------------------------------------


@router.post(
    "/v1/inference-jobs",
    response_model=DeveloperJobAcceptedResponse,
    status_code=202,
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        202: {"description": "Inference job accepted for out-of-process processing (never a result)."},
        400: {"description": "ASYNC_NOT_CONFIGURED or a malformed/invalid request."},
        409: {"description": "Idempotency conflict — key reused with a different request."},
        413: {"description": "Request body exceeds the accepted size."},
        429: {"description": "Quota exhausted — job not admitted."},
        503: {"description": "Metering/async store unavailable — fail closed."},
    },
    openapi_extra={
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": False,
                "schema": {"type": "string", "minLength": 16, "maxLength": 200, "pattern": "^[A-Za-z0-9._~-]+$"},
                "description": (
                    "Optional. Same Phase 5C contract as POST /v1/process, scoped to the CREATION "
                    "request: same key + same raw_input returns the original job reference without a "
                    "duplicate job or extra quota; same key + different raw_input is a 409."
                ),
            },
            {
                "name": "X-Platrixa-API-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "description": "Tenant API key (operator-provisioned). Never logged or echoed.",
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["raw_input"],
                        "properties": {
                            "raw_input": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 2000,
                                "description": "Raw transaction text (identical contract to POST /v1/process).",
                            },
                        },
                    },
                    "example": {"raw_input": "Purchased furniture for cash Rs. 15,000"},
                }
            },
        },
    },
)
async def create_inference_job(request: Request) -> JSONResponse:
    """Submit transaction text for out-of-process (device) inference.

    202 means ADMITTED — it says nothing about the final status, which can be
    VERIFIED, REVIEW_REQUIRED, UNSUPPORTED, or FAILED. Poll
    ``GET /v1/jobs/{job_id}`` and fetch the authoritative envelope from
    ``GET /v1/results/{result_id}`` when the job completes.
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.perf_counter()

    if not async_jobs.async_configured():
        return _async_not_ready(
            "ASYNC_NOT_CONFIGURED",
            rid,
            "this deployment has no durable async store configured; use POST /v1/process",
        )

    # NOTE: pending-queue capacity is enforced ATOMICALLY inside
    # async_jobs.create_job (advisory lock + durable count + INSERT in one
    # transaction). A pre-flight check here could only race the insert and
    # would under-count jobs still waiting for a worker.

    # ---- parse + validate with the canonical request schema ---------------
    # KernelProcessRequest is the SAME schema POST /v1/process uses, so the
    # raw-input length constraints (1..2000) cannot drift between the two.
    try:
        raw_body = await request.body()
        body = json.loads(raw_body)
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)

    from pydantic import ValidationError

    # ---- contract selection: one transaction OR a bounded batch -----------
    # The presence of "transactions" is the switch. The single-transaction
    # body keeps its exact pre-batch meaning and constraints, so no existing
    # client has to change.
    mode = batch_jobs.MODE_SINGLE
    transactions: List[batch_jobs.BatchTransaction] = []
    if batch_jobs.is_batch_request(body):
        try:
            mode, transactions = batch_jobs.validate_request(
                body, body_bytes=len(raw_body)
            )
        except batch_jobs.BatchValidationError as exc:
            return _error_response(
                400, exc.code, exc.message, request_id=rid, details=exc.details
            )
        # Not used by the batch path (each transaction keeps its own input);
        # assigned so the type stays non-optional below.
        raw_input = transactions[0].input
    else:
        try:
            validated = KernelProcessRequest(**body)
        except ValidationError:
            # Field paths/types only — never the caller's raw values.
            return _error_response(
                400,
                "REQUEST_MALFORMED",
                "request body could not be parsed as a valid inference request (raw_input is required, 1-2000 characters)",
                request_id=rid,
            )
        raw_input = validated.raw_input

    # ---- tenant scope (server-derived; authenticate-only, never charges) --
    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; request not admitted")
    limited = _enforce_tenant_rate_limit(tenant)
    if limited is not None:
        return limited

    # ---- idempotency FIRST (Phase 5C ordering) ----------------------------
    # The key is claimed BEFORE the reservation, so a replay never charges and
    # a conflicting reuse is rejected before any unit is spent.
    idem_key_raw = request.headers.get("idempotency-key")
    idem_key: Optional[str] = None
    # Batch idempotency is per BATCH: the fingerprint covers the mode and every
    # (transaction_id, input) pair, so the same key with a changed batch is a
    # 409 (never a silently different job) and an identical replay returns the
    # original job reference without charging either quota again.
    fingerprint = (
        {"raw_input": raw_input}
        if mode == batch_jobs.MODE_SINGLE
        else batch_jobs.job_payload(mode, transactions)
    )
    if idem_key_raw is not None:
        ok, key_code = idempotency_store.validate_key(idem_key_raw)
        if not ok:
            return _error_response(400, key_code, idempotency_store.KEY_FORMAT_MESSAGE, request_id=rid)
        idem_key = idem_key_raw.strip()
        try:
            outcome = idempotency_store.claim(idem_key, tenant, "/v1/inference-jobs", fingerprint)
        except idempotency_store.IdempotencyUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; request not admitted")
        if outcome.replay:
            env = outcome.envelope or {}
            data_part = env.get("data", env)
            status_url, result_url = _job_urls(
                data_part.get("job_id", ""), data_part.get("result_id", "")
            )
            _log("/v1/inference-jobs", request_id=rid, status=None,
                 duration_ms=int((time.perf_counter() - started) * 1000), error="IDEMPOTENT_REPLAY")
            return JSONResponse(
                status_code=202,
                content={
                    "api_version": API_VERSION,
                    "request_id": env.get("request_id") or rid,
                    "job_id": data_part.get("job_id", ""),
                    "result_id": data_part.get("result_id", ""),
                    "status": "PROCESSING",
                    "status_label": "Processing",
                    "status_url": status_url,
                    "result_url": result_url,
                    "created_at": data_part.get("created_at") or "",
                    **{
                        key: data_part[key]
                        for key in ("mode", "transaction_count", "batch_url")
                        if key in data_part
                    },
                },
                headers={
                    "Idempotent-Replayed": "true",
                    "Idempotency-Key": idempotency_store.idempotency_key_hash(idem_key)[:8],
                },
            )
        if outcome.conflict:
            return _error_response(
                409,
                idempotency_store.CONFLICT_CODE,
                "this Idempotency-Key was already used with a different request",
                request_id=rid,
            )
        if outcome.processing:
            return JSONResponse(
                status_code=202,
                content={
                    "api_version": API_VERSION,
                    "request_id": outcome.request_id or rid,
                    "status": "PROCESSING",
                    "status_label": "Processing",
                    "retryable": True,
                    "reason_code": idempotency_store.IN_PROGRESS_CODE,
                },
                headers={"Idempotent-Replayed": "false", "Retry-After": "2"},
            )

    # ---- quota reservation (canonical attempts only) ----------------------
    # Exactly ONE admission call on this route: after the idempotency claim
    # (replays never reach this line), BEFORE durable job creation.
    provided = request.headers.get("x-platrixa-api-key", "")
    reason, adm = admission_boundary.admit(provided, reserve=True)
    if reason != admission_boundary.ADMIT_OK:
        if idem_key:
            idempotency_store.release(idem_key, tenant)
        from api.routes.developer import _gate_http_exception

        raise _gate_http_exception(reason)
    admitted_ctx = (adm.tenant_id, adm.key_prefix)

    # ---- ACCOUNTING-TRANSACTION quota (a SEPARATE, deterministic unit) ----
    # One job admits N transactions, so charging only the per-job quota would
    # let a developer buy N units of device inference for one unit of quota.
    # Charged exactly once here, after the idempotency claim (a replay never
    # reaches this line) and before the job row exists.
    transaction_units = len(transactions) if mode != batch_jobs.MODE_SINGLE else 1
    try:
        transaction_quota.reserve(tenant, transaction_units)
    except transaction_quota.TransactionQuotaExceeded as exc:
        if idem_key:
            idempotency_store.release(idem_key, tenant)
        response = _error_response(
            429,
            transaction_quota.TRANSACTION_QUOTA_EXCEEDED,
            "this tenant's monthly accounting-transaction allowance is exhausted",
            request_id=rid,
            details={"limit": exc.limit, "used": exc.used, "requested": exc.requested},
        )
        response.headers["X-Transaction-Quota-Limit"] = str(exc.limit)
        response.headers["X-Transaction-Quota-Remaining"] = str(exc.remaining)
        return response
    except async_jobs.AsyncStoreUnavailableError:
        if idem_key:
            idempotency_store.release(idem_key, tenant)
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; request not admitted")

    # ---- durable inference job (BEFORE any inference happens) -------------
    create_kwargs: Dict[str, Any] = {}
    if mode != batch_jobs.MODE_SINGLE:
        create_kwargs["batch"] = {
            "mode": mode,
            "transactions": [
                {"transaction_id": tx.transaction_id, "input": tx.input}
                for tx in transactions
            ],
        }
    try:
        record = async_jobs.create_job(
            tenant,
            (
                {"raw_input": raw_input}
                if mode == batch_jobs.MODE_SINGLE
                else batch_jobs.job_payload(mode, transactions)
            ),
            request_id=rid or None,
            kind=async_jobs.JOB_KIND_INFERENCE,
            **create_kwargs,
        )
    except queue_capacity.QueueCapacityExceeded as exc:
        # Queue full: the admission transaction rolled back, so no job row and
        # no capacity reservation exist. Release the idempotency claim so the
        # client can retry the same key once capacity frees up.
        if idem_key:
            idempotency_store.release(idem_key, tenant)
        _release_transaction_units(tenant, transaction_units)
        _log("/v1/inference-jobs", request_id=rid, status=None,
             duration_ms=int((time.perf_counter() - started) * 1000), error=exc.code)
        return _async_not_ready(exc.code, rid, "pending queue capacity exhausted; retry later")
    except async_jobs.AsyncStoreUnavailableError:
        if idem_key:
            idempotency_store.release(idem_key, tenant)
        _release_transaction_units(tenant, transaction_units)
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; request not admitted")

    # NOTE: the in-process document worker is deliberately NOT started here.
    # It claims only kind='document'; an inference job is claimed exclusively
    # by a device worker through POST /v1/worker/jobs/claim. Starting it would
    # be a no-op side effect on this path.

    status_url, result_url = _job_urls(record.job_id, record.result_id)
    created_at = record.created_at.isoformat() if record.created_at else ""
    accepted = {
        "api_version": API_VERSION,
        "request_id": rid or None,
        "job_id": record.job_id,
        "result_id": record.result_id,
        "status": "PROCESSING",
        "status_label": "Processing",
        "status_url": status_url,
        "result_url": result_url,
        "created_at": created_at,
    }
    if mode != batch_jobs.MODE_SINGLE:
        accepted["mode"] = mode
        accepted["transaction_count"] = len(transactions)
        accepted["batch_url"] = f"/v1/results/{record.result_id}/transactions"

    if idem_key:
        try:
            idempotency_store.complete(
                idem_key,
                tenant,
                "/v1/inference-jobs",
                fingerprint,
                request_id=rid or None,
                envelope={"data": accepted},
                http_status=202,
            )
        except idempotency_store.IdempotencyUnavailableError:
            logger.warning("inference idempotency record failed (non-fatal)")

    _log("/v1/inference-jobs", request_id=rid, status="ACCEPTED",
         duration_ms=int((time.perf_counter() - started) * 1000), error=None)
    _record_request_metadata(
        admitted_ctx,
        http_status=202,
        api_status="PROCESSING",
        reason_code=None,
        request_id=rid or None,
        duration_ms=int((time.perf_counter() - started) * 1000),
        endpoint="/v1/inference-jobs",
    )
    return JSONResponse(
        status_code=202,
        content=accepted,
        headers={"Idempotent-Replayed": "false"} if idem_key else None,
    )


# ---------------------------------------------------------------------------
# POST /v1/worker/jobs/claim — claim ONE inference job for THIS tenant
# ---------------------------------------------------------------------------


@router.post(
    "/v1/worker/jobs/claim",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "One claimed job with its lease, or an explicit no-work response."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed) or not configured."},
    },
    openapi_extra={
        "parameters": [
            {
                "name": "X-Platrixa-API-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "description": "Tenant API key. Determines the tenant scope of the claim (never client-supplied).",
            },
            {
                "name": "X-Platrixa-Worker-Id",
                "in": "header",
                "required": False,
                "schema": {"type": "string", "maxLength": 128},
                "description": "Optional lease-owner label (untrusted; sanitized). Ignored if not a bounded identifier.",
            },
        ],
    },
)
async def claim_inference_job(request: Request) -> JSONResponse:
    """Atomically claim at most ONE runnable inference job for this tenant.

    The claim is BOTH tenant-scoped (from the authenticated key) and
    kind-scoped (kind='inference'), and it returns the job's payload from the
    same statement that claims it — so there is no unscoped payload lookup by
    job id anywhere on this path.

    A no-work response is explicit and carries no information about other
    tenants' jobs (not even their existence).
    """
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", None, "this deployment has no durable async store configured")

    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", None, "async store unavailable")

    limited = _enforce_tenant_rate_limit(tenant)
    if limited is not None:
        return limited

    owner = _claim_owner(request)
    # Opportunistic sweep: a job whose retry budget is spent and whose lease has
    # lapsed can never be claimed again, so without this it would sit in
    # PROCESSING forever. Tenant-scoped, expiry-gated and budget-gated; a store
    # failure here is not fatal because the claim below reports it honestly.
    try:
        async_jobs.fail_exhausted_leases(
            tenant_id=tenant, kind=async_jobs.JOB_KIND_INFERENCE
        )
    except async_jobs.AsyncStoreUnavailableError:
        logger.warning("inference claim: exhausted-lease sweep skipped (store unavailable)")
    # Inference lease: configurable, provisional engineering default (see
    # async_jobs.inference_lease_seconds). The worker heartbeats while its
    # device model runs, and a lost lease can never commit.
    lease_seconds = async_jobs.inference_lease_seconds()
    try:
        record = async_jobs.claim_next_job(
            lease_seconds=lease_seconds,
            owner=owner,
            kind=async_jobs.JOB_KIND_INFERENCE,
            tenant_id=tenant,
        )
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", None, "async store unavailable")

    if record is None:
        return JSONResponse(
            status_code=200,
            content={
                "api_version": API_VERSION,
                "status": "NO_WORK",
                "job": None,
                # Bounded pacing hint only — the worker must not spin.
                "retry_after_seconds": float(max(1, async_jobs.LEASE_RENEW_FRACTION)),
            },
        )

    payload: Dict[str, Any] = {}
    if record.request_json:
        try:
            parsed = json.loads(record.request_json)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}

    lease_block = {
        "owner": record.lease_owner,
        "generation": int(record.lease_generation),
        "lease_seconds": lease_seconds,
        # Renew this often (the documented renew cadence); do not
        # wait for the lease to approach expiry.
        "renew_every_seconds": max(
            1, int(lease_seconds / async_jobs.LEASE_RENEW_FRACTION)
        ),
        "renew_url": f"/v1/worker/jobs/{record.job_id}/lease",
        "result_url": f"/v1/worker/jobs/{record.job_id}/result",
    }

    mode = str(
        getattr(record, "batch_mode", batch_jobs.MODE_SINGLE) or batch_jobs.MODE_SINGLE
    )

    if mode != batch_jobs.MODE_SINGLE:
        # Batch claim: only the transactions that are still PENDING are
        # returned, so a reclaimed job resumes instead of re-running work whose
        # outcome is already committed. Inputs come from the SERVER's stored
        # payload, never from the claim request.
        try:
            rows = batch_jobs.rows_for_job(tenant, record.job_id)
        except async_jobs.AsyncStoreUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", None, "async store unavailable")
        stored = batch_jobs.stored_transactions(payload)
        pending = batch_jobs.pending_rows(rows)
        claimed: List[Dict[str, Any]] = []
        for row in pending:
            item = stored.get(row.transaction_id) or {}
            tx_input = item.get("input")
            if not isinstance(tx_input, str) or not tx_input.strip():
                _fail_inference_job(
                    record, "INPUT_MISSING", "stored batch transaction has no input"
                )
                return _error_response(500, "INTERNAL_ERROR", "internal error", request_id=None)
            claimed.append(
                {
                    "transaction_id": row.transaction_id,
                    "batch_index": row.batch_index,
                    "input": tx_input,
                }
            )
        if not claimed:
            # Every transaction is already final; the next submission will
            # finalize the job. Never hand back an empty work unit silently.
            _fail_inference_job(
                record, "INPUT_MISSING", "batch has no pending transaction"
            )
            return _error_response(500, "INTERNAL_ERROR", "internal error", request_id=None)

        prior_state: Optional[Dict[str, Any]] = None
        for row in rows:
            if not row.state_summary:
                continue
            try:
                parsed_summary = json.loads(row.state_summary)
            except Exception:
                parsed_summary = None
            if isinstance(parsed_summary, dict):
                prior_state = parsed_summary

        return JSONResponse(
            status_code=200,
            content={
                "api_version": API_VERSION,
                "status": "CLAIMED",
                "job": {
                    "job_id": record.job_id,
                    "request_id": record.request_id,
                    "mode": mode,
                    "transaction_count": int(getattr(record, "transaction_count", len(claimed)) or len(claimed)),
                    "transactions": claimed,
                    "batch_budget_seconds": float(batch_jobs.limits()["budget_seconds"]),
                    "transaction_result_url": (
                        f"/v1/worker/jobs/{record.job_id}/transactions/{{transaction_id}}/result"
                    ),
                    "prior_state": prior_state,
                    "prior_state_hash": batch_jobs.last_state_hash(rows),
                    "lease": lease_block,
                },
            },
        )

    raw_input = payload.get("raw_input")
    if not isinstance(raw_input, str) or not raw_input.strip():
        # Defensive: a stored inference job without raw_input cannot be
        # processed. Fail it permanently (fenced) rather than looping it
        # through claims forever, and say so honestly.
        _fail_inference_job(record, "INPUT_MISSING", "stored inference job has no raw_input")
        return _error_response(500, "INTERNAL_ERROR", "internal error", request_id=None)

    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "status": "CLAIMED",
            "job": {
                "job_id": record.job_id,
                "request_id": record.request_id,
                "raw_input": raw_input,
                "lease": lease_block,
            },
        },
    )


# ---------------------------------------------------------------------------
# POST /v1/worker/jobs/{job_id}/lease — heartbeats (never charges quota)
# ---------------------------------------------------------------------------


@router.post(
    "/v1/worker/jobs/{job_id}/lease",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "Lease renewed for the current owner/generation."},
        404: {"description": "JOB_NOT_FOUND — no such inference job for this tenant (non-disclosing)."},
        409: {"description": "LEASE_LOST — the caller no longer holds the current lease, or LEASE_EXPIRED — its lease already lapsed and cannot be renewed."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
)
async def renew_inference_lease(job_id: str, request: Request) -> JSONResponse:
    """Extend the CURRENT lease, proving owner + generation.

    Renewal is conditional on still owning the lease, so a worker that has
    already been fenced out can never restore stale ownership. A refused
    renewal is a clear LEASE_LOST response: the worker must stop and discard
    its work. This endpoint never reserves quota.
    """
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", None, "this deployment has no durable async store configured")

    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=None)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=None)

    proof = _lease_proof(body)
    if proof is None:
        return _error_response(400, "REQUEST_MALFORMED", "lease proof requires owner and generation", request_id=None)
    owner, generation = proof

    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", None, "async store unavailable")

    try:
        record = async_jobs.get_job(tenant, job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", None, "async store unavailable")
    # Tenant ownership is checked BEFORE anything else, and a foreign job is
    # indistinguishable from a nonexistent one.
    if record is None or record.kind != async_jobs.JOB_KIND_INFERENCE:
        return _error_response(404, "JOB_NOT_FOUND", "no such job for this tenant", request_id=None)

    lease_seconds = async_jobs.inference_lease_seconds()
    try:
        renewed = async_jobs.renew_lease(
            job_id,
            lease_owner=owner,
            lease_generation=generation,
            lease_seconds=lease_seconds,
        )
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", None, "async store unavailable")

    if not renewed:
        # Expiry is a real revocation (renewal cannot resurrect it), so name it
        # separately from "another worker owns this job now".
        try:
            reason = async_jobs.lease_commit_refusal_reason(
                job_id, lease_owner=owner, lease_generation=generation
            )
        except async_jobs.AsyncStoreUnavailableError:
            reason = None
        if reason == "EXPIRED":
            return _error_response(
                409,
                "LEASE_EXPIRED",
                "this worker's lease expired; it cannot be renewed (claim the job again)",
                request_id=None,
            )
        return _error_response(409, "LEASE_LOST", "the lease is no longer held at that owner/generation", request_id=None)

    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "job_id": job_id,
            "renewed": True,
            "lease": {
                "owner": owner,
                "generation": generation,
                "lease_seconds": lease_seconds,
                "renew_every_seconds": max(
                    1, int(lease_seconds / async_jobs.LEASE_RENEW_FRACTION)
                ),
            },
        },
    )


# ---------------------------------------------------------------------------
# POST /v1/worker/jobs/{job_id}/result — submit the device candidate
# ---------------------------------------------------------------------------


@router.post(
    "/v1/worker/jobs/{job_id}/result",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "The authoritative 5D envelope (engine terminal state verbatim)."},
        400: {"description": "Malformed submission (missing lease proof / bridge_body)."},
        404: {"description": "JOB_NOT_FOUND — no such inference job for this tenant (non-disclosing)."},
        409: {"description": "LEASE_LOST (stale/unproven worker) or LEASE_EXPIRED (this worker's own lease lapsed before the commit point) — nothing was committed."},
        413: {"description": "REQUEST_TOO_LARGE — submission exceeds the accepted size."},
        415: {"description": "CONTENT_TYPE_UNSUPPORTED — the body must be application/json."},
        422: {"description": "The deterministic pipeline's own terminal state was VALIDATION_FAILED / GROUNDING_FAILED / FORBIDDEN_OUTPUT / UNSUPPORTED_TRANSACTION."},
        503: {"description": "ASYNC_UNAVAILABLE (store down) or MODEL_UNAVAILABLE (unexpected runtime failure)."},
    },
    openapi_extra={
        "parameters": [
            {
                "name": "X-Platrixa-API-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "description": "Tenant API key (the worker's credential). Never logged or echoed.",
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["lease", "bridge_body"],
                        "properties": {
                            "lease": {
                                "type": "object",
                                "required": ["owner", "generation"],
                                "properties": {
                                    "owner": {"type": "string", "maxLength": 128},
                                    "generation": {"type": "integer", "minimum": 0},
                                },
                            },
                            "bridge_body": {
                                "type": "object",
                                "description": "The UNMODIFIED device bridge response body (e.g. {interpretation_text, completion_status, model}).",
                            },
                            "request_id": {"type": "string", "description": "Optional correlation id (never used as the input)."},
                        },
                    },
                }
            },
        },
    },
)
async def submit_inference_result(job_id: str, request: Request) -> JSONResponse:
    """Validate a device-produced candidate and persist the authoritative result.

    The submitted ``bridge_body`` goes through the SAME shared contract parser
    the synchronous bridge provider uses, and the resulting interpretation is
    fed to ``Kernel.process_from_source`` — the SAME deterministic pipeline
    (schema validation → grounding → grounded IR → accounting → rules →
    evidence). No Railway model provider is resolved or called here.

    The stored input is the SERVER's own ``raw_input`` for the job; a
    client-supplied input is never substituted. The commit is lease-fenced,
    so a stale worker cannot overwrite the result of the worker that holds
    the current lease, and a duplicate submission returns the stored envelope
    without re-running the kernel.

    Stage 2 O1: the fence also requires the lease to be UNEXPIRED on the
    server's own clock at the commit point, so an expired lease cannot
    authorize a commit merely because nobody has reclaimed the job yet.
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.perf_counter()

    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "this deployment has no durable async store configured")

    # ---- transport-shape validation BEFORE any expensive work -------------
    ctype = (request.headers.get("content-type", "") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return _error_response(
            415, "CONTENT_TYPE_UNSUPPORTED", "result submissions must be application/json", request_id=rid
        )
    try:
        raw_body = await request.body()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be read", request_id=rid)
    if not raw_body:
        return _error_response(400, "REQUEST_MALFORMED", "request body is required", request_id=rid)
    if len(raw_body) > MAX_INFERENCE_RESULT_BYTES:
        return _error_response(
            413, "REQUEST_TOO_LARGE", "result submission exceeds the accepted size", request_id=rid
        )
    try:
        body = json.loads(raw_body)
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)

    proof = _lease_proof(body)
    if proof is None:
        return _error_response(400, "REQUEST_MALFORMED", "lease proof requires owner and generation", request_id=rid)
    owner, generation = proof

    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    try:
        record = async_jobs.get_job(tenant, job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    # Tenant- AND kind-scoped: a foreign job, a nonexistent job, and a document
    # job are all the same non-disclosing 404.
    if record is None or record.kind != async_jobs.JOB_KIND_INFERENCE:
        return _error_response(404, "JOB_NOT_FOUND", "no such job for this tenant", request_id=rid)

    # ---- batch jobs use the batch submission contract ---------------------
    if str(
        getattr(record, "batch_mode", batch_jobs.MODE_SINGLE) or batch_jobs.MODE_SINGLE
    ) != batch_jobs.MODE_SINGLE:
        return await _submit_batch_job(record, request, body, owner, generation, rid)

    if "bridge_body" not in body:
        return _error_response(400, "REQUEST_MALFORMED", "bridge_body is required", request_id=rid)
    bridge_body = body.get("bridge_body")

    # ---- duplicate submission: the STORED result, no kernel, no overwrite --
    if record.status == async_jobs.STATUS_COMPLETED and record.result_json:
        try:
            stored = json.loads(record.result_json)
        except Exception:
            stored = None
        if isinstance(stored, dict):
            return JSONResponse(
                status_code=record.http_status or 200,
                content=stored,
                headers={"Idempotent-Replayed": "true"},
            )

    if record.status != async_jobs.STATUS_PROCESSING:
        return _error_response(
            409, "LEASE_LOST", "the job is not currently leased for processing", request_id=rid
        )
    if (record.lease_owner or "") != owner or int(record.lease_generation or 0) != generation:
        return _error_response(
            409, "LEASE_LOST", "the lease is no longer held at that owner/generation", request_id=rid
        )
    # NOTE: this pre-check deliberately does NOT judge EXPIRY. Lease freshness
    # is decided by the server's own clock inside the fenced commit statement
    # (async_jobs.complete_job), which is atomic with the write; comparing a
    # Python timestamp here would be a second, non-authoritative clock.

    # ---- the SERVER's stored input (never a client-supplied one) ----------
    try:
        payload_json = async_jobs.get_job_request_json(tenant, job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    raw_input: Optional[str] = None
    if payload_json:
        try:
            stored_payload = json.loads(payload_json)
            if isinstance(stored_payload, dict):
                candidate_input = stored_payload.get("raw_input")
                if isinstance(candidate_input, str):
                    raw_input = candidate_input
        except Exception:
            raw_input = None
    if not raw_input or not raw_input.strip():
        _fail_inference_job(record, "INPUT_MISSING", "stored inference job has no raw_input")
        return _error_response(500, "INTERNAL_ERROR", "internal error", request_id=rid)

    content, transport_status, failure = _run_kernel_candidate(
        request,
        record,
        raw_input,
        bridge_body,
        rid,
        owner=owner,
        generation=generation,
        started=started,
    )

    # MODEL_UNAVAILABLE on THIS path means the server could not validate the
    # submission at all: the device candidate parsed and entered the pipeline,
    # so there is no model-availability problem here — it is an unexpected
    # server-side failure whose outcome is UNKNOWN. That is a RETRYABLE job
    # failure under the repository's existing vocabulary
    # (async_jobs.classify_failure), recorded with the fenced fail path and
    # bounded by the job's attempt budget — never committed as a terminal
    # result, and never presented to the client as an authoritative outcome.
    if failure == "MODEL_UNAVAILABLE":
        _fail_inference_job(
            record, "MODEL_UNAVAILABLE", "the submission could not be validated on the server"
        )
        return _error_response(
            503,
            "MODEL_UNAVAILABLE",
            "the submission could not be validated; retry later",
            request_id=rid,
        )
    if content is None or transport_status is None:
        logger.warning("inference result: service configuration invalid")
        return _error_response(503, "PROVIDER_UNAVAILABLE", "service configuration invalid", request_id=rid)

    # ---- fenced commit: result + terminal status in ONE statement ---------
    try:
        accepted = async_jobs.complete_job(
            job_id,
            envelope=content,
            http_status=transport_status,
            request_id=content.get("request_id") or record.request_id,
            lease_owner=owner,
            lease_generation=generation,
        )
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; result not persisted")
    if not accepted:
        # Fenced out (superseded generation, lost the race to a concurrent
        # submission, or this worker's own lease expired). Never claim a commit
        # that did not happen: surface the stored authoritative result when one
        # exists, else name the refusal.
        try:
            latest = async_jobs.get_job(tenant, job_id)
        except async_jobs.AsyncStoreUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
        if latest is not None and latest.status == async_jobs.STATUS_COMPLETED and latest.result_json:
            try:
                stored = json.loads(latest.result_json)
            except Exception:
                stored = None
            if isinstance(stored, dict):
                return JSONResponse(
                    status_code=latest.http_status or 200,
                    content=stored,
                    headers={"Idempotent-Replayed": "true"},
                )
        # The commit already refused; this only NAMES the reason so the worker
        # can tell "someone else owns this job now" (LEASE_LOST) from "my own
        # lease ran out" (LEASE_EXPIRED). Both are 409 with nothing stored.
        try:
            reason = async_jobs.lease_commit_refusal_reason(
                job_id, lease_owner=owner, lease_generation=generation
            )
        except async_jobs.AsyncStoreUnavailableError:
            reason = None
        if reason == "EXPIRED":
            return _error_response(
                409,
                "LEASE_EXPIRED",
                "this worker's lease expired before the commit point; nothing was stored",
                request_id=rid,
            )
        return _error_response(409, "LEASE_LOST", "the lease was lost before commit; nothing was stored", request_id=rid)

    api_status = content.get("api_status")
    _record_request_metadata(
        (record.tenant_id, None),
        http_status=transport_status,
        api_status=api_status,
        reason_code=(content.get("reason_codes") or [None])[0]
        if isinstance(content.get("reason_codes"), list)
        else None,
        request_id=content.get("request_id") or rid,
        duration_ms=int((time.perf_counter() - started) * 1000),
        endpoint="/v1/inference-jobs(worker)",
    )
    _log(
        "/v1/inference-jobs(worker)",
        request_id=rid,
        status=api_status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        error=None,
    )
    return JSONResponse(status_code=transport_status, content=content)


# ---------------------------------------------------------------------------
# POST /v1/worker/jobs/{job_id}/failure — worker reports an unusable generation
# ---------------------------------------------------------------------------


# The ONLY failure codes a worker may report. Every one already exists in the
# server's vocabulary (backend/auth/async_jobs.py PERMANENT/RETRYABLE sets), so
# `classify_failure` — not the caller — decides retryability. An unknown code is
# a malformed request, never an invented failure class.
#: The only release reasons a worker may report. A closed vocabulary keeps the
#: release path from becoming a free-text channel, and none of these can affect
#: a result: the transition only returns the row to QUEUED.
WORKER_RELEASE_REASONS = frozenset({"SLOT_BUSY", "SHUTDOWN", "LEASE_YIELD"})

WORKER_REPORTABLE_FAILURE_CODES = frozenset({
    "MODEL_UNAVAILABLE",
    "PROVIDER_UNAVAILABLE",
    "INTERNAL_ERROR",
    "VALIDATION_FAILED",
    "UNAUTHORIZED",
    "REQUEST_MALFORMED",
})

MAX_FAILURE_MESSAGE_CHARS = 200


@router.post(
    "/v1/worker/jobs/{job_id}/failure",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "The failure was recorded; the job is RETRY_WAIT (budget left) or FAILED (terminal)."},
        400: {"description": "Malformed report (missing lease proof, or an unsupported failure code)."},
        404: {"description": "JOB_NOT_FOUND — no such inference job for this tenant (non-disclosing)."},
        409: {"description": "LEASE_LOST (stale/unproven worker, or the job already has an authoritative result) — nothing was written."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
    openapi_extra={
        "parameters": [
            {
                "name": "X-Platrixa-API-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "description": "Tenant API key. Determines the tenant scope (never client-supplied).",
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["lease", "error"],
                        "properties": {
                            "lease": {
                                "type": "object",
                                "required": ["owner", "generation"],
                                "properties": {
                                    "owner": {"type": "string", "maxLength": 128},
                                    "generation": {"type": "integer", "minimum": 0},
                                },
                            },
                            "error": {
                                "type": "object",
                                "required": ["code"],
                                "properties": {
                                    "code": {"type": "string", "maxLength": 64},
                                    "message": {"type": "string", "maxLength": 200},
                                },
                            },
                        },
                    },
                }
            },
        },
    },
)
async def report_inference_failure(job_id: str, request: Request) -> JSONResponse:
    """Record that THIS generation produced no submittable candidate.

    Why this route exists: without it a failed model call leaves the job in
    ``PROCESSING`` until its lease lapses, and once the attempt budget is spent
    nothing can ever claim it again — the job is stranded permanently. This lets
    the worker say so, and the server then applies its OWN existing policy:
    ``classify_failure`` decides retryable vs permanent, and ``fail_job`` moves
    the job to ``RETRY_WAIT`` (budget remaining, exponential backoff) or
    terminal ``FAILED``.

    What it can never do:

      * write a result — ``result_json`` is untouched, no kernel runs, no
        ``bridge_body`` is accepted, so a failed generation can never become an
        authoritative outcome;
      * touch a completed job — an existing authoritative result is a 409;
      * act on a newer generation — the write is fenced on owner + generation;
      * consume quota or metering;
      * be steered by the caller on retryability — the client cannot send it.
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))

    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "this deployment has no durable async store configured")

    ctype = (request.headers.get("content-type", "") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return _error_response(
            415, "CONTENT_TYPE_UNSUPPORTED", "failure reports must be application/json", request_id=rid
        )
    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)

    proof = _lease_proof(body)
    if proof is None:
        return _error_response(400, "REQUEST_MALFORMED", "lease proof requires owner and generation", request_id=rid)
    owner, generation = proof

    error = body.get("error")
    if not isinstance(error, dict):
        return _error_response(400, "REQUEST_MALFORMED", "error must be a JSON object", request_id=rid)
    code = error.get("code")
    if not isinstance(code, str) or code not in WORKER_REPORTABLE_FAILURE_CODES:
        # An unsupported code is a contract error, not something to coerce.
        return _error_response(
            400,
            "REQUEST_MALFORMED",
            "error.code must be one of the supported worker failure codes",
            request_id=rid,
        )
    raw_message = error.get("message")
    # The worker's message is a DIAGNOSTIC, never authority: bounded, single-line,
    # and stored only inside the error envelope (never in a result).
    message = " ".join(str(raw_message or "").split())[:MAX_FAILURE_MESSAGE_CHARS]

    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    try:
        record = async_jobs.get_job(tenant, job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    # Tenant- AND kind-scoped: foreign, nonexistent and document jobs are all the
    # same non-disclosing 404.
    if record is None or record.kind != async_jobs.JOB_KIND_INFERENCE:
        return _error_response(404, "JOB_NOT_FOUND", "no such job for this tenant", request_id=rid)

    # An authoritative result already exists: a failure report must never be able
    # to sit beside it (or worse, replace its status).
    if record.status == async_jobs.STATUS_COMPLETED:
        return _error_response(
            409, "LEASE_LOST", "the job already has an authoritative result", request_id=rid
        )
    if record.status != async_jobs.STATUS_PROCESSING:
        # Already RETRY_WAIT / FAILED (e.g. a retried report): report the stored
        # state instead of failing it a second time. Idempotent, not an error.
        return JSONResponse(
            status_code=200,
            content={
                "api_version": API_VERSION,
                "job_id": job_id,
                "status": record.status,
                "retryable": bool(record.retryable),
                "attempts": int(record.attempt_count or 0),
                "max_attempts": int(record.max_attempts or 0),
            },
        )

    if (record.lease_owner or "") != owner or int(record.lease_generation or 0) != generation:
        return _error_response(
            409, "LEASE_LOST", "the lease is no longer held at that owner/generation", request_id=rid
        )

    from api.status import public_status_for_error_code

    api_status = public_status_for_error_code(code)
    retryable = async_jobs.classify_failure(error_code=code, api_status=api_status)
    envelope = {
        "api_version": API_VERSION,
        "error": {
            "code": code,
            "message": message or "the worker could not produce a submittable candidate",
            "request_id": record.request_id,
            "api_status": api_status,
            "retryable": retryable,
        },
    }

    try:
        accepted = async_jobs.fail_job(
            job_id,
            envelope=envelope,
            http_status=503 if retryable else 400,
            retryable=retryable,
            lease_owner=owner,
            lease_generation=generation,
        )
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; failure not recorded")
    if not accepted:
        # Fenced out (superseded generation, or the job left PROCESSING first).
        return _error_response(
            409, "LEASE_LOST", "the lease was lost before the failure was recorded", request_id=rid
        )

    # Mirrors fail_job's own branch so the answer names the state it just wrote.
    status = (
        async_jobs.STATUS_RETRY_WAIT
        if retryable and int(record.attempt_count or 0) < int(record.max_attempts or 0)
        else async_jobs.STATUS_FAILED
    )
    _log(
        "/v1/inference-jobs(worker-failure)",
        request_id=rid,
        status=status,
        duration_ms=0,
        error=None,
    )
    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "job_id": job_id,
            "status": status,
            "retryable": retryable,
            "attempts": int(record.attempt_count or 0),
            "max_attempts": int(record.max_attempts or 0),
        },
    )


@router.post(
    "/v1/worker/jobs/{job_id}/release",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "The claim was returned to the queue (attempt budget refunded), or the job was already final."},
        400: {"description": "Malformed release request (missing lease proof or unsupported reason)."},
        404: {"description": "JOB_NOT_FOUND — no such inference job for this tenant (non-disclosing)."},
        409: {"description": "LEASE_LOST — the caller no longer owns the lease; nothing was changed."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
)
async def release_inference_claim(job_id: str, request: Request) -> JSONResponse:
    """Return a claimed job to the queue because the DEVICE could not run it.

    The device-wide slot limiter is shared by every tenant worker, so a claim
    can legitimately arrive while the phone is busy. This endpoint exists so
    that situation costs NOTHING: the job goes back to ``QUEUED``, its lease is
    cleared, and the attempt the claim consumed is refunded. Without it a busy
    phone would burn a job's retry budget on another tenant's generation.

    It can never write a result, change a status to a terminal one, or act on a
    job it no longer owns (the write is fenced on owner + generation + an
    unexpired lease).
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "this deployment has no durable async store configured")
    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)

    proof = _lease_proof(body)
    if proof is None:
        return _error_response(400, "REQUEST_MALFORMED", "lease proof requires owner and generation", request_id=rid)
    owner, generation = proof
    reason = body.get("reason")
    if reason is not None and reason not in WORKER_RELEASE_REASONS:
        return _error_response(
            400,
            "REQUEST_MALFORMED",
            "reason must be one of the supported release reasons",
            request_id=rid,
        )

    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    try:
        record = async_jobs.get_job(tenant, job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    if record is None or record.kind != async_jobs.JOB_KIND_INFERENCE:
        return _error_response(404, "JOB_NOT_FOUND", "no such job for this tenant", request_id=rid)

    if record.status != async_jobs.STATUS_PROCESSING:
        # Idempotent: the job already left PROCESSING (maybe another attempt is
        # running). Report the stored state instead of rewriting it.
        return JSONResponse(
            status_code=200,
            content={
                "api_version": API_VERSION,
                "job_id": job_id,
                "status": record.status,
                "released": False,
                "attempts": int(record.attempt_count or 0),
                "max_attempts": int(record.max_attempts or 0),
            },
        )
    if (record.lease_owner or "") != owner or int(record.lease_generation or 0) != generation:
        return _error_response(
            409, "LEASE_LOST", "the lease is no longer held at that owner/generation", request_id=rid
        )

    try:
        released = async_jobs.release_claim(
            job_id, lease_owner=owner, lease_generation=generation
        )
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; claim not released")
    if not released:
        return _batch_lease_refusal(record, rid, owner, generation)

    _log(
        "/v1/inference-jobs(worker-release)",
        request_id=rid,
        status="QUEUED",
        duration_ms=0,
        error=None,
    )
    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "job_id": job_id,
            "status": async_jobs.STATUS_QUEUED,
            "released": True,
            "reason": reason,
            "attempts": int(record.attempt_count or 0),
            "max_attempts": int(record.max_attempts or 0),
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fail_inference_job(record: async_jobs.JobRecord, code: str, message: str) -> None:
    """Permanently fail an inference job (fenced on the claim's ownership).

    Used only for server-side inconsistency (a stored inference job whose
    payload is unreadable). ``classify_failure`` makes this non-retryable by
    construction, so the job cannot loop through claims; a fenced-out worker
    records nothing.
    """
    from api.status import LABEL_BY_PUBLIC_STATUS, public_status_for_error_code

    api_status = public_status_for_error_code(code)
    retryable = async_jobs.classify_failure(error_code=code, api_status=api_status)
    envelope = {
        "api_version": API_VERSION,
        "error": {
            "code": code,
            "message": message,
            "request_id": record.request_id,
            "api_status": api_status,
            "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
            "retryable": retryable,
        },
    }
    try:
        async_jobs.fail_job(
            record.job_id,
            envelope=envelope,
            http_status=503 if retryable else 400,
            retryable=retryable,
            lease_owner=record.lease_owner,
            lease_generation=record.lease_generation,
        )
    except async_jobs.AsyncStoreUnavailableError:
        logger.warning("inference job %s failure store write failed", record.job_id[:14])


# ---------------------------------------------------------------------------
# Multi-transaction batch pipeline
# ---------------------------------------------------------------------------

#: The per-transaction states the kernel can produce. Anything else (a
#: transport-level error) is recorded as FAILED with its own error code, so an
#: unexpected value can never masquerade as a good outcome.
_TX_VOCAB = frozenset(
    {
        batch_jobs.TX_VERIFIED,
        batch_jobs.TX_REVIEW_REQUIRED,
        batch_jobs.TX_UNSUPPORTED,
        batch_jobs.TX_INVALID_INPUT,
        batch_jobs.TX_FAILED,
    }
)


def _tx_status(api_status: Optional[str]) -> str:
    return api_status if api_status in _TX_VOCAB else batch_jobs.TX_FAILED


def _release_transaction_units(tenant: str, units: int) -> None:
    """Best-effort refund for a job that was never created.

    The charge and the job row are separate statements (the quota lives in its
    own table), so this is the ONE path that gives units back: the create failed,
    therefore no job exists to spend them. A refund failure is logged and left
    charged — never silently swallowed into "the job exists".
    """
    if int(units) <= 0:
        return
    try:
        transaction_quota.release(tenant, int(units))
    except Exception as exc:
        logger.warning(
            "transaction quota release failed (%s); the units stay charged",
            type(exc).__name__,
        )


def _run_kernel_candidate(
    request: Request,
    record: async_jobs.JobRecord,
    raw_input: str,
    bridge_body: Any,
    rid: Optional[str],
    *,
    owner: str,
    generation: int,
    started: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
    """Validate ONE device candidate through the shared deterministic pipeline.

    Returns ``(envelope, transport_status, failure)`` where ``failure`` is
    ``None`` on success, ``"MODEL_UNAVAILABLE"`` when the server could not
    validate the submission at all (outcome UNKNOWN, retryable) and
    ``"PROVIDER_UNAVAILABLE"`` when the service is misconfigured.

    This is the SAME pipeline the single-transaction route uses, extracted so a
    batch transaction cannot get a different (weaker) validator. The model's
    suggestion is never authority: ``process_from_source`` clamps a
    model-suggested VERIFIED, applies grounding and capability rules, and only
    the kernel decides the terminal state.
    """
    # Lazy import: keeps the model/provider import graph out of the API process
    # unless a device result is actually submitted (see module note).
    from backend.model_provider.local_bridge import (
        device_reported_model_identity,
        interpretation_from_bridge_body,
    )

    model_id, provider_revision = device_reported_model_identity(bridge_body)

    def _interpret():
        """Parse the device candidate through the SHARED bridge contract.

        Raising here is intentional: the Kernel's existing exception mapping
        turns MalformedOutputError into VALIDATION_FAILED and
        ForbiddenAccountingFieldError into FORBIDDEN_OUTPUT, so a bad device
        candidate keeps the engine's real terminal state instead of a
        transport-invented one.
        """
        return interpretation_from_bridge_body(
            bridge_body,
            raw_input,
            model_id=model_id,
            provider_revision=provider_revision,
            generated_profile={
                "source": "device-bridge-submission",
                "artifact_identity": provider_revision,
                "lease_owner": owner,
                "lease_generation": generation,
            },
        )

    try:
        client = _get_client(request)
        kernel = client.kernel()
    except Exception:
        logger.warning("inference result: service configuration invalid")
        return None, None, "PROVIDER_UNAVAILABLE"

    # process_from_source performs NO provider resolution and NO inference: the
    # in-process model is never loaded or called on this path.
    kernel_result = kernel.process_from_source(
        raw_input,
        _interpret,
        request_id=rid or record.request_id or None,
    )

    engine_status = kernel_result.status
    if engine_status == "MODEL_UNAVAILABLE":
        return None, None, "MODEL_UNAVAILABLE"

    transport_status = _transport_status_for(engine_status)
    content = build_process_result(
        request_id=getattr(kernel_result, "request_id", None),
        engine_status=engine_status,
        engine_status_label=getattr(kernel_result, "status_label", "") or engine_status,
        next_action=getattr(kernel_result, "next_action", None),
        issues=list(getattr(kernel_result, "issues", None) or []),
        grounding_issues=list(getattr(kernel_result, "grounding_issues", None) or []),
        rule_evidence=list(getattr(kernel_result, "rule_evidence", None) or []),
        # KernelResult owns ``interpretation_candidate`` (the 18-field dict) and
        # ``accounting_result``; the projections are taken from THOSE fields so
        # the envelope carries the same substance the synchronous path does.
        interpretation=_safe_candidate(getattr(kernel_result, "interpretation_candidate", None)),
        accounting=_safe_accounting(getattr(kernel_result, "accounting_result", None)),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    # ADDITIVE provenance: the kernel's execution-evidence chain (recorded ==
    # actually executed). No existing 5D field is changed.
    evidence = getattr(kernel_result, "evidence", None)
    if isinstance(content.get("metadata"), dict):
        content["metadata"]["execution_evidence"] = (
            evidence.to_dict()
            if evidence is not None and hasattr(evidence, "to_dict")
            else None
        )
    return content, transport_status, None


def _worker_failure_envelope(
    record: async_jobs.JobRecord, transaction_id: str, code: str, message: str
) -> Tuple[str, Dict[str, Any]]:
    """The stored per-transaction envelope for a worker-reported failure.

    It records a DIAGNOSTIC, never an accounting outcome: no accounting, no
    interpretation, no authoritative result. Retryability comes from the
    server's existing ``classify_failure`` vocabulary, never from the worker.
    """
    from api.status import LABEL_BY_PUBLIC_STATUS, public_status_for_error_code

    api_status = public_status_for_error_code(code)
    retryable = async_jobs.classify_failure(error_code=code, api_status=api_status)
    return api_status, {
        "api_version": API_VERSION,
        "request_id": record.request_id,
        "status": api_status,
        "api_status": api_status,
        "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
        "engine_status": None,
        "success": False,
        "retryable": retryable,
        "reason_codes": [code],
        "issues": [],
        "grounding_issues": [],
        "interpretation": None,
        "accounting": None,
        "error": {"code": code, "message": message, "request_id": record.request_id},
        "metadata": {
            "authority": "worker-reported-failure",
            "transaction_id": transaction_id,
        },
    }


def _batch_lease_refusal(
    record: async_jobs.JobRecord, rid: Optional[str], owner: str, generation: int
) -> JSONResponse:
    """Name WHY a batch write was refused; nothing was stored."""
    try:
        reason = async_jobs.lease_commit_refusal_reason(
            record.job_id, lease_owner=owner, lease_generation=generation
        )
    except async_jobs.AsyncStoreUnavailableError:
        reason = None
    if reason == "EXPIRED":
        return _error_response(
            409,
            "LEASE_EXPIRED",
            "this worker's lease expired before the batch commit point; nothing was stored",
            request_id=rid,
        )
    return _error_response(
        409,
        "LEASE_LOST",
        "the lease was lost before the batch commit point; nothing was stored",
        request_id=rid,
    )


def _load_batch_context(
    tenant: str, record: async_jobs.JobRecord
) -> Tuple[Dict[str, Dict[str, Any]], List[batch_jobs.BatchTransactionRecord]]:
    """The SERVER's stored inputs plus the current transaction rows."""
    payload_json = async_jobs.get_job_request_json(tenant, record.job_id)
    stored: Dict[str, Dict[str, Any]] = {}
    if payload_json:
        try:
            parsed = json.loads(payload_json)
            if isinstance(parsed, dict):
                stored = batch_jobs.stored_transactions(parsed)
        except Exception:
            stored = {}
    return stored, batch_jobs.rows_for_job(tenant, record.job_id)


async def _submit_batch_job(
    record: async_jobs.JobRecord,
    request: Request,
    body: Dict[str, Any],
    owner: str,
    generation: int,
    rid: Optional[str],
) -> JSONResponse:
    """INDEPENDENT batch submission: every candidate in ONE fenced call.

    Partial outcomes are explicit: each transaction is validated on its own and
    stored on its own row, so one failure never rewrites another's outcome.
    Transactions the worker did not submit (budget exhausted, or an explicit
    ``deferred: true``) are recorded as DEFERRED with ``BATCH_BUDGET_EXHAUSTED``
    — a partial outcome, not a failure verdict.
    """
    tenant = record.tenant_id
    mode = str(
        getattr(record, "batch_mode", batch_jobs.MODE_SINGLE) or batch_jobs.MODE_SINGLE
    )
    if mode == batch_jobs.MODE_ORDERED:
        return _error_response(
            400,
            "BATCH_MODE_ORDERED_REQUIRES_TRANSACTION_SUBMISSION",
            "an ordered batch submits one transaction at a time to "
            "/v1/worker/jobs/{job_id}/transactions/{transaction_id}/result",
            request_id=rid,
        )

    # ---- replay: the stored authoritative envelope, no kernel -------------
    if record.status == async_jobs.STATUS_COMPLETED and record.result_json:
        try:
            stored_envelope = json.loads(record.result_json)
        except Exception:
            stored_envelope = None
        if isinstance(stored_envelope, dict):
            return JSONResponse(
                status_code=record.http_status or 200,
                content=stored_envelope,
                headers={"Idempotent-Replayed": "true"},
            )

    if record.status != async_jobs.STATUS_PROCESSING:
        return _error_response(
            409, "LEASE_LOST", "the job is not currently leased for processing", request_id=rid
        )
    if (record.lease_owner or "") != owner or int(record.lease_generation or 0) != generation:
        return _error_response(
            409, "LEASE_LOST", "the lease is no longer held at that owner/generation", request_id=rid
        )

    submitted = body.get("transactions")
    if not isinstance(submitted, list) or not submitted:
        return _error_response(
            400, "REQUEST_MALFORMED", "transactions must be a non-empty list", request_id=rid
        )

    try:
        stored, rows = _load_batch_context(tenant, record)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    by_id = {row.transaction_id: row for row in rows}

    # ---- validate the WHOLE submission before running any kernel ---------
    normalized: List[Tuple[str, Dict[str, Any]]] = []
    seen: set = set()
    for index, item in enumerate(submitted):
        if not isinstance(item, dict):
            return _error_response(
                400,
                "BATCH_TRANSACTION_INVALID",
                f"transactions[{index}] must be an object",
                request_id=rid,
                details={"index": index},
            )
        tx_id = item.get("transaction_id")
        if not isinstance(tx_id, str) or tx_id not in by_id:
            return _error_response(
                400,
                batch_jobs.BATCH_UNKNOWN_TRANSACTION,
                f"transactions[{index}].transaction_id is not part of this batch",
                request_id=rid,
                details={"index": index},
            )
        if tx_id in seen:
            return _error_response(
                400,
                "BATCH_DUPLICATE_TRANSACTION_ID",
                "a transaction may be submitted at most once per batch",
                request_id=rid,
                details={"duplicate_transaction_ids": [tx_id]},
            )
        seen.add(tx_id)
        normalized.append((tx_id, item))

    # ---- per-transaction validation + fenced write -----------------------
    for tx_id, item in normalized:
        row = by_id[tx_id]
        if row.status != batch_jobs.TX_PENDING:
            # Already final: a processed outcome is NEVER overwritten (this is
            # what makes a reclaimed job resume instead of re-deciding).
            continue
        if item.get("deferred") is True:
            continue

        error = item.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            if not isinstance(code, str) or code not in WORKER_REPORTABLE_FAILURE_CODES:
                return _error_response(
                    400,
                    "REQUEST_MALFORMED",
                    "error.code must be one of the supported worker failure codes",
                    request_id=rid,
                    details={"transaction_id": tx_id},
                )
            message = " ".join(str(error.get("message") or "").split())[
                :MAX_FAILURE_MESSAGE_CHARS
            ]
            api_status, failure_envelope = _worker_failure_envelope(
                record, tx_id, code, message
            )
            accepted_row = batch_jobs.record_transaction(
                tenant_id=tenant,
                job_id=record.job_id,
                transaction_id=tx_id,
                status=_tx_status(api_status),
                api_status=api_status,
                engine_status=None,
                error_code=code,
                envelope=failure_envelope,
                http_status=503 if failure_envelope.get("retryable") else 400,
                lease_owner=owner,
                lease_generation=generation,
            )
            if not accepted_row:
                return _batch_lease_refusal(record, rid, owner, generation)
            continue

        bridge_body = item.get("bridge_body")
        if not isinstance(bridge_body, dict):
            return _error_response(
                400,
                "REQUEST_MALFORMED",
                "each transaction needs bridge_body or error",
                request_id=rid,
                details={"transaction_id": tx_id},
            )

        tx_input = (stored.get(tx_id) or {}).get("input")
        if not isinstance(tx_input, str) or not tx_input.strip():
            _fail_inference_job(
                record, "INPUT_MISSING", "stored batch transaction has no input"
            )
            return _error_response(500, "INTERNAL_ERROR", "internal error", request_id=rid)

        content, transport_status, failure = _run_kernel_candidate(
            request,
            record,
            tx_input,
            bridge_body,
            rid,
            owner=owner,
            generation=generation,
            started=time.perf_counter(),
        )
        if failure == "MODEL_UNAVAILABLE":
            # Outcome UNKNOWN for this submission: record NO per-transaction
            # verdict, fail the job through the existing bounded retry path.
            _fail_inference_job(
                record,
                "MODEL_UNAVAILABLE",
                "a batch transaction could not be validated on the server",
            )
            return _error_response(
                503,
                "MODEL_UNAVAILABLE",
                "the submission could not be validated; retry later",
                request_id=rid,
            )
        if content is None or transport_status is None:
            logger.warning("inference result: service configuration invalid")
            return _error_response(
                503, "PROVIDER_UNAVAILABLE", "service configuration invalid", request_id=rid
            )

        api_status = content.get("api_status")
        reason_codes = content.get("reason_codes") or []
        error_code = reason_codes[0] if isinstance(reason_codes, list) and reason_codes else None
        # INDEPENDENT mode has no accounting sequence, so no state chain is
        # recorded: a hash fed to no one would imply an ordering guarantee the
        # mode does not make.
        accepted_row = batch_jobs.record_transaction(
            tenant_id=tenant,
            job_id=record.job_id,
            transaction_id=tx_id,
            status=_tx_status(api_status),
            api_status=api_status,
            engine_status=content.get("status"),
            error_code=None if _tx_status(api_status) == batch_jobs.TX_VERIFIED else error_code,
            envelope=content,
            http_status=transport_status,
            prior_state_hash=None,
            state_hash=None,
            state_summary=None,
            lease_owner=owner,
            lease_generation=generation,
        )
        if not accepted_row:
            return _batch_lease_refusal(record, rid, owner, generation)

    # ---- anything the worker did not submit is DEFERRED, explicitly ------
    try:
        remaining = batch_jobs.pending_rows(batch_jobs.rows_for_job(tenant, record.job_id))
        if remaining:
            batch_jobs.mark_pending(
                tenant_id=tenant,
                job_id=record.job_id,
                from_index=remaining[0].batch_index,
                status=batch_jobs.TX_DEFERRED,
                error_code=batch_jobs.DEFERRED_CODE,
                lease_owner=owner,
                lease_generation=generation,
            )
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    try:
        accepted, envelope = batch_jobs.finalize(
            record, lease_owner=owner, lease_generation=generation
        )
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; result not persisted")
    if not accepted or envelope is None:
        return _batch_lease_refusal(record, rid, owner, generation)

    _log(
        "/v1/inference-jobs(worker-batch)",
        request_id=rid,
        status=envelope.get("api_status"),
        duration_ms=0,
        error=None,
    )
    return JSONResponse(status_code=200, content=envelope)


def _ordered_state_response(
    record: async_jobs.JobRecord,
    rows: List[batch_jobs.BatchTransactionRecord],
    current: batch_jobs.BatchTransactionRecord,
    rid: Optional[str],
) -> JSONResponse:
    """The interim answer for an ordered chain that has more work to do."""
    nxt = batch_jobs.next_pending(rows)
    prior_state: Optional[Dict[str, Any]] = None
    if current.state_summary:
        try:
            parsed = json.loads(current.state_summary)
            if isinstance(parsed, dict):
                prior_state = parsed
        except Exception:
            prior_state = None
    processed = len([row for row in rows if row.status in batch_jobs.TX_PROCESSED])
    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "job_id": record.job_id,
            "mode": batch_jobs.MODE_ORDERED,
            "transaction": batch_jobs.transaction_summary(current, record.result_id),
            "next": None
            if nxt is None
            else {
                "transaction_id": nxt.transaction_id,
                "batch_index": nxt.batch_index,
                "prior_state_hash": current.state_hash,
                "prior_state": prior_state,
            },
            "batch": {
                "total_transactions": len(rows),
                "processed_transactions": processed,
                "remaining_transactions": len(rows) - processed,
            },
        },
    )


@router.post(
    "/v1/worker/jobs/{job_id}/transactions/{transaction_id}/result",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "The transaction's outcome, plus the approved state for the next transaction (or the finalized batch envelope)."},
        400: {"description": "Malformed submission, unknown transaction, or an ordered batch used the wrong route."},
        404: {"description": "JOB_NOT_FOUND / BATCH_TRANSACTION_NOT_FOUND (non-disclosing)."},
        409: {"description": "LEASE_LOST / LEASE_EXPIRED / BATCH_ORDER_VIOLATION — nothing was stored."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
)
async def submit_batch_transaction_result(
    job_id: str, transaction_id: str, request: Request
) -> JSONResponse:
    """Submit ONE transaction of an ORDERED batch, in order.

    The body is ``{"lease": {...}, "bridge_body": {...}}`` normally, or
    ``{"lease": {...}, "error": {"code", "message"}}`` when this generation
    produced nothing submittable, or ``{"lease": {...}, "deferred": true}``
    when the device budget ran out. The server validates the candidate with the
    SAME kernel pipeline and returns the APPROVED prior state for the next
    transaction. A transaction that is not VERIFIED halts the chain: every later
    transaction becomes BLOCKED (``BLOCKED_BY_PRIOR_UNVERIFIED_STATE``) and never
    receives device inference.
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.perf_counter()

    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "this deployment has no durable async store configured")

    ctype = (request.headers.get("content-type", "") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return _error_response(
            415, "CONTENT_TYPE_UNSUPPORTED", "result submissions must be application/json", request_id=rid
        )
    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)

    proof = _lease_proof(body)
    if proof is None:
        return _error_response(400, "REQUEST_MALFORMED", "lease proof requires owner and generation", request_id=rid)
    owner, generation = proof

    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    try:
        record = async_jobs.get_job(tenant, job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    if record is None or record.kind != async_jobs.JOB_KIND_INFERENCE:
        return _error_response(404, "JOB_NOT_FOUND", "no such job for this tenant", request_id=rid)

    mode = str(
        getattr(record, "batch_mode", batch_jobs.MODE_SINGLE) or batch_jobs.MODE_SINGLE
    )
    if mode != batch_jobs.MODE_ORDERED:
        return _error_response(
            400,
            "BATCH_MODE_NOT_ORDERED",
            "this job is not an ordered batch; independent batches submit all transactions to "
            "/v1/worker/jobs/{job_id}/result",
            request_id=rid,
        )

    if record.status == async_jobs.STATUS_COMPLETED and record.result_json:
        try:
            stored_envelope = json.loads(record.result_json)
        except Exception:
            stored_envelope = None
        if isinstance(stored_envelope, dict):
            return JSONResponse(
                status_code=record.http_status or 200,
                content=stored_envelope,
                headers={"Idempotent-Replayed": "true"},
            )

    if record.status != async_jobs.STATUS_PROCESSING:
        return _error_response(
            409, "LEASE_LOST", "the job is not currently leased for processing", request_id=rid
        )
    if (record.lease_owner or "") != owner or int(record.lease_generation or 0) != generation:
        return _error_response(
            409, "LEASE_LOST", "the lease is no longer held at that owner/generation", request_id=rid
        )

    try:
        stored, rows = _load_batch_context(tenant, record)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    by_id = {row.transaction_id: row for row in rows}
    row = by_id.get(transaction_id)
    if row is None:
        return _error_response(
            404,
            batch_jobs.BATCH_UNKNOWN_TRANSACTION,
            "no such transaction in this batch",
            request_id=rid,
        )

    if row.status != batch_jobs.TX_PENDING:
        # Idempotent replay of an already-recorded transaction: report it and
        # the next step. Nothing is rewritten.
        return _ordered_state_response(record, rows, row, rid)

    pending = batch_jobs.next_pending(rows)
    if pending is None or pending.transaction_id != transaction_id:
        return _error_response(
            409,
            batch_jobs.ORDER_VIOLATION_CODE,
            "an ordered batch must be submitted in batch order",
            request_id=rid,
            details={
                "expected_transaction_id": pending.transaction_id if pending else None
            },
        )

    # ---- budget exhausted: defer this and every later transaction --------
    if body.get("deferred") is True:
        try:
            batch_jobs.mark_pending(
                tenant_id=tenant,
                job_id=record.job_id,
                from_index=row.batch_index,
                status=batch_jobs.TX_DEFERRED,
                error_code=batch_jobs.DEFERRED_CODE,
                lease_owner=owner,
                lease_generation=generation,
            )
        except async_jobs.AsyncStoreUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
        try:
            accepted, envelope = batch_jobs.finalize(
                record, lease_owner=owner, lease_generation=generation
            )
        except async_jobs.AsyncStoreUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; result not persisted")
        if not accepted or envelope is None:
            return _batch_lease_refusal(record, rid, owner, generation)
        return JSONResponse(status_code=200, content=envelope)

    # ---- this generation produced no candidate ---------------------------
    error = body.get("error")
    if error is not None:
        code = error.get("code") if isinstance(error, dict) else None
        if not isinstance(code, str) or code not in WORKER_REPORTABLE_FAILURE_CODES:
            return _error_response(
                400,
                "REQUEST_MALFORMED",
                "error.code must be one of the supported worker failure codes",
                request_id=rid,
            )
        message = " ".join(str(error.get("message") or "").split())[:MAX_FAILURE_MESSAGE_CHARS]
        api_status, failure_envelope = _worker_failure_envelope(record, transaction_id, code, message)
        accepted_row = batch_jobs.record_transaction(
            tenant_id=tenant,
            job_id=record.job_id,
            transaction_id=transaction_id,
            status=_tx_status(api_status),
            api_status=api_status,
            engine_status=None,
            error_code=code,
            prior_state_hash=batch_jobs.last_state_hash(rows),
            envelope=failure_envelope,
            http_status=503 if failure_envelope.get("retryable") else 400,
            lease_owner=owner,
            lease_generation=generation,
        )
        if not accepted_row:
            return _batch_lease_refusal(record, rid, owner, generation)
        # A link that produced no candidate cannot carry the sequence forward.
        try:
            batch_jobs.mark_pending(
                tenant_id=tenant,
                job_id=record.job_id,
                from_index=row.batch_index + 1,
                status=batch_jobs.TX_BLOCKED,
                error_code=batch_jobs.BLOCKED_CODE,
                lease_owner=owner,
                lease_generation=generation,
            )
            accepted, envelope = batch_jobs.finalize(
                record, lease_owner=owner, lease_generation=generation
            )
        except async_jobs.AsyncStoreUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; result not persisted")
        if not accepted or envelope is None:
            return _batch_lease_refusal(record, rid, owner, generation)
        return JSONResponse(status_code=200, content=envelope)

    bridge_body = body.get("bridge_body")
    if not isinstance(bridge_body, dict):
        return _error_response(
            400, "REQUEST_MALFORMED", "bridge_body is required", request_id=rid
        )

    tx_input = (stored.get(transaction_id) or {}).get("input")
    if not isinstance(tx_input, str) or not tx_input.strip():
        _fail_inference_job(
            record, "INPUT_MISSING", "stored batch transaction has no input"
        )
        return _error_response(500, "INTERNAL_ERROR", "internal error", request_id=rid)

    prior_state_hash = batch_jobs.last_state_hash(rows)
    content, transport_status, failure = _run_kernel_candidate(
        request,
        record,
        tx_input,
        bridge_body,
        rid,
        owner=owner,
        generation=generation,
        started=started,
    )
    if failure == "MODEL_UNAVAILABLE":
        _fail_inference_job(
            record,
            "MODEL_UNAVAILABLE",
            "a batch transaction could not be validated on the server",
        )
        return _error_response(
            503,
            "MODEL_UNAVAILABLE",
            "the submission could not be validated; retry later",
            request_id=rid,
        )
    if content is None or transport_status is None:
        logger.warning("inference result: service configuration invalid")
        return _error_response(
            503, "PROVIDER_UNAVAILABLE", "service configuration invalid", request_id=rid
        )

    api_status = content.get("api_status")
    reason_codes = content.get("reason_codes") or []
    error_code = reason_codes[0] if isinstance(reason_codes, list) and reason_codes else None
    state_hash = batch_jobs.state_hash_for(
        prior_state_hash=prior_state_hash,
        index=row.batch_index,
        transaction_id=transaction_id,
        envelope=content,
    )
    state_summary = (
        batch_jobs.state_summary_for(
            transaction_id=transaction_id, state_hash=state_hash, envelope=content
        )
        if state_hash
        else None
    )
    accepted_row = batch_jobs.record_transaction(
        tenant_id=tenant,
        job_id=record.job_id,
        transaction_id=transaction_id,
        status=_tx_status(api_status),
        api_status=api_status,
        engine_status=content.get("status"),
        error_code=None if _tx_status(api_status) == batch_jobs.TX_VERIFIED else error_code,
        prior_state_hash=prior_state_hash,
        state_hash=state_hash,
        state_summary=state_summary,
        envelope=content,
        http_status=transport_status,
        lease_owner=owner,
        lease_generation=generation,
    )
    if not accepted_row:
        return _batch_lease_refusal(record, rid, owner, generation)

    if state_hash is None:
        # The kernel did not approve this link. Dependent transactions must not
        # run on an unapproved state, so they are BLOCKED deterministically —
        # with no device inference spent on them.
        try:
            batch_jobs.mark_pending(
                tenant_id=tenant,
                job_id=record.job_id,
                from_index=row.batch_index + 1,
                status=batch_jobs.TX_BLOCKED,
                error_code=batch_jobs.BLOCKED_CODE,
                lease_owner=owner,
                lease_generation=generation,
            )
            accepted, envelope = batch_jobs.finalize(
                record, lease_owner=owner, lease_generation=generation
            )
        except async_jobs.AsyncStoreUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; result not persisted")
        if not accepted or envelope is None:
            return _batch_lease_refusal(record, rid, owner, generation)
        return JSONResponse(status_code=200, content=envelope)

    try:
        refreshed = batch_jobs.rows_for_job(tenant, record.job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    updated = {r.transaction_id: r for r in refreshed}.get(transaction_id) or row

    if batch_jobs.next_pending(refreshed) is None:
        try:
            accepted, envelope = batch_jobs.finalize(
                record, lease_owner=owner, lease_generation=generation
            )
        except async_jobs.AsyncStoreUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; result not persisted")
        if not accepted or envelope is None:
            return _batch_lease_refusal(record, rid, owner, generation)
        return JSONResponse(status_code=200, content=envelope)

    return _ordered_state_response(record, refreshed, updated, rid)


# ---------------------------------------------------------------------------
# Batch result reads (tenant-scoped, non-disclosing)
# ---------------------------------------------------------------------------


def _batch_result_record(tenant: str, result_id: str) -> Tuple[Optional[async_jobs.JobRecord], Optional[JSONResponse]]:
    try:
        record = async_jobs.get_result_record(tenant, result_id)
    except async_jobs.AsyncStoreUnavailableError:
        return None, _async_not_ready("ASYNC_UNAVAILABLE", None, "async store unavailable")
    if record is None or record.kind != async_jobs.JOB_KIND_INFERENCE:
        return None, _error_response(404, "RESULT_NOT_FOUND", "no such result for this tenant", request_id=None)
    if str(getattr(record, "batch_mode", batch_jobs.MODE_SINGLE) or batch_jobs.MODE_SINGLE) == batch_jobs.MODE_SINGLE:
        return None, _error_response(
            400, "NOT_A_BATCH_JOB", "this result is not a multi-transaction batch", request_id=None
        )
    return record, None


@router.get(
    "/v1/results/{result_id}/transactions",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "Per-transaction summaries, in batch order (paginated)."},
        400: {"description": "NOT_A_BATCH_JOB."},
        404: {"description": "RESULT_NOT_FOUND (unknown id for this tenant)."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
)
def list_batch_transactions(
    result_id: str, request: Request, limit: int = 50, offset: int = 0
) -> JSONResponse:
    """Per-transaction summaries for ONE batch result the tenant owns.

    The full envelope for a single transaction is fetched from its own
    ``result_url``, so this listing stays bounded no matter how large the
    batch's individual envelopes are.
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "this deployment has no durable async store configured")
    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    record, error = _batch_result_record(tenant, result_id)
    if error is not None:
        return error
    assert record is not None

    try:
        rows = batch_jobs.rows_for_job(tenant, record.job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    bounded_limit = max(1, min(int(limit or 50), 200))
    bounded_offset = max(0, int(offset or 0))
    page = rows[bounded_offset : bounded_offset + bounded_limit]
    return JSONResponse(
        status_code=200,
        content={
            "api_version": API_VERSION,
            "result_id": record.result_id,
            "job_id": record.job_id,
            "mode": str(getattr(record, "batch_mode", batch_jobs.MODE_INDEPENDENT) or batch_jobs.MODE_INDEPENDENT),
            "job_status": record.status,
            "total": len(rows),
            "limit": bounded_limit,
            "offset": bounded_offset,
            "transactions": [
                batch_jobs.transaction_summary(row, record.result_id) for row in page
            ],
        },
    )


@router.get(
    "/v1/results/{result_id}/transactions/{transaction_id}",
    dependencies=[Depends(_tenant_api_key_guard)],
    responses={
        200: {"description": "The transaction's own authoritative envelope."},
        400: {"description": "NOT_A_BATCH_JOB."},
        404: {"description": "RESULT_NOT_FOUND / BATCH_TRANSACTION_NOT_FOUND / RESULT_NOT_READY (non-disclosing)."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
)
def get_batch_transaction_result(
    result_id: str, transaction_id: str, request: Request
) -> JSONResponse:
    """ONE transaction's authoritative envelope, tenant-scoped."""
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "this deployment has no durable async store configured")
    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    record, error = _batch_result_record(tenant, result_id)
    if error is not None:
        return error
    assert record is not None

    try:
        row = batch_jobs.get_transaction(tenant, record.job_id, transaction_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    if row is None:
        return _error_response(
            404,
            batch_jobs.BATCH_UNKNOWN_TRANSACTION,
            "no such transaction in this batch",
            request_id=rid,
        )
    if not row.result_json:
        return _error_response(
            404,
            "RESULT_NOT_READY",
            "this transaction has no authoritative result yet; poll the job status",
            request_id=rid,
        )
    try:
        envelope = json.loads(row.result_json)
    except Exception:
        return _error_response(500, "INTERNAL_ERROR", "internal error", request_id=rid)
    return JSONResponse(
        status_code=row.http_status or 200,
        content=envelope,
        headers={"Idempotent-Replayed": "false"},
    )
