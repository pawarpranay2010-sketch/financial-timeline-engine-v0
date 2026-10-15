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
from typing import Any, Dict, Optional

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
from backend.auth import async_jobs
from backend.auth import idempotency as idempotency_store

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

    # ---- parse + validate with the canonical request schema ---------------
    # KernelProcessRequest is the SAME schema POST /v1/process uses, so the
    # raw-input length constraints (1..2000) cannot drift between the two.
    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)

    from pydantic import ValidationError

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

    # ---- idempotency FIRST (Phase 5C ordering) ----------------------------
    # The key is claimed BEFORE the reservation, so a replay never charges and
    # a conflicting reuse is rejected before any unit is spent.
    idem_key_raw = request.headers.get("idempotency-key")
    idem_key: Optional[str] = None
    fingerprint = {"raw_input": raw_input}
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

    # ---- durable inference job (BEFORE any inference happens) -------------
    try:
        record = async_jobs.create_job(
            tenant,
            {"raw_input": raw_input},
            request_id=rid or None,
            kind=async_jobs.JOB_KIND_INFERENCE,
        )
    except async_jobs.AsyncStoreUnavailableError:
        if idem_key:
            idempotency_store.release(idem_key, tenant)
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

    owner = _claim_owner(request)
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
                "lease": {
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
                },
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
        409: {"description": "LEASE_LOST — the caller no longer holds the current lease."},
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
        409: {"description": "LEASE_LOST — stale or unproven worker; nothing was committed."},
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

    if "bridge_body" not in body:
        return _error_response(400, "REQUEST_MALFORMED", "bridge_body is required", request_id=rid)
    bridge_body = body.get("bridge_body")

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

    # ---- device-reported identity (labelled untrusted downstream) ---------
    # Lazy import: keeps the model/provider import graph out of the API
    # process unless a device result is actually submitted (see module note).
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
        return _error_response(503, "PROVIDER_UNAVAILABLE", "service configuration invalid", request_id=rid)

    # process_from_source performs NO provider resolution and NO inference: the
    # in-process model is never loaded or called on this path.
    kernel_result = kernel.process_from_source(
        raw_input,
        _interpret,
        request_id=rid or record.request_id or None,
    )

    engine_status = kernel_result.status

    # MODEL_UNAVAILABLE on THIS path means the server could not validate the
    # submission at all: the device candidate parsed and entered the pipeline,
    # so there is no model-availability problem here — it is an unexpected
    # server-side failure whose outcome is UNKNOWN. That is a RETRYABLE job
    # failure under the repository's existing vocabulary
    # (async_jobs.classify_failure), recorded with the fenced fail path and
    # bounded by the job's attempt budget — never committed as a terminal
    # result, and never presented to the client as an authoritative outcome.
    if engine_status == "MODEL_UNAVAILABLE":
        _fail_inference_job(
            record, "MODEL_UNAVAILABLE", "the submission could not be validated on the server"
        )
        return _error_response(
            503,
            "MODEL_UNAVAILABLE",
            "the submission could not be validated; retry later",
            request_id=rid,
        )

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
    # ADDITIVE provenance for this route only: the kernel's execution-evidence
    # chain (recorded == actually executed). No existing 5D field is changed —
    # the envelope's own ``evidence`` key keeps its document-evidence contract.
    evidence = getattr(kernel_result, "evidence", None)
    if isinstance(content.get("metadata"), dict):
        content["metadata"]["execution_evidence"] = (
            evidence.to_dict() if evidence is not None and hasattr(evidence, "to_dict") else None
        )

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
        # Fenced out (or lost the race to a concurrent submission by the same
        # generation). Never claim a commit that did not happen: surface the
        # stored authoritative result when one exists, else LEASE_LOST.
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
