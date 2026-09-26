"""Platrixa — async developer API (Phase 5E).

Async document processing over the EXISTING pipeline — the async layer
adds scheduling, not semantics:

    POST /v1/documents              POST /v1/webhook-endpoints
        ↓ 202 job accepted              ↓ 201 endpoint registered
        ↓ (job_id, result_id,           ↓ (secret shown EXACTLY ONCE)
           status_url, result_url)          ↓
        ↓                          best-effort signed delivery after
    durable PostgreSQL job state   each terminal job outcome
        ↓
    in-process worker thread
        ↓ claim_next_job (lease)
    EXISTING DocumentProcessor → Kernel   (reused verbatim — the async
        ↓                                  layer owns no part of it)
    5D canonical envelope stored verbatim
        ↓
    GET /v1/jobs/{job_id}      status poll (completion ≠ VERIFIED)
    GET /v1/results/{result_id}   ← THE Phase 5D envelope

Honest limits (documented in docs/HOSTED_API.md):
  * One durable job store (the metering PostgreSQL). Restarts never
    erase jobs; QUEUED work is resumed and leased PROCESSING work is
    recovered after the lease expires.
  * Webhook delivery is a best-effort, single-attempt, signed
    foundation — not an at-least-once production queue. Polling is the
    reliable path; webhooks are a convenience.

Security boundary:
  * Same admission as sync: Phase 15 key / Phase 16 metered gate
    (reservation at submission — the unit pays for the work admitted).
  * Job/result reads are strictly tenant-scoped (server-derived tenant).
  * No user-controlled paths, no URL fetching, no raw document logging.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.results import build_process_result
from api.schemas import (
    DeveloperJobAcceptedResponse,
    DeveloperJobStatusResponse,
    DeveloperResultEnvelope,
    DeveloperWebhookEndpointResponse,
)
from api.status import (
    LABEL_BY_PUBLIC_STATUS,
    public_status_for_engine,
    public_status_for_error_code,
)
from api.routes.developer import (
    _error_response,
    _get_client,
    _log,
    _resolve_idempotency_tenant,
    _safe_accounting,
    _safe_candidate,
    _sanitize_request_id,
)
from backend.auth import async_jobs
from backend.auth import gate as metered_gate
from backend.auth import idempotency as idempotency_store

logger = logging.getLogger("platrixa.api")

router = APIRouter(tags=["developer-async"])

API_VERSION = "v1"

# A stable, private placeholder URL accepted at registration (spec-compliant
# https validation; the endpoint is never fetched in this deployment).
_PLACEHOLDER_HOSTS = {"example.com", "www.example.com"}

# 16 MiB of base64 ≈ 12 MiB of document — the durable-store payload cap.
_MAX_B64_BODY = 16 * 1024 * 1024


def _is_https_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if host in _PLACEHOLDER_HOSTS:
        return True  # documented developer placeholder, never fetched
    # Fail closed on clearly non-public targets (no SSRF surface).
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return False
    try:
        import ipaddress

        ipaddress.ip_address(host)
        return False  # raw IP literals are rejected (https hostname required)
    except ValueError:
        return True


def _job_urls(job_id: str, result_id: str) -> tuple[str, str]:
    base = (os.getenv("PLATRIXA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    status_url = f"{base}/v1/jobs/{job_id}" if base else f"/v1/jobs/{job_id}"
    result_url = f"{base}/v1/results/{result_id}" if base else f"/v1/results/{result_id}"
    return status_url, result_url


def _async_not_ready(code: str, rid: Optional[str], message: str) -> JSONResponse:
    return _error_response(
        400 if code == "ASYNC_NOT_CONFIGURED" else 503, code, message, request_id=rid
    )


# ---------------------------------------------------------------------------
# Guard: same admission semantics as sync (auth only here; reservation is
# performed inside the submission handler so replays never double-charge)
# ---------------------------------------------------------------------------


def _metered_api_key_guard_async(request: Request) -> None:
    from api.routes.developer import _metered_api_key_guard

    _metered_api_key_guard(request)


# ---------------------------------------------------------------------------
# Worker thread (in-process; honest best-effort, durable state)
# ---------------------------------------------------------------------------

_worker_started = False
_worker_lock = threading.Lock()


def ensure_worker_started() -> None:
    """Start the in-process worker once per process (idempotent).

    Started lazily on the first /v1/documents submission so an API
    process that never uses async pays nothing. The worker is a daemon
    thread: it never blocks shutdown, and durable state means a restart
    loses nothing (QUEUED jobs are resumed; leased work is recovered).
    """
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name="platrixa-async-worker", daemon=True)
        thread.start()
        _worker_started = True
        logger.info("async document worker started (in-process, lease-based)")


def _worker_loop() -> None:
    while True:
        try:
            record = async_jobs.claim_next_job()
            if record is None:
                time.sleep(0.5)
                continue
            _process_job(record)
        except async_jobs.AsyncStoreUnavailableError:
            time.sleep(2.0)
        except Exception as exc:  # never let the worker die
            logger.warning("async worker iteration failed: %s", type(exc).__name__)
            time.sleep(1.0)


def _process_job(record: async_jobs.JobRecord) -> None:
    """Process one claimed job through the EXISTING document pipeline.

    The async layer owns scheduling only: it reuses the same facade
    wiring (_get_client) and the same DocumentProcessor construction as
    the synchronous document route, and stores the verbatim 5D envelope.
    Infrastructure failures mark the job FAILED with retryable=true
    (honest signal — resubmission may succeed); every engine terminal
    state is stored as COMPLETED with its real status.
    """
    rid = record.request_id
    started = time.perf_counter()

    try:
        client = _get_client(_WorkerRequest())
    except Exception:
        _fail_job(record, "PROVIDER_UNAVAILABLE", "service configuration invalid", rid)
        return

    from backend.document_understanding.processor import DocumentProcessor
    from backend.document_understanding.registry import get_ocr_provider
    from backend.document_understanding.inputs import DocumentInputError
    from platrixa.errors import PlatrixaError

    try:
        request_payload = _load_job_request(record.job_id)
        data, source_name = _decode_document_bytes(request_payload)
    except DocumentInputError as exc:
        _fail_job(record, exc.code, exc.message, rid, retryable=False)
        return
    except async_jobs.AsyncStoreUnavailableError:
        # Store down mid-flight: release the lease so the job is retried.
        _release_lease(record)
        return
    except Exception as exc:
        logger.warning("async job %s payload unreadable: %s", record.job_id[:14], type(exc).__name__)
        _fail_job(record, "INPUT_INVALID", "stored job payload could not be read", rid, retryable=False)
        return

    try:
        processor = DocumentProcessor(
            process_text=client.process,
            ocr_provider=get_ocr_provider(),
        )
        result = processor.process(data, source_name, request_id=rid)
    except PlatrixaError:
        _fail_job(record, "PROVIDER_UNAVAILABLE", "model provider is unavailable; retry later", rid, retryable=True)
        return
    except Exception as exc:
        logger.warning("async job %s failed: %s", record.job_id[:14], type(exc).__name__)
        _fail_job(record, "PROVIDER_UNAVAILABLE", "processing failed; resubmission may succeed", rid, retryable=True)
        return

    kernel_result = result.kernel_result
    status = result.status
    transport_status = _transport_status_for(status)

    content = build_process_result(
        request_id=getattr(kernel_result, "request_id", None),
        engine_status=status,
        engine_status_label=getattr(kernel_result, "status_label", "") or status,
        next_action=getattr(kernel_result, "next_action", None),
        issues=list(getattr(kernel_result, "issues", None) or []),
        grounding_issues=list(getattr(kernel_result, "grounding_issues", None) or []),
        rule_evidence=list(getattr(kernel_result, "rule_evidence", None) or []),
        interpretation=_safe_candidate(getattr(kernel_result, "interpretation", None)),
        accounting=_safe_accounting(getattr(kernel_result, "accounting", None)),
        document=result.document.to_dict(),
        evidence_refs=result.document.evidence,
        lineage=result.lineage,
        timings_ms=result.timings_ms,
        notes=result.notes,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    try:
        async_jobs.complete_job(record.job_id, envelope=content, http_status=transport_status)
    except async_jobs.AsyncStoreUnavailableError:
        logger.warning("async job %s completion store write failed", record.job_id[:14])
        return

    api_status = public_status_for_engine(status)
    _emit_webhooks(record, api_status)
    _log(
        "/v1/documents(worker)",
        request_id=rid,
        status=api_status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        error=None,
    )


def _fail_job(
    record: async_jobs.JobRecord,
    code: str,
    message: str,
    rid: Optional[str],
    *,
    retryable: bool,
) -> None:
    """Mark a job FAILED with a deterministic error envelope."""
    api_status = public_status_for_error_code(code)
    envelope = {
        "api_version": API_VERSION,
        "error": {
            "code": code,
            "message": message,
            "request_id": rid,
            "api_status": api_status,
            "api_status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, ""),
            "retryable": retryable,
        },
    }
    try:
        async_jobs.fail_job(record.job_id, envelope=envelope, http_status=503 if retryable else 400, retryable=retryable)
    except async_jobs.AsyncStoreUnavailableError:
        logger.warning("async job %s failure store write failed", record.job_id[:14])
        return
    _emit_webhooks(record, api_status if retryable else "FAILED")


def _release_lease(record: async_jobs.JobRecord) -> None:
    """Give a leased job back (store hiccup mid-flight — not a failure)."""
    from sqlalchemy import text

    try:
        SessionLocal = async_jobs._session_factory()
        with SessionLocal() as session:
            with session.begin():
                session.execute(
                    text(
                        "UPDATE platrixa_async_jobs SET status = 'QUEUED', "
                        "lease_expires_at = NULL, updated_at = now() WHERE job_id = :j"
                    ),
                    {"j": record.job_id},
                )
    except Exception:
        logger.warning("async job %s lease release failed", record.job_id[:14])


def _transport_status_for(engine_status: str) -> int:
    from api.routes.kernel import _HTTP_STATUS_BY_KERNEL_STATUS

    return _HTTP_STATUS_BY_KERNEL_STATUS.get(engine_status, 500)


def _load_job_request(job_id: str) -> Dict[str, Any]:
    import json as _json
    from sqlalchemy import text

    SessionLocal = async_jobs._session_factory()
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT request_json FROM platrixa_async_jobs WHERE job_id = :j"),
            {"j": job_id},
        ).first()
    return _json.loads(row[0]) if row else {}


def _decode_document_bytes(request_payload: Dict[str, Any]) -> tuple[bytes, str]:
    """Recover (bytes, source_name) from the stored job request.

    Only base64 document payloads / raw text are supported (multipart
    bodies with inline file bytes are not accepted — the durable store
    is not a blob store). source_name is bounded and sanitized; it is
    never used as a filesystem path.
    """
    import base64
    import re

    data_b64 = request_payload.get("document_b64") or ""
    source_name = re.sub(r"[^A-Za-z0-9._ -]", "", str(request_payload.get("source_name") or "document"))[:120] or "document"
    if data_b64:
        return base64.b64decode(data_b64, validate=False), source_name
    raw_input = request_payload.get("raw_input")
    if isinstance(raw_input, str) and raw_input.strip():
        return raw_input.encode("utf-8"), "text_input.txt"
    from backend.document_understanding.inputs import DocumentInputError

    raise DocumentInputError("INPUT_MISSING", "stored job has no document payload")


class _WorkerRequest:
    """Minimal request stand-in for the worker thread (no app state)."""

    def __init__(self) -> None:
        self.app = type("App", (), {"state": type("State", (), {"platrixa_client": None})()})()


# ---------------------------------------------------------------------------
# Webhook delivery (best-effort, single attempt, signed)
# ---------------------------------------------------------------------------


def _emit_webhooks(record: async_jobs.JobRecord, api_status: str) -> None:
    """Emit the deterministic event for a terminal outcome (best effort).

    Never raises and never blocks the worker: delivery runs on a short
    daemon thread; problems are logged, never surfaced as job failures.
    """
    event = async_jobs.EVENT_BY_API_STATUS.get(api_status)
    if event is None:
        return
    thread = threading.Thread(
        target=_deliver_webhooks,
        args=(record, event, api_status),
        name=f"platrixa-webhook-{record.job_id[-8:]}",
        daemon=True,
    )
    thread.start()


def _deliver_webhooks(record: async_jobs.JobRecord, event: str, api_status: str) -> None:
    try:
        endpoints = async_jobs.webhooks_for_event(record.tenant_id, event)
        if not endpoints:
            return
        import json as _json

        payload = async_jobs.build_event_payload(
            event=event,
            event_id_value=async_jobs.event_id(record.job_id, event),
            job_id=record.job_id,
            result_id=record.result_id,
            request_id=record.request_id,
            api_status=api_status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        body = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
        ts = int(time.time())
        import requests
        from urllib.parse import urlparse

        for endpoint in endpoints:
            try:
                secret = async_jobs.unseal_webhook_secret(endpoint.secret_sealed)
                sig = async_jobs.sign_event(secret, ts, body)
                headers = {
                    "Content-Type": "application/json",
                    "Platrixa-Event-Id": payload["id"],
                    "Platrixa-Signature": f"t={ts},v1={sig}",
                    "Platrixa-Event": event,
                }
                resp = requests.post(endpoint.url, data=body, headers=headers, timeout=5)
                logger.info(
                    "webhook delivered endpoint=%s job=%s status=%d",
                    urlparse(endpoint.url).netloc[:40],
                    record.job_id[:14],
                    resp.status_code,
                )
            except Exception as exc:
                logger.warning(
                    "webhook delivery failed endpoint=%s job=%s: %s",
                    urlparse(endpoint.url).netloc[:40] if endpoint.url else "?",
                    record.job_id[:14],
                    type(exc).__name__,
                )
                # Single attempt — no retry queue exists; documented.
    except Exception as exc:
        logger.warning("webhook emit failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/v1/documents",
    response_model=DeveloperJobAcceptedResponse,
    status_code=202,
    dependencies=[Depends(_metered_api_key_guard_async)],
    responses={
        202: {"description": "Job accepted for asynchronous processing (never a result)."},
        400: {"description": "ASYNC_NOT_CONFIGURED (zero-config deployment) or malformed request."},
        409: {"description": "Idempotency conflict — key reused with a different request."},
        413: {"description": "Document payload exceeds the durable-store limit."},
        415: {"description": "Unsupported document type."},
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
                    "Optional. Same Phase 5C contract as POST /v1/process, scoped to the document "
                    "CREATION request: same key + same request returns the original job reference "
                    "without a duplicate job or extra quota; same key + different request is a 409. "
                    "The key identifies the creation request — it is not the job_id."
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
                        "properties": {
                            "document_b64": {
                                "type": "string",
                                "description": "Base64 of a PDF/image/txt document (≤10 MiB decoded). Exactly one of document_b64 / raw_input.",
                            },
                            "raw_input": {
                                "type": "string",
                                "description": "Plain text alternative to document_b64. Exactly one of the two.",
                            },
                            "source_name": {
                                "type": "string",
                                "description": "Original file name (extension-validated; never used as a filesystem path).",
                            },
                        },
                    },
                    "example": {
                        "document_b64": "<base64 pdf>",
                        "source_name": "invoice.pdf",
                    },
                }
            },
        },
    },
)
async def create_document_job(request: Request) -> JSONResponse:
    """Submit a document for asynchronous processing.

    Acceptance (202) means admitted — it says NOTHING about the final
    status, which can be VERIFIED, REVIEW_REQUIRED, UNSUPPORTED or
    FAILED. Poll ``GET /v1/jobs/{job_id}``; fetch the 5D envelope from
    ``GET /v1/results/{result_id}`` when complete.
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    started = time.perf_counter()

    if not async_jobs.async_configured():
        return _async_not_ready(
            "ASYNC_NOT_CONFIGURED",
            rid,
            "this deployment has no durable async store configured; use POST /v1/process/document",
        )

    # ---- parse the request (JSON only; base64 document or raw text) ------
    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)

    document_b64 = body.get("document_b64")
    raw_input = body.get("raw_input")
    source_name = str(body.get("source_name") or "document.pdf")
    if document_b64 is None and not (isinstance(raw_input, str) and raw_input.strip()):
        return _error_response(400, "INPUT_MISSING", "provide document_b64 or raw_input", request_id=rid)
    if document_b64 is not None and not isinstance(document_b64, str):
        return _error_response(400, "INPUT_INVALID", "document_b64 must be a base64 string", request_id=rid)
    if document_b64:
        import base64 as _b64

        try:
            data = _b64.b64decode(document_b64, validate=False)
        except Exception:
            return _error_response(400, "INPUT_INVALID", "document_b64 is not valid base64", request_id=rid)
        from backend.document_understanding.inputs import validate_document_input

        try:
            validate_document_input(data, source_name)
        except Exception as exc:
            code = getattr(exc, "code", "INPUT_INVALID")
            status_map = {"FILE_TYPE_UNSUPPORTED": 415, "CONTENT_TYPE_UNSUPPORTED": 415, "FILE_TOO_LARGE": 413}
            return _error_response(status_map.get(code, 400), code, str(getattr(exc, "message", code)), request_id=rid)
        if len(document_b64) > _MAX_B64_BODY:
            return _error_response(
                413,
                "REQUEST_TOO_LARGE",
                "base64 document exceeds the durable-store payload limit",
                request_id=rid,
            )
    elif isinstance(raw_input, str) and len(raw_input) > 2_000_000:
        return _error_response(413, "REQUEST_TOO_LARGE", "raw_input exceeds the durable-store payload limit", request_id=rid)

    # ---- tenant scope (server-derived) ------------------------------------
    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; request not admitted")

    # ---- idempotency on the CREATION request (Phase 5C machinery) ---------
    idem_key_raw = request.headers.get("idempotency-key")
    idem_key: Optional[str] = None
    if idem_key_raw is not None:
        ok, key_code = idempotency_store.validate_key(idem_key_raw)
        if not ok:
            return _error_response(400, key_code, idempotency_store.KEY_FORMAT_MESSAGE, request_id=rid)
        idem_key = idem_key_raw.strip()
        try:
            outcome = idempotency_store.claim(idem_key, tenant, "/v1/documents", _creation_fingerprint(body))
        except idempotency_store.IdempotencyUnavailableError:
            return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; request not admitted")
        if outcome.replay:
            env = outcome.envelope or {}
            data_part = env.get("data", env)
            status_url, result_url = _job_urls(data_part.get("job_id", ""), data_part.get("result_id", ""))
            _log("/v1/documents", request_id=rid, status=None,
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

    # ---- quota reservation (canonical attempts only) -----------------------
    if metered_gate._metering_configured():
        provided = request.headers.get("x-platrixa-api-key", "")
        reason, _ctx = metered_gate.authorize_request(provided)
        if reason != metered_gate.REASON_OK:
            if idem_key:
                idempotency_store.release(idem_key, tenant)
            from api.routes.developer import _gate_http_exception

            raise _gate_http_exception(reason)

    # ---- durable job creation ----------------------------------------------
    stored_payload: Dict[str, Any] = {"source_name": source_name[:120]}
    if document_b64:
        stored_payload["document_b64"] = document_b64
    if isinstance(raw_input, str) and raw_input.strip():
        stored_payload["raw_input"] = raw_input

    try:
        record = async_jobs.create_job(tenant, stored_payload, request_id=rid)
    except async_jobs.AsyncStoreUnavailableError:
        if idem_key:
            idempotency_store.release(idem_key, tenant)
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable; request not admitted")

    ensure_worker_started()

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

    # Record the creation receipt for idempotent replay of THIS response.
    if idem_key:
        try:
            idempotency_store.complete(
                idem_key,
                tenant,
                "/v1/documents",
                _creation_fingerprint(body),
                request_id=rid or None,
                envelope={"data": accepted},
                http_status=202,
            )
        except idempotency_store.IdempotencyUnavailableError:
            logger.warning("async idempotency record failed (non-fatal)")

    _log("/v1/documents", request_id=rid, status="ACCEPTED",
         duration_ms=int((time.perf_counter() - started) * 1000), error=None)
    return JSONResponse(
        status_code=202,
        content=accepted,
        headers={"Idempotent-Replayed": "false"} if idem_key else None,
    )


def _creation_fingerprint(body: Dict[str, Any]) -> Dict[str, Any]:
    """The request fields that materially affect processing (fingerprint)."""
    return {
        "document_b64": body.get("document_b64"),
        "raw_input": body.get("raw_input"),
        "source_name": str(body.get("source_name") or "document.pdf"),
    }


@router.get(
    "/v1/jobs/{job_id}",
    response_model=DeveloperJobStatusResponse,
    dependencies=[Depends(_metered_api_key_guard_async)],
    responses={
        404: {"description": "JOB_NOT_FOUND — no such job for this tenant."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
)
def job_status_v1(job_id: str, request: Request) -> JSONResponse:
    """Poll job status. Completion is NOT VERIFIED — a completed job
    surfaces the REAL engine outcome stored with its result (VERIFIED /
    REVIEW_REQUIRED / UNSUPPORTED); ``retryable`` is true only for
    infrastructure failures."""
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "async documents are not configured on this deployment")
    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    try:
        record = async_jobs.get_job(tenant, job_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    if record is None:
        return _error_response(404, "JOB_NOT_FOUND", "no such job for this tenant", request_id=rid)

    reason_codes: list = []
    if record.status in {"QUEUED", "PROCESSING"}:
        api_status = "PROCESSING"
        status_out = "PROCESSING"
        retryable = True
        _status_url, result_url = _job_urls(record.job_id, record.result_id)
    elif record.status == "COMPLETED":
        engine_status = _engine_status_from_result(record)
        api_status = public_status_for_engine(engine_status) if engine_status else "PROCESSING"
        status_out = engine_status or "PROCESSING"
        retryable = False
        _status_url, result_url = _job_urls(record.job_id, record.result_id)
    else:  # FAILED
        code = _error_code_from_record(record)
        api_status = public_status_for_error_code(code)
        status_out = "FAILED"
        reason_codes.append(code)
        retryable = bool(record.retryable)
        _status_url, result_url = _job_urls(record.job_id, record.result_id)

    body = {
        "api_version": API_VERSION,
        "job_id": record.job_id,
        "request_id": record.request_id or rid,
        "status": status_out,
        "status_label": LABEL_BY_PUBLIC_STATUS.get(api_status, status_out),
        "retryable": retryable,
        "reason_codes": reason_codes,
        "result_url": result_url,
        "created_at": record.created_at.isoformat() if record.created_at else "",
        "updated_at": record.updated_at.isoformat() if record.updated_at else "",
    }
    return JSONResponse(status_code=200, content=body)


def _engine_status_from_result(record: async_jobs.JobRecord) -> Optional[str]:
    """Read the engine status from the stored result envelope."""
    if not record.result_json:
        return None
    try:
        import json as _json

        env = _json.loads(record.result_json)
        return env.get("status") or (env.get("metadata") or {}).get("engine_status")
    except Exception:
        return None


def _error_code_from_record(record: async_jobs.JobRecord) -> str:
    """The deterministic error code stored with a FAILED job."""
    if record.error_json:
        try:
            import json as _json

            env = _json.loads(record.error_json)
            code = (env.get("error") or {}).get("code")
            if code:
                return str(code)
        except Exception:
            pass
    return "PROVIDER_UNAVAILABLE" if record.retryable else "JOB_FAILED"


@router.get(
    "/v1/results/{result_id}",
    response_model=DeveloperResultEnvelope,
    dependencies=[Depends(_metered_api_key_guard_async)],
    responses={
        404: {"description": "RESULT_NOT_FOUND (unknown id) or RESULT_NOT_READY (job incomplete)."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
)
def result_v1(result_id: str, request: Request) -> JSONResponse:
    """Fetch THE Phase 5D canonical envelope for an async document — the
    same contract as synchronous ``/v1/process`` (convergent results)."""
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "async documents are not configured on this deployment")
    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    try:
        record = async_jobs.get_result_record(tenant, result_id)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    if record is None:
        return _error_response(404, "RESULT_NOT_FOUND", "no such result for this tenant", request_id=rid)
    if record.status != "COMPLETED" or not record.result_json:
        return _error_response(
            404, "RESULT_NOT_READY", "the job has not completed yet; poll the job status", request_id=rid
        )
    import json as _json

    env = _json.loads(record.result_json)
    return JSONResponse(
        status_code=record.http_status or 200,
        content=env,
        headers={"Idempotent-Replayed": "false"},
    )


# ---------------------------------------------------------------------------
# Webhook endpoint registration
# ---------------------------------------------------------------------------


@router.post(
    "/v1/webhook-endpoints",
    response_model=DeveloperWebhookEndpointResponse,
    status_code=201,
    dependencies=[Depends(_metered_api_key_guard_async)],
    responses={
        400: {"description": "ASYNC_NOT_CONFIGURED or invalid url/events/secret."},
        503: {"description": "ASYNC_UNAVAILABLE — store down (fail closed)."},
    },
    openapi_extra={
        "parameters": [
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
                        "required": ["url"],
                        "properties": {
                            "url": {
                                "type": "string",
                                "format": "uri",
                                "description": "Public https URL to deliver signed events to (no localhost/IP literals).",
                            },
                            "events": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": [
                                        "document.processing",
                                        "document.completed",
                                        "document.review_required",
                                        "document.unsupported",
                                        "document.failed",
                                    ],
                                },
                                "description": "Closed event vocabulary. Defaults to all terminal events.",
                            },
                            "secret": {
                                "type": "string",
                                "minLength": 16,
                                "maxLength": 200,
                                "description": "Optional caller-supplied signing secret (else one is generated). Returned exactly once; stored sealed.",
                            },
                        },
                    },
                    "example": {
                        "url": "https://example.com/hooks/platrixa",
                        "events": ["document.completed", "document.failed"],
                    },
                }
            },
        },
    },
)
async def register_webhook_endpoint(request: Request) -> JSONResponse:
    """Register a webhook endpoint.

    The signing secret is returned EXACTLY ONCE in this response; only a
    server-keyed seal is stored. Events: document.processing,
    document.completed, document.review_required, document.unsupported,
    document.failed. Delivery in this deployment is best-effort,
    single-attempt, signed (``Platrixa-Signature: t=...,v1=...``), with
    deterministic event ids — NOT an at-least-once guarantee; polling
    remains the reliable path.
    """
    rid = _sanitize_request_id(request.headers.get("x-request-id", ""))
    if not async_jobs.async_configured():
        return _async_not_ready("ASYNC_NOT_CONFIGURED", rid, "async features are not configured on this deployment")
    if not async_jobs.webhook_signing_configured():
        return _async_not_ready(
            "ASYNC_NOT_CONFIGURED",
            rid,
            "webhook signing is not configured on this deployment (server-side signing key missing)",
        )
    try:
        body = await request.json()
    except Exception:
        return _error_response(400, "REQUEST_MALFORMED", "request body could not be parsed as JSON", request_id=rid)
    if not isinstance(body, dict):
        return _error_response(400, "REQUEST_MALFORMED", "request body must be a JSON object", request_id=rid)
    url = body.get("url")
    events = body.get("events") or [
        "document.completed",
        "document.review_required",
        "document.unsupported",
        "document.failed",
    ]
    if not isinstance(url, str) or not _is_https_url(url):
        return _error_response(400, "INPUT_INVALID", "url must be a public https URL", request_id=rid)
    if not isinstance(events, list) or not events or not all(e in async_jobs.ALL_EVENTS for e in events):
        return _error_response(
            400, "INPUT_INVALID", f"events must be a non-empty subset of {list(async_jobs.ALL_EVENTS)}", request_id=rid
        )
    supplied_secret = body.get("secret")
    if supplied_secret is not None and (
        not isinstance(supplied_secret, str) or len(supplied_secret) < 16 or len(supplied_secret) > 200
    ):
        return _error_response(400, "INPUT_INVALID", "secret must be 16-200 characters when supplied", request_id=rid)

    tenant = _resolve_idempotency_tenant(request)
    if tenant is None:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")
    try:
        record, plain_secret = async_jobs.register_webhook(tenant, url, [str(e) for e in events], secret=supplied_secret)
    except async_jobs.AsyncStoreUnavailableError:
        return _async_not_ready("ASYNC_UNAVAILABLE", rid, "async store unavailable")

    _log("/v1/webhook-endpoints", request_id=rid, status="REGISTERED", duration_ms=0, error=None)
    return JSONResponse(
        status_code=201,
        content={
            "api_version": API_VERSION,
            "webhook_id": record.webhook_id,
            "url": record.url,
            "events": record.events,
            "secret": plain_secret,
            "created_at": record.created_at.isoformat() if record.created_at else "",
            "delivery": {
                "semantics": "best-effort single attempt (not at-least-once)",
                "signature": "Platrixa-Signature: t=<unix>,v1=<hmac-sha256 over '{t}.{body}'>",
                "signature_tolerance_seconds": async_jobs.SIGNATURE_TOLERANCE_SECONDS,
                "event_ids": "deterministic per (job_id, event); deduplicate on id",
                "recommended": "poll GET /v1/jobs/{job_id} as the reliable path",
            },
        },
    )
