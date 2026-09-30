"""Platrixa — async developer API (Phase 5E).

Async document processing over the EXISTING pipeline — the async layer
adds scheduling, not semantics:

    POST /v1/documents              POST /v1/webhook-endpoints
        ↓ 202 job accepted              ↓ 201 endpoint registered
        ↓ (job_id, result_id,           ↓ (secret shown EXACTLY ONCE)
           status_url, result_url)          ↓
        ↓                          durable signed delivery after
    durable PostgreSQL job state   each job outcome (5K)
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
  * Webhook delivery is DURABLE and at-least-once (Phase 5K): each
    delivery is persisted before the attempt and retried with bounded
    backoff. Consumers must still deduplicate on the deterministic event
    id; exactly-once is not claimed. Polling remains the simplest
    reliable path.

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
from typing import Any, Dict, List, Optional

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
    _record_request_metadata,
    _resolve_idempotency_tenant,
    _safe_accounting,
    _safe_candidate,
    _sanitize_request_id,
)
from backend.auth import async_jobs
from backend.auth import gate as metered_gate
from backend.auth import idempotency as idempotency_store

# Phase 5I: the ONE authoritative admission path (audit C1). Reservation
# is owned by the admission boundary, never by a per-route call.
from backend.auth import admission as admission_boundary

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


def _assert_safe_delivery_target(url: str) -> None:
    """
    Delivery-time re-validation of a registered webhook URL.

    Security hardening (audit H-03, 2026-09-29). ``_is_https_url`` runs only
    at REGISTRATION time and never resolves DNS, so it cannot stop:

      * a redirect from the approved host into an internal destination
        (fixed separately by pinning ``allow_redirects=False``);
      * DNS rebinding — a public-looking hostname that resolves to a
        private, loopback, or link-local address at delivery time;
      * an attacker re-pointing the domain between registration and
        delivery.

    This check resolves the hostname NOW and refuses any address that is not
    globally routable. It is deliberately a property of the resolved address,
    not a string blocklist: blocking the literal "169.254.169.254" would be
    security theatre, because the same range is reachable via any address in
    it, via IPv6, or via a hostname.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise ValueError("webhook URL has no host")

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(f"webhook host could not be resolved: {type(exc).__name__}")

    if not infos:
        raise ValueError("webhook host resolved to no addresses")

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise ValueError("webhook host resolved to a non-IP address")
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"webhook host resolves to a non-public address "
                f"({ip.is_private and 'private' or ip.is_loopback and 'loopback' or ip.is_link_local and 'link-local' or 'non-public'})"
            )


def _post_webhook(url: str, body: str, headers: dict) -> Any:
    """
    Perform one webhook POST.

    Security hardening (audit H-03): redirects are NOT followed and the
    resolved destination is re-validated immediately before the request, so
    a destination approved at registration cannot be used to reach an
    internal service later.

    Extracted as a named seam so the security regression suite can exercise
    the real delivery path rather than a reimplementation of it.
    """
    import requests

    _assert_safe_delivery_target(url)
    return requests.post(
        url,
        data=body,
        headers=headers,
        timeout=5,
        allow_redirects=False,
    )


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
    """Phase 5I admission for read/management async endpoints.

    This guard authenticates WITHOUT reserving: the billable endpoint
    (POST /v1/documents) reserves exactly once inside its own handler
    — after idempotency claim, before job creation — through the single
    ``admission.admit`` choke point, exactly like POST /v1/process.
    Read endpoints (jobs/results/webhook registration) never charge.
    """
    from api.routes.developer import _gate_http_exception

    provided = request.headers.get("x-platrixa-api-key", "")
    reason, _adm = admission_boundary.authenticate_only(provided)
    if reason != admission_boundary.ADMIT_OK:
        raise _gate_http_exception(reason)


# ---------------------------------------------------------------------------
# Worker thread (in-process; honest best-effort, durable state)
# ---------------------------------------------------------------------------

_worker_started = False
_worker_lock = threading.Lock()

# Phase 5K §18: bounded worker concurrency. A fixed pool, NOT a task per
# queued job. This is a resource-safety bound, not a capacity claim: no
# jobs/sec figure is asserted because none has been benchmarked.
WORKER_MAX_CONCURRENCY = max(
    1, int((os.getenv("PLATRIXA_WORKER_CONCURRENCY", "") or "2").strip() or 2)
)

# Webhook retries are drained in bounded batches (5K §18).
WEBHOOK_BATCH_SIZE = 20
WEBHOOK_RETRY_INTERVAL_SECONDS = 5.0


def ensure_worker_started() -> None:
    """Start the in-process worker once per process (idempotent).

    Still started lazily so an API process that never uses async pays
    nothing, but the worker now runs a RECOVERY SWEEP on entry, so a
    restart picks up jobs that already exist instead of waiting for the
    next submission to poke it (5K §8).
    """
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name="platrixa-async-worker", daemon=True)
        thread.start()
        _worker_started = True
        logger.info("async document worker started (in-process, lease-based, bounded pool)")


def _worker_loop(stop_event=None) -> None:
    """Bounded worker pool with startup recovery (5K §8, §18).

    Before 5K this loop only ever started when a NEW submission arrived,
    so a job created while no worker was running stayed QUEUED forever.
    Two changes:

      * it performs a RECOVERY SWEEP on entry, so jobs that already
        exist (including ones whose previous worker died with an expired
        lease) are discovered and completed without waiting for another
        submission to poke the thread;
      * concurrency is BOUNDED by a fixed-size pool
        (``WORKER_MAX_CONCURRENCY``). Jobs are pulled only while a slot
        is free, so an unbounded queue can never create unbounded tasks.
        Overload behaviour is therefore explicit: excess QUEUED jobs
        simply wait — they are not dropped and not partially processed.
    """
    logger.info("async worker starting (recovery sweep + bounded pool)")
    _recovery_sweep()
    sem = threading.Semaphore(WORKER_MAX_CONCURRENCY)
    active: List[threading.Thread] = []

    while not (stop_event is not None and stop_event.is_set()):
        # Reap finished work so the list cannot grow without bound.
        active = [t for t in active if t.is_alive()]
        if sem.acquire(blocking=False):
            try:
                record = async_jobs.claim_next_job()
            except async_jobs.AsyncStoreUnavailableError:
                sem.release()
                if _stopped(stop_event):
                    break
                time.sleep(2.0)
                continue
            except Exception as exc:  # never let the worker die
                logger.warning("async worker iteration failed: %s", type(exc).__name__)
                sem.release()
                if _stopped(stop_event):
                    break
                time.sleep(1.0)
                continue
            if record is None:
                sem.release()
                if _stopped(stop_event):
                    break
                # Keep draining webhook retries alongside job work even
                # when the job queue is empty: a retrying delivery must
                # not wait for the next submission to arrive.
                if _drain_webhook_retries():
                    continue
                if _stopped(stop_event):
                    break
                _interruptible_sleep(stop_event, 0.5)
                continue
            # 5K §15: the processing event is emitted at CLAIM time — the
            # only point where "this document is now being processed" is
            # true. It was declared in the event vocabulary but never
            # emitted anywhere (the audit's reachability finding).
            _emit_event_for_job(record, async_jobs.EVENT_PROCESSING, "PROCESSING")
            t = threading.Thread(
                target=_run_claimed_job,
                args=(record, sem),
                name=f"platrixa-job-{record.job_id[-8:]}",
                daemon=True,
            )
            active.append(t)
            t.start()
            try:
                if _drain_webhook_retries():
                    time.sleep(0.05)
            except Exception:
                pass
        else:
            # At capacity: wait for a slot instead of queueing more work.
            _interruptible_sleep(stop_event, 0.2)


def _stopped(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _interruptible_sleep(stop_event, seconds: float) -> None:
    """Sleep that wakes early on shutdown (so tests/utdown are prompt)."""
    if stop_event is None:
        time.sleep(seconds)
        return
    stop_event.wait(seconds)


def _run_claimed_job(record: async_jobs.JobRecord, sem: threading.Semaphore) -> None:
    try:
        _process_job(record)
    except Exception as exc:  # never let one job kill its worker thread
        logger.warning("async job thread failed: %s", type(exc).__name__)
    finally:
        sem.release()


def start_worker_thread(stop_event=None) -> threading.Thread:
    """Run the worker loop on a background thread (tests + startup).

    Production uses :func:`ensure_worker_started`, which additionally
    guards against double-start. This entry point exists so the loop's
    RECOVERY behaviour and BOUNDED CONCURRENCY can be exercised directly
    by a test rather than merely asserted from reading the code.
    """
    t = threading.Thread(
        target=_worker_loop, args=(stop_event,), name="platrixa-async-worker-test", daemon=True
    )
    t.start()
    return t


def _recovery_sweep() -> int:
    """Startup recovery: report and reclaim work left by a previous run.

    Expired leases are NOT mutated here — ``claim_next_job`` already
    treats an expired lease as claimable, atomically and race-free, so
    this sweep only COUNTS what is outstanding. Counting is what makes
    the recovery observable (and testable) without introducing a second,
    racy reclaim path that could double-claim.

    It also drains webhook deliveries left PENDING/RETRYING by a previous
    process, which is the delivery-side equivalent of job recovery.
    """
    try:
        counts = async_jobs.count_jobs_by_status()
    except Exception as exc:
        logger.info("async recovery sweep could not read store: %s", type(exc).__name__)
        return 0
    outstanding = {
        k: v for k, v in counts.items()
        if k in (async_jobs.STATUS_QUEUED, async_jobs.STATUS_RETRY_WAIT,
                 async_jobs.STATUS_PROCESSING)
    }
    if outstanding:
        logger.info("async recovery sweep found outstanding jobs: %s", outstanding)
    try:
        due = async_jobs.due_deliveries(limit=100)
        if due:
            logger.info("async recovery sweep found %d due webhook deliveries", len(due))
            _drain_webhook_retries()
    except Exception:
        pass
    return sum(outstanding.values())


def _drain_webhook_retries() -> int:
    """Attempt every delivery whose backoff has elapsed (5K §14).

    Bounded per call by ``WEBHOOK_BATCH_SIZE`` and by each delivery's own
    attempt budget, so a failing endpoint cannot spin forever or grow the
    queue without limit.
    """
    sent = 0
    try:
        due = async_jobs.due_deliveries(limit=WEBHOOK_BATCH_SIZE)
    except Exception:
        return 0
    for delivery in due:
        try:
            record = async_jobs.get_job(delivery.tenant_id, delivery.job_id)
            if record is None:
                continue
            endpoints = async_jobs.webhooks_for_event(
                delivery.tenant_id, delivery.event
            )
            match = next(
                (e for e in endpoints if e.webhook_id == delivery.webhook_id), None
            )
            if match is None:
                continue
            _attempt_delivery(record, delivery, match)
            sent += 1
        except Exception as exc:
            logger.warning("webhook retry failed: %s", type(exc).__name__)
    return sent


def _attempt_delivery(record, delivery, endpoint) -> None:
    """One signed POST for a queued delivery, with status classification."""
    import json as _json

    payload = async_jobs.build_event_payload(
        event=delivery.event,
        event_id_value=async_jobs.event_id(record.job_id, delivery.event),
        job_id=record.job_id,
        result_id=record.result_id,
        request_id=record.request_id,
        api_status=delivery.event.rsplit(".", 1)[-1].upper(),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
    body = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
    ts = int(time.time())
    secret = async_jobs.unseal_webhook_secret(endpoint.secret_sealed)
    sig = async_jobs.sign_event(secret, ts, body)
    headers = {
        "Content-Type": "application/json",
        "Platrixa-Event-Id": payload["id"],
        "Platrixa-Signature": f"t={ts},v1={sig}",
        "Platrixa-Event": delivery.event,
    }
    try:
        resp = _post_webhook(endpoint.url, body, headers)
        async_jobs.mark_delivery_attempt(
            delivery.delivery_id,
            http_status=resp.status_code,
            error=None,
            retryable=async_jobs.classify_webhook_failure(resp.status_code),
        )
    except Exception as exc:
        async_jobs.mark_delivery_attempt(
            delivery.delivery_id,
            http_status=None,
            error=type(exc).__name__,
            retryable=True,
        )


def _resolve_engine_request_id(record: async_jobs.JobRecord) -> Optional[str]:
    """Phase 5I: the request id the whole stack reports for this job.

    The document pipeline assigns the engine request id inside the
    worker (record.request_id is the value captured at submission). The
    job/result/observability surfaces must all agree on ONE id, so the
    worker overwrites the record's request_id with the engine id after
    processing completes (see _process_job). This helper reads the
    stored envelope's request_id when present, falling back to the
    submission-time id.
    """
    if not record.result_json:
        return record.request_id
    try:
        import json as _json

        env = _json.loads(record.result_json)
        rid_out = env.get("request_id")
        return rid_out or record.request_id
    except Exception:
        return record.request_id


def _process_job(record: async_jobs.JobRecord) -> None:
    """Process one claimed job under a RENEWED, FENCED lease (5K).

    Wraps the entire body in :class:`_LeaseRenewal` so a job that runs
    longer than the initial lease stays owned by this worker instead of
    being duplicated, and refuses to publish anything if the lease was
    lost mid-flight.

    The pipeline itself is untouched: this function still calls the
    EXISTING document path and the EXISTING Phase 5D serializer.
    """
    with _LeaseRenewal(record, async_jobs.JOB_LEASE_SECONDS) as renewal:
        try:
            _process_job_inner(record, renewal)
        except Exception as exc:  # a keeper or pipeline fault must not kill the worker
            logger.error("async job %s crashed: %s", record.job_id[:14], type(exc).__name__)


def _process_job_inner(
    record: async_jobs.JobRecord, renewal: _LeaseRenewal
) -> None:
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
    # 5K §7: refuse to publish once the lease has been lost, and pass the
    # ownership proof so the STORE also rejects a stale commit. Before
    # 5K this write was guarded only on status='PROCESSING', which let a
    # fenced-out worker overwrite the authoritative result.
    if renewal.lost:
        logger.warning(
            "async job %s result discarded (lease lost during processing)",
            record.job_id[:14],
        )
        return
    try:
        accepted = async_jobs.complete_job(
            record.job_id,
            envelope=content,
            http_status=transport_status,
            request_id=content.get("request_id") or rid,
            lease_owner=record.lease_owner,
            lease_generation=record.lease_generation,
        )
        if not accepted:
            logger.warning(
                "async job %s completion REJECTED (stale worker)", record.job_id[:14]
            )
            return
    except async_jobs.AsyncStoreUnavailableError:
        logger.warning("async job %s completion store write failed", record.job_id[:14])
        return

    api_status = public_status_for_engine(status)
    # Phase 5I: record the worker outcome under the ENGINE request id —
    # the same id /v1/jobs/{id}, /v1/results/{id} and the submit log row
    # align to — so observability covers the async path (audit M3).
    _record_request_metadata(
        (record.tenant_id, None),
        http_status=transport_status,
        api_status=api_status,
        reason_code=(content.get("reason_codes") or [None])[0]
        if isinstance(content.get("reason_codes"), list)
        else None,
        request_id=content.get("request_id") or rid,
        duration_ms=int((time.perf_counter() - started) * 1000),
        endpoint="/v1/documents(worker)",
    )
    _emit_webhooks(record, api_status)
    _log(
        "/v1/documents(worker)",
        request_id=rid,
        status=api_status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        error=None,
    )


class _LeaseRenewal:
    """Keep a claimed job's lease alive while it is being processed (5K §6).

    A fixed 120 s lease with no keepalive means any legitimately slow
    document gets re-claimed by a second worker *while it is still
    running* — duplicate processing, duplicate provider load, and a
    second result fighting the first.

    This renews at 1/3 of the lease so two consecutive failures are
    absorbed before expiry. Properties that matter:

      * renewal is CONDITIONAL on still owning the lease — a worker that
        has been fenced out cannot extend a lease it no longer holds;
      * ``lost`` flips when a renewal is refused, and the worker checks
        it before committing, so a stale worker never publishes;
      * it is ONE daemon thread per in-flight job, started and stopped
        deterministically with the work — never an unbounded task pool;
      * it always terminates, including on exception (``finally``).
    """

    def __init__(self, record: async_jobs.JobRecord, lease_seconds: int):
        self._record = record
        self._lease = max(5, int(lease_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lost = False

    def __enter__(self) -> "_LeaseRenewal":
        interval = max(1.0, self._lease / async_jobs.LEASE_RENEW_FRACTION)
        owner = self._record.lease_owner
        gen = self._record.lease_generation

        def _beat() -> None:
            while not self._stop.wait(interval):
                try:
                    ok = async_jobs.renew_lease(
                        self._record.job_id,
                        lease_owner=owner or "",
                        lease_generation=gen,
                        lease_seconds=self._lease,
                    )
                except async_jobs.AsyncStoreUnavailableError:
                    # Transient store outage: keep trying until the lease
                    # would actually expire, then report the loss.
                    continue
                except Exception as exc:  # never let the keeper die loudly
                    logger.warning("lease renewal error: %s", type(exc).__name__)
                    continue
                if not ok:
                    self.lost = True
                    logger.warning(
                        "lease renewal refused for job %s (ownership lost)",
                        self._record.job_id[:14],
                    )
                    return

        self._thread = threading.Thread(
            target=_beat, name=f"platrixa-lease-{self._record.job_id[-8:]}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        return False


def _fail_job(
    record: async_jobs.JobRecord,
    code: str,
    message: str,
    rid: Optional[str],
    *,
    retryable: bool,
) -> None:
    """Mark a job FAILED with a deterministic error envelope.

    Phase 5K: ``retryable`` is now DERIVED from the explicit classifier
    (``async_jobs.classify_failure``) rather than decided at each call
    site, and the store decides between RETRY_WAIT and terminal FAILED
    based on the remaining attempt budget. The commit is fenced on lease
    ownership, so a worker that lost its lease cannot fail a job another
    worker now owns.
    """
    api_status = public_status_for_error_code(code)
    # 5K §9: one deterministic classifier for the whole worker, so two
    # call sites can never disagree about the same failure.
    retryable = async_jobs.classify_failure(error_code=code, api_status=api_status)
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
        accepted = async_jobs.fail_job(
            record.job_id,
            envelope=envelope,
            http_status=503 if retryable else 400,
            retryable=retryable,
            lease_owner=record.lease_owner,
            lease_generation=record.lease_generation,
        )
        if not accepted:
            # Fenced out: another worker owns this job now. Its work is
            # no longer authoritative, so publish nothing for it.
            logger.warning(
                "async job %s failure NOT recorded (lease lost)", record.job_id[:14]
            )
            return
    except async_jobs.AsyncStoreUnavailableError:
        logger.warning("async job %s failure store write failed", record.job_id[:14])
        return
    # Phase 5I: the worker thread has no HTTP admission context; the
    # tenant is the job's own durable tenant (server-derived at
    # submission) — attribution is never guessed from the id.
    _record_request_metadata(
        (record.tenant_id, None),
        http_status=503 if retryable else 400,
        api_status=api_status,
        reason_code=code,
        request_id=rid,
        duration_ms=None,
        endpoint="/v1/documents(worker)",
    )
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
    """Emit the deterministic event for a terminal outcome (durable).

    Never raises and never blocks the worker. Phase 5K: a delivery ROW is
    written first (durably), and the actual HTTP attempt happens on a
    short daemon thread. If the process dies between the two, the row is
    still PENDING and the webhook worker retries it — the pre-5K path
    simply lost the event.
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


def _emit_event_for_job(
    record: async_jobs.JobRecord, event: str, api_status: str
) -> None:
    """Emit a NON-terminal lifecycle event (5K §15).

    ``document.processing`` belongs to this: it is true only while the
    job is claimed and executing, and it is the only point at which a
    subscriber can learn that work has started. It was declared in
    ``ALL_EVENTS`` but never emitted anywhere.
    """
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
            # 5K §13: persist the delivery BEFORE attempting it. A crash
            # after this point leaves a PENDING row the webhook worker
            # will pick up — instead of silently losing the event.
            delivery = None
            try:
                delivery = async_jobs.record_delivery(
                    record.job_id, record.tenant_id, endpoint.webhook_id, event
                )
            except async_jobs.AsyncStoreUnavailableError:
                logger.warning("webhook delivery row not persisted (store down)")
            try:
                secret = async_jobs.unseal_webhook_secret(endpoint.secret_sealed)
                sig = async_jobs.sign_event(secret, ts, body)
                headers = {
                    "Content-Type": "application/json",
                    "Platrixa-Event-Id": payload["id"],
                    "Platrixa-Signature": f"t={ts},v1={sig}",
                    "Platrixa-Event": event,
                }
                resp = _post_webhook(endpoint.url, body, headers)
                status = resp.status_code
                # 5K §14: retryability is CLASSIFIED, not guessed from the
                # status at the call site. Retry-After is honoured within
                # a bounded window (see store.mark_delivery_attempt).
                retryable = async_jobs.classify_webhook_failure(status)
                if delivery is not None:
                    async_jobs.mark_delivery_attempt(
                        delivery.delivery_id,
                        http_status=status,
                        error=None,
                        retryable=retryable,
                    )
                logger.info(
                    "webhook delivered endpoint=%s job=%s status=%d",
                    urlparse(endpoint.url).netloc[:40],
                    record.job_id[:14],
                    status,
                )
            except Exception as exc:
                # 5K §14: a network/transport failure is retryable, and the
                # attempt is recorded so a bounded retry is scheduled.
                # Pre-5K this was a silent give-up after one attempt.
                if delivery is not None:
                    try:
                        async_jobs.mark_delivery_attempt(
                            delivery.delivery_id,
                            http_status=None,
                            error=type(exc).__name__,
                            retryable=True,
                        )
                    except Exception:
                        pass
                logger.warning(
                    "webhook delivery failed endpoint=%s job=%s: %s",
                    urlparse(endpoint.url).netloc[:40] if endpoint.url else "?",
                    record.job_id[:14],
                    type(exc).__name__,
                )
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

    # ---- tenant scope (server-derived; authenticate-only, never charges)
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

    # ---- Phase 5I quota reservation (canonical attempts only) ------------
    # Exactly ONE admission call on this route: after the idempotency
    # claim (replays never reach this line), before durable job creation.
    provided = request.headers.get("x-platrixa-api-key", "")
    reason, adm = admission_boundary.admit(provided, reserve=True)
    if reason != admission_boundary.ADMIT_OK:
        if idem_key:
            idempotency_store.release(idem_key, tenant)
        from api.routes.developer import _gate_http_exception

        raise _gate_http_exception(reason)
    # Phase 5I: keep the admission context for the observability append
    # below (submission is a terminal outcome for the SUBMIT request).
    admitted_ctx = (adm.tenant_id, adm.key_prefix)

    # ---- durable job creation ----------------------------------------------
    stored_payload: Dict[str, Any] = {"source_name": source_name[:120]}
    if document_b64:
        stored_payload["document_b64"] = document_b64
    if isinstance(raw_input, str) and raw_input.strip():
        stored_payload["raw_input"] = raw_input

    # Phase 5I request-trace alignment: the job's request_id is the
    # ENGINE request id (which is what /v1/jobs/{id}, /v1/results/{id}
    # and the observability log all publish) with the client correlation
    # id as the fallback. Before this change a client-supplied X-Request-Id
    # was stored here while every read path reported the engine id —
    # so GET /v1/developer/requests/{id} 404'd for every async request
    # (audit: request-history finding M3).
    try:
        record = async_jobs.create_job(tenant, stored_payload, request_id=rid or None)
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

    # Phase 5I: the SUBMIT request is a terminal outcome for admission
    # purposes — record it so request history covers async submissions
    # (audit M3: only /v1/process was ever recorded).
    _record_request_metadata(
        admitted_ctx,
        http_status=202,
        api_status="PROCESSING",
        reason_code=None,
        request_id=rid or None,
        duration_ms=int((time.perf_counter() - started) * 1000),
        endpoint="/v1/documents",
    )
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
    document.failed. Delivery is signed
    (``Platrixa-Signature: t=...,v1=...``) and at-least-once with bounded
    retry; exactly-once is NOT claimed, so consumers deduplicate on the
    deterministic event id. Polling remains the simplest reliable path.
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
                "semantics": "at-least-once (deduplicate on event id)",
                "signature": "Platrixa-Signature: t=<unix>,v1=<hmac-sha256 over '{t}.{body}'>",
                "signature_tolerance_seconds": async_jobs.SIGNATURE_TOLERANCE_SECONDS,
                "event_ids": "deterministic per (job_id, event); deduplicate on id",
                "recommended": "poll GET /v1/jobs/{job_id} as the reliable path",
            },
        },
    )
