#!/usr/bin/env python3
"""Platrixa — outbound inference worker (Stage 2, device side).

WHAT THIS IS
============
The device half of the durable outbound inference plane introduced in Stage 1
(commit 021a77d, ``api/routes/inference_api.py``). It is deliberately a
STANDALONE script: stdlib plus ``requests``, no repo imports, so it can run on a
device (Termux/Android) without pulling the hosted API's dependency graph.

    client → POST /v1/inference-jobs         (Railway: durable job + ONE
                                              reservation, no inference)
      ↓
    THIS WORKER → POST /v1/worker/jobs/claim        (outbound HTTPS only)
      ↓  {job_id, raw_input, lease{owner, generation, renew_every_seconds,
                                   renew_url, result_url}}
    THIS WORKER → POST {renew_url}                  (heartbeat while running)
    THIS WORKER → POST 127.0.0.1:8081/interpret     (its OWN loopback bridge)
      ↓  the COMPLETE bridge response body, unmodified
    THIS WORKER → POST {result_url}                 (lease-fenced)
      ↓
    Railway deterministic kernel → authoritative stored result

THE AUTHORITY RULE (never violated here)
========================================
This process moves bytes. It does not decide financial correctness, does not
parse the model's JSON, does not repair or extract fields, and never declares a
status. It forwards the bridge's response body VERBATIM to the server, where the
SAME shared parser and the SAME deterministic kernel that serve the synchronous
path decide the authoritative outcome. A model-suggested ``VERIFIED`` is clamped
server-side; a truncated or malformed candidate fails closed server-side.

NO INBOUND SURFACE
==================
There is no listener, no bind, no port, no tunnel, no reverse proxy anywhere in
this file. The worker only ever *makes* outbound requests: HTTPS to the API and
plain HTTP to its own loopback bridge.

LEASE DISCIPLINE (and its limits, stated honestly)
==================================================
A client cannot prove it still holds a lease — only the server can. So:

  * the heartbeat keeps the lease warm and STOPS the worker as soon as a renewal
    is refused (409 LEASE_LOST) or the confirmed lease deadline passes;
  * a worker that believes it lost the lease discards the candidate and does not
    submit it;
  * but none of that is authority. The server's lease GENERATION FENCE is the
    final arbiter: ``POST .../result`` re-checks owner+generation at commit time
    and answers 409 when the lease has moved on. A check performed locally
    before a network request cannot make the request atomic — that is exactly
    why the fence exists, and why a 409 here means "discard", never "retry as
    the same generation".

CONFIGURATION (all values overridable by flag or environment)
=============================================================
    PLATRIXA_API_BASE_URL       hosted API origin. MUST be https, or plain http
                                ONLY for a loopback host. Default: none.
    PLATRIXA_API_KEY            tenant API key (never logged, never printed)
    PLATRIXA_WORKER_ID          bounded lease-owner label (^[A-Za-z0-9._-]{1,128}$)
    PLATRIXA_LOCAL_BRIDGE_URL   default http://127.0.0.1:8081 (loopback only)
    PLATRIXA_LOCAL_BRIDGE_KEY   optional device bridge key (separate credential)
    PLATRIXA_WORKER_MODEL_URL   optional DIRECT model endpoint on this device's
                                loopback (e.g. http://127.0.0.1:11434, the phone's
                                llama-server app). When set, the worker calls the
                                model itself instead of the device-bridge process;
                                the submitted result contract is IDENTICAL. When
                                unset (the default), the bridge path is unchanged.
    PLATRIXA_WORKER_MODEL_ID    model id sent to that endpoint (default "default")
    PLATRIXA_WORKER_MODEL_KEY   optional bearer key for the model endpoint only
    PLATRIXA_WORKER_MODEL_TIMEOUT_S  seconds to wait for the model (default 120)
    PLATRIXA_WORKER_MODEL_DIALECT    "chat" (default: POST /v1/chat/completions) or
                                "completion" (POST /v1/completions, the exact SFT
                                framing the Phase-H adapter was trained on)

Run:
    python scripts/platrixa_outbound_inference_worker.py --once
    python scripts/platrixa_outbound_inference_worker.py            # poll loop
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

logger = logging.getLogger("platrixa.worker")

# ---------------------------------------------------------------------------
# Wire contract constants — mirrored from the server, never invented.
# Source of truth: api/routes/inference_api.py and
# backend/model_provider/local_bridge.py at commit 021a77d.
# ---------------------------------------------------------------------------

API_KEY_HEADER = "X-Platrixa-API-Key"
WORKER_ID_HEADER = "X-Platrixa-Worker-Id"
BRIDGE_KEY_HEADER = "X-Platrixa-Android-Bridge-Key"

CLAIM_PATH = "/v1/worker/jobs/claim"
RENEW_SUFFIX = "/lease"
RESULT_SUFFIX = "/result"

STATUS_NO_WORK = "NO_WORK"
STATUS_CLAIMED = "CLAIMED"
CODE_LEASE_LOST = "LEASE_LOST"
CODE_JOB_NOT_FOUND = "JOB_NOT_FOUND"

# The server's idempotent-replay marker on the RESULT route. It is the only
# signal that distinguishes "your candidate became authoritative just now"
# from "a result was already authoritative and is being shown to you again".
# Source of truth: api/routes/inference_api.py sets it to "true" on the two
# duplicate/late paths and to nothing at all on a fresh fenced commit.
REPLAY_HEADER = "Idempotent-Replayed"

# Mirrors api/routes/inference_api.py::MAX_INFERENCE_RESULT_BYTES (64 KiB): the
# server's fail-closed ceiling for a result submission.
MAX_RESULT_BYTES = 64 * 1024
# A bridge response is bounded by the same ceiling: raw model text for <=512
# generated tokens is far smaller, so this is a ceiling, not a target.
MAX_BRIDGE_RESPONSE_BYTES = MAX_RESULT_BYTES

# Loopback-only defaults, matching the existing bridge contract.
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8081"
DEFAULT_BRIDGE_PATHS: Tuple[str, ...] = ("/interpret", "/")

API_BASE_URL_ENV = "PLATRIXA_API_BASE_URL"
API_KEY_ENV = "PLATRIXA_API_KEY"
WORKER_ID_ENV = "PLATRIXA_WORKER_ID"
BRIDGE_URL_ENV = "PLATRIXA_LOCAL_BRIDGE_URL"
BRIDGE_KEY_ENV = "PLATRIXA_LOCAL_BRIDGE_KEY"

# Direct model endpoint (the phone's llama-server app on this device's own
# loopback). Optional: when unset the worker keeps using the device bridge.
MODEL_URL_ENV = "PLATRIXA_WORKER_MODEL_URL"
MODEL_ID_ENV = "PLATRIXA_WORKER_MODEL_ID"
MODEL_KEY_ENV = "PLATRIXA_WORKER_MODEL_KEY"
MODEL_TIMEOUT_ENV = "PLATRIXA_WORKER_MODEL_TIMEOUT_S"
MODEL_DIALECT_ENV = "PLATRIXA_WORKER_MODEL_DIALECT"

DEFAULT_MODEL_ID = "default"
DEFAULT_MODEL_TIMEOUT_S = 120.0
MODEL_DIALECT_CHAT = "chat"
MODEL_DIALECT_COMPLETION = "completion"
MODEL_DIALECTS = (MODEL_DIALECT_CHAT, MODEL_DIALECT_COMPLETION)

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
COMPLETIONS_PATH = "/v1/completions"

# The server sanitizes worker ids with this exact shape and ignores anything
# else (falling back to a server-generated owner). Rejecting a bad id locally is
# fail-closed: a silently substituted owner would make every renewal proof
# mismatch.
BOUNDED_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_LEASE_PROOF_PATH_RE = re.compile(r"^/v1/worker/jobs/([A-Za-z0-9._-]{1,128})(?:/(lease|result))$")

_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WorkerError(Exception):
    """Base class for every failure this worker distinguishes."""


class WorkerConfigError(WorkerError):
    """The worker's own configuration is unusable (fail closed, before any I/O)."""


class UnsafeEndpointError(WorkerError):
    """A URL the worker refuses to call (remote plaintext HTTP, traversal, …)."""


class MalformedResponseError(WorkerError):
    """A server or bridge response that does not satisfy the documented shape."""


class TransportError(WorkerError):
    """A network-level failure (timeout, connection refused, DNS, TLS)."""


class BridgeUnavailableError(WorkerError):
    """The local model bridge is unreachable or reports itself unavailable."""


class BridgeResponseError(WorkerError):
    """The bridge answered with something this worker refuses to submit.

    Covers a non-JSON body, a JSON non-object, an oversized body, and a request
    the bridge itself rejected (422). A candidate in this class is NEVER sent to
    the API: no partial or invented interpretation is ever submitted.
    """


class LeaseLost(WorkerError):
    """The lease is no longer held: the candidate must be discarded.

    Raised on a refused renewal, a passed deadline, or a 409 from the result
    endpoint. It is never retried as the same generation.
    """


class ApiError(WorkerError):
    """A non-2xx API response this worker does not treat as lease loss."""

    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool):
        super().__init__(f"HTTP {status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


# ---------------------------------------------------------------------------
# URL / identifier validation
# ---------------------------------------------------------------------------


def _host_is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    host = host.strip().strip("[]").lower()
    if host in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _reject_unsafe_path(path: str, *, what: str) -> str:
    """Refuse traversal/encoding tricks in a URL path. Returns the path."""
    if "\\" in path or ".." in path or "%" in path:
        raise UnsafeEndpointError(f"{what} contains a traversal sequence: {path!r}")
    if "//" in path:
        raise UnsafeEndpointError(f"{what} contains an empty path segment: {path!r}")
    for segment in (path or "/").split("/"):
        if segment and not re.fullmatch(r"[A-Za-z0-9._~-]+", segment):
            raise UnsafeEndpointError(f"{what} segment is not a bounded identifier: {segment!r}")
    return path


def normalize_api_base_url(url: str) -> str:
    """Validate and normalize the hosted API base URL.

    Rules (all fail closed):
      * an absolute http/https URL with a host, no userinfo;
      * plaintext ``http`` ONLY for a loopback host — a remote plaintext API
        would put the tenant key on the wire unprotected;
      * no query, no fragment (a base URL is an origin + path prefix);
      * no traversal in the path prefix.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeEndpointError(f"{API_BASE_URL_ENV} is not configured")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise UnsafeEndpointError(f"API base URL must be http(s), got scheme {parts.scheme!r}")
    if not parts.hostname:
        raise UnsafeEndpointError(f"API base URL has no host: {raw!r}")
    if parts.username or parts.password:
        raise UnsafeEndpointError("API base URL must not embed credentials (use the API key header)")
    if parts.query or parts.fragment:
        raise UnsafeEndpointError("API base URL must not carry a query or fragment")
    if parts.scheme == "http" and not _host_is_loopback(parts.hostname):
        raise UnsafeEndpointError(
            "refusing remote plaintext HTTP: the tenant API key must not cross the network "
            f"unencrypted (got {raw!r}); use https, or a loopback host for local development"
        )
    path = _reject_unsafe_path(parts.path or "", what="API base URL path")
    return f"{parts.scheme}://{parts.netloc}{path.rstrip('/')}"


def normalize_bridge_url(url: str) -> str:
    """Validate the local model bridge URL: HTTP on a loopback host ONLY.

    The bridge is a device-local process. Pointing it anywhere else would either
    publish the model endpoint or send raw financial input to a third party, so
    a non-loopback host is refused outright rather than warned about.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeEndpointError(f"{BRIDGE_URL_ENV} is empty (bridge disabled)")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise UnsafeEndpointError(f"bridge URL must be http(s), got scheme {parts.scheme!r}")
    if not parts.hostname:
        raise UnsafeEndpointError(f"bridge URL has no host: {raw!r}")
    if parts.username or parts.password:
        raise UnsafeEndpointError("bridge URL must not embed credentials")
    if parts.query or parts.fragment:
        raise UnsafeEndpointError("bridge URL must not carry a query or fragment")
    if not _host_is_loopback(parts.hostname):
        raise UnsafeEndpointError(
            f"refusing a non-loopback bridge host: {parts.hostname!r} (the model bridge is local-only; "
            "no inbound port, public bridge, or tunnel is ever used)"
        )
    path = _reject_unsafe_path(parts.path or "", what="bridge URL path")
    return f"{parts.scheme}://{parts.netloc}{path.rstrip('/')}"


def normalize_model_url(url: Optional[str]) -> Optional[str]:
    """Validate the DIRECT model endpoint URL: HTTP on a loopback host ONLY.

    Same rules as the bridge URL, for the same reasons: the model server runs
    on the operator's own device, so a non-loopback host would publish the
    model endpoint or send raw financial input to a third party. Empty means
    "no direct model configured" (the bridge path stays in effect).
    """
    raw = (url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise UnsafeEndpointError(f"model URL must be http(s), got scheme {parts.scheme!r}")
    if not parts.hostname:
        raise UnsafeEndpointError(f"model URL has no host: {raw!r}")
    if parts.username or parts.password:
        raise UnsafeEndpointError("model URL must not embed credentials")
    if parts.query or parts.fragment:
        raise UnsafeEndpointError("model URL must not carry a query or fragment")
    if not _host_is_loopback(parts.hostname):
        raise UnsafeEndpointError(
            f"refusing a non-loopback model host: {parts.hostname!r} (the model server is local-only; "
            "no public model endpoint, tunnel, or relay is ever used)"
        )
    path = _reject_unsafe_path(parts.path or "", what="model URL path")
    return f"{parts.scheme}://{parts.netloc}{path.rstrip('/')}"


# ---------------------------------------------------------------------------
# Phase-H prompt mirror (byte-exact)
# ---------------------------------------------------------------------------
# The worker is deliberately standalone (stdlib + requests, no repo imports),
# so the canonical prompt from backend/model_provider/platrixa_prompt.py is
# mirrored here. scripts/platrixa_outbound_inference_worker_test.py asserts
# byte-equality against that module (loaded by path, dependency-free), so the
# two copies cannot silently drift. Do NOT reformat: the Phase-H adapter was
# fine-tuned on this exact SFT framing.

MODEL_MAX_NEW_TOKENS = 512
MODEL_TEMPERATURE = 0.0
MODEL_TOP_P = 1.0
MODEL_STOP_SEQUENCES: Tuple[str, ...] = (
    "\n### " + "Instruction:",
    "\n### " + "Input:",
    "\n### " + "Response:",
    "\nBelow is an " + "instruction",
    "\n" + "---",
)

ALPACA_PREFIX = (
    "Below is an instruction that describes a task, paired with an input "
    "that provides further context. Write a response that appropriately "
    "completes the request."
)

ALPACA_INSTRUCTION = (
    "You are the FYJC accounting language-understanding specialist. Parse "
    "the student's accounting language into a grounded structured "
    "interpretation with exactly these 18 fields: transaction_type, parties, "
    "amounts, payment_method, references, ambiguities, grounding, "
    "transaction_type_enum, payment_method_enum, ambiguity_flags, "
    "referenced_transaction_index, referenced_party, referenced_amount, "
    "field_confidences, overall_confidence, suggested_status, "
    "safety_flags, scope_flags. Do not invent missing information. Never "
    "produce journal entries, debit/credit decisions, or accounting "
    "conclusions. Output only machine-readable JSON with exactly these "
    "fields."
)


def build_model_prompt(text: str) -> str:
    """The byte-exact Phase-H SFT prompt (mirrored build_prompt)."""
    return (
        f"{ALPACA_PREFIX}\n\n"
        f"### Instruction:\n{ALPACA_INSTRUCTION}\n\n"
        f"### Input:\n{text}\n\n"
        f"### Response:\n"
    )


def is_alpaca_prompt(prompt: str) -> bool:
    """Guard used before generation to prevent prompt substitution."""
    return (
        prompt.startswith(ALPACA_PREFIX)
        and "### Instruction:" in prompt
        and "### Input:" in prompt
    )


def resolve_lease_endpoint(base_url: str, candidate: str, job_id: str, suffix: str) -> str:
    """Resolve a server-provided ``renew_url``/``result_url`` safely.

    The server documents these as relative paths for the job it just handed
    out. A response is therefore refused when it is absolute, carries a query or
    fragment, contains traversal, or does not name EXACTLY this job id with
    EXACTLY the expected suffix. A hijacked or buggy response cannot redirect
    the worker's credential, its raw input, or its model output somewhere else.
    """
    value = (candidate or "").strip()
    if not value:
        raise MalformedResponseError("lease URL is missing from the claim response")
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        raise UnsafeEndpointError(f"refusing an absolute lease URL: {value!r}")
    if parts.query or parts.fragment:
        raise UnsafeEndpointError(f"refusing a lease URL with a query or fragment: {value!r}")
    _reject_unsafe_path(parts.path, what="lease URL path")
    if not parts.path.startswith("/"):
        raise UnsafeEndpointError(f"lease URL must be an absolute path: {value!r}")
    expected = f"/v1/worker/jobs/{job_id}{suffix}"
    if parts.path != expected:
        raise UnsafeEndpointError(
            f"lease URL does not address this job: expected {expected!r}, got {parts.path!r}"
        )
    return f"{base_url}{parts.path}"


def _bounded_identifier(value: Any, *, what: str, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise MalformedResponseError(f"{what} must be a string")
    trimmed = value.strip()
    if not trimmed or len(trimmed) > max_length or not BOUNDED_IDENTIFIER_RE.match(trimmed):
        raise MalformedResponseError(f"{what} is not a bounded identifier: {trimmed[:128]!r}")
    return trimmed


def _positive_number(value: Any, *, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedResponseError(f"{what} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        raise MalformedResponseError(f"{what} must be a positive finite number, got {value!r}")
    return number


def _generation(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedResponseError(f"lease generation must be a non-negative integer, got {value!r}")
    return int(value)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def error_code_of(response: Any) -> str:
    """Extract ``error.code`` from a documented error envelope, else ""."""
    try:
        body = response.json()
    except Exception:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if not isinstance(error, dict):
        return ""
    code = error.get("code")
    return code if isinstance(code, str) else ""


def _response_text(response: Any) -> str:
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else ""


def rejection_code_of(response: Any) -> str:
    """A diagnostic code for a REJECTED submission.

    A deterministic rejection (HTTP 422) is answered with the engine's 5D
    envelope rather than an ``error`` envelope, so ``error.code`` is absent.
    Prefer the real code when there is one, else the engine's own terminal
    status, and only then a generic label.
    """
    code = error_code_of(response)
    if code:
        return code
    try:
        body = response.json()
    except Exception:
        return "CANDIDATE_REJECTED"
    if isinstance(body, dict):
        for key in ("status", "api_status"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return "CANDIDATE_REJECTED"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerConfig:
    """Everything the worker needs. No default here points at production."""

    api_base_url: str
    api_key: str
    worker_id: str
    bridge_url: str = DEFAULT_BRIDGE_URL
    bridge_key: Optional[str] = None
    bridge_paths: Tuple[str, ...] = DEFAULT_BRIDGE_PATHS
    bridge_timeout_s: float = 120.0
    bridge_max_response_bytes: int = MAX_BRIDGE_RESPONSE_BYTES
    model_url: Optional[str] = None
    model_id: str = DEFAULT_MODEL_ID
    model_key: Optional[str] = None
    model_timeout_s: float = DEFAULT_MODEL_TIMEOUT_S
    model_dialect: str = MODEL_DIALECT_CHAT
    max_result_bytes: int = MAX_RESULT_BYTES
    request_timeout_s: float = 30.0
    poll_interval_s: float = 5.0
    max_poll_interval_s: float = 60.0
    max_backoff_s: float = 60.0
    heartbeat_tick_s: float = 0.25
    shutdown_timeout_s: float = 10.0


@dataclass(frozen=True)
class LeaseProof:
    """The wire proof ``{"owner": ..., "generation": N}``."""

    owner: str
    generation: int

    def to_wire(self) -> Dict[str, Any]:
        return {"owner": self.owner, "generation": self.generation}


class LeaseState:
    """The worker's client-side view of ONE lease.

    Holds the confirmed deadline (extended only by a *confirmed* renewal) and
    whether the worker has decided the lease is gone. It is advisory: the
    server's generation fence remains the authority at commit time.
    """

    def __init__(
        self,
        proof: LeaseProof,
        *,
        lease_seconds: float,
        renew_every_seconds: float,
        renew_url: str,
        result_url: str,
        now: Callable[[], float],
    ) -> None:
        self.proof = proof
        self.lease_seconds = float(lease_seconds)
        self.renew_every_seconds = float(renew_every_seconds)
        self.renew_url = renew_url
        self.result_url = result_url
        self._now = now
        self._lock = threading.Lock()
        self._deadline = now() + self.lease_seconds
        self._next_renew_at = now() + self.renew_every_seconds
        self._lost_reason: Optional[str] = None
        self.renewals = 0
        self.renewal_failures = 0
        # Set exactly once, when the lease is declared lost. Tests and the
        # shutdown path wait on this instead of sleeping.
        self.lost_event = threading.Event()

    # -- reads ------------------------------------------------------------

    @property
    def owner(self) -> str:
        return self.proof.owner

    @property
    def generation(self) -> int:
        return self.proof.generation

    @property
    def is_lost(self) -> bool:
        with self._lock:
            return self._lost_reason is not None

    @property
    def lost_reason(self) -> Optional[str]:
        with self._lock:
            return self._lost_reason

    @property
    def deadline(self) -> float:
        with self._lock:
            return self._deadline

    def renew_due(self, now: Optional[float] = None) -> bool:
        moment = self._now() if now is None else now
        with self._lock:
            return self._lost_reason is None and moment >= self._next_renew_at

    # -- transitions ------------------------------------------------------

    def mark_lost(self, reason: str) -> None:
        with self._lock:
            if self._lost_reason is None:
                self._lost_reason = reason
                logger.warning("lease %s/%s lost: %s", self.proof.owner, self.proof.generation, reason)
                self.lost_event.set()

    def fail_if_expired(self, now: Optional[float] = None) -> None:
        """Fail closed when the confirmed deadline has passed un-renewed."""
        moment = self._now() if now is None else now
        with self._lock:
            expired = self._lost_reason is None and moment >= self._deadline
        if expired:
            self.mark_lost(
                f"lease deadline passed without a confirmed renewal (deadline={self.deadline:.3f})"
            )

    def ensure_held(self, now: Optional[float] = None) -> None:
        """Raise :class:`LeaseLost` unless this worker may still submit.

        Called immediately before a submission. It cannot make the submission
        atomic — the server fence does that — it only avoids sending work the
        worker already knows is worthless.
        """
        if self.is_lost:
            raise LeaseLost(self.lost_reason or "lease lost")
        self.fail_if_expired(now)
        if self.is_lost:
            raise LeaseLost(self.lost_reason or "lease lost")

    def note_renewed(self, now: Optional[float] = None) -> None:
        """Record a CONFIRMED renewal: extend the deadline, not a guess."""
        moment = self._now() if now is None else now
        with self._lock:
            if self._lost_reason is not None:
                return
            self._deadline = moment + self.lease_seconds
            self._next_renew_at = moment + self.renew_every_seconds
            self.renewals += 1

    def note_renewal_failure(self, detail: str, now: Optional[float] = None) -> None:
        """Record a failed renewal attempt (transport-level, retryable).

        Repeated failures are tolerated ONLY until the confirmed deadline: at
        that point the lease is gone as far as this worker can tell, and its
        candidate must be discarded.
        """
        moment = self._now() if now is None else now
        with self._lock:
            if self._lost_reason is not None:
                return
            self.renewal_failures += 1
            self._next_renew_at = moment + self.renew_every_seconds
            expired = moment >= self._deadline
        logger.warning("lease renewal failed (%s); scheduling the next attempt", detail)
        if expired:
            self.mark_lost(f"renewal deadline passed after {self.renewal_failures} failure(s): {detail}")


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class LeaseHeartbeat(threading.Thread):
    """Renews ONE lease on its own thread until it is lost or stopped.

    TWO signals, deliberately kept apart:

      ``stop_event``    per-JOB. Set when this heartbeat's own job is finished
                        (``stop_heartbeat``). It must never be the worker-wide
                        shutdown event: a job ending is not the worker ending.
      ``shutdown_event`` worker-wide. Set by ``OutboundInferenceWorker.stop()``,
                        so an operator shutdown still tears down a heartbeat
                        that is in flight.

    The thread never touches the candidate, and stopping it is always safe: it
    waits on an event rather than on a network call, so shutdown does not depend
    on a renewal returning.
    """

    def __init__(
        self,
        *,
        state: LeaseState,
        renew: Callable[[], None],
        stop_event: threading.Event,
        tick_s: float = 0.25,
        now: Callable[[], float] = time.monotonic,
        shutdown_event: Optional[threading.Event] = None,
    ) -> None:
        super().__init__(name="platrixa-lease-heartbeat", daemon=True)
        self.state = state
        self._renew = renew
        # NOTE: never name this ``_stop`` — ``threading.Thread`` owns a private
        # ``_stop()`` method that ``join()`` calls, and shadowing it breaks the
        # join with "TypeError: 'Event' object is not callable".
        self._stop_signal = stop_event
        self._shutdown = shutdown_event if shutdown_event is not None else threading.Event()
        self._tick = max(0.001, float(tick_s))
        self._now = now

    def request_stop(self) -> None:
        """Ask this heartbeat to exit (per-job teardown)."""
        self._stop_signal.set()

    def run(self) -> None:  # pragma: no cover - exercised via events in tests
        while not self._stop_signal.is_set() and not self._shutdown.is_set():
            if self._stop_signal.wait(self._tick):
                return
            if self._shutdown.is_set() or self.state.is_lost:
                return
            self._tick_once()

    def _tick_once(self) -> None:
        now = self._now()
        if not self.state.renew_due(now):
            # Nothing due yet, but a deadline that has already passed (a very
            # short lease, or a stalled clock) must still fail closed.
            self.state.fail_if_expired(now)
            return
        try:
            self._renew()
        except LeaseLost as exc:
            # The server refused the renewal: this worker is fenced out.
            self.state.mark_lost(f"renewal refused (LEASE_LOST): {exc}")
        except (MalformedResponseError, UnsafeEndpointError) as exc:
            # A renewal we cannot interpret is not proof of ownership. Fail
            # closed rather than assume the lease is still ours.
            self.state.mark_lost(f"renewal proof unusable: {exc}")
        except WorkerError as exc:
            self.state.note_renewal_failure(str(exc))
        else:
            self.state.note_renewed(self._now())


# ---------------------------------------------------------------------------
# Claim shapes
# ---------------------------------------------------------------------------


@dataclass
class ClaimedJob:
    """One claimed inference job, with its live lease state."""

    job_id: str
    request_id: Optional[str]
    raw_input: str
    state: LeaseState

    @property
    def lease(self) -> LeaseState:
        return self.state


@dataclass
class ClaimResult:
    """A claim outcome: either explicit NO_WORK or exactly one job."""

    no_work: bool
    retry_after_seconds: float = 5.0
    job: Optional[ClaimedJob] = None


class SubmitOutcome(dict):
    """What the result endpoint actually did with a candidate (Stage 2 O2).

    ``replayed`` is decided ONLY by the server's ``Idempotent-Replayed``
    header; the worker never infers it. ``True`` means the envelope is a
    previously committed authoritative result being replayed — this candidate
    did not become authoritative. ``False`` means no replay was claimed, i.e.
    the server performed a fresh fenced commit at this owner/generation.

    A ``dict`` subclass on purpose: every existing caller still receives the
    server's envelope unchanged (equality, indexing, ``json.dumps`` all behave
    exactly as before), while the replay decision travels ALONGSIDE the payload
    instead of being guessed from it. The distinction is reporting-only —
    nothing on the device can change what was committed — but a device operator
    must not be told that a discarded candidate was accepted.
    """

    def __init__(self, envelope: Dict[str, Any], replayed: bool) -> None:
        super().__init__(envelope)
        self.replayed = bool(replayed)

    @property
    def accepted(self) -> bool:
        """True only for a FRESH acceptance (never for a replay)."""
        return not self.replayed


# ---------------------------------------------------------------------------
# Run report
# ---------------------------------------------------------------------------
# Why a run ended with the count it did. The legacy summary line
# ("worker stopped; N job(s) submitted and accepted") is printed by an idle
# server, by a run whose every claim was refused, and by a run that claimed a
# job it could not commit — so the counters below record which of those
# actually happened. They are observations only: nothing here is inferred from
# silence, and no counter changes what the server decides.

RUN_REPORT_KEYS: Tuple[str, ...] = (
    "claims",           # claim requests actually sent
    "claim_failures",   # claim attempts that produced no claim outcome
    "no_work",          # explicit NO_WORK answers
    "claimed",          # jobs handed to this worker
    "accepted",         # fresh fenced commits (== run_forever's return value)
    "replayed",         # candidates the server reported as already committed
    "rejected",         # submission refused by the server (candidate discarded)
    "lease_lost",       # fenced out / lapsed before committing
    "bridge_failures",  # bridge unreachable or response refused (never submitted)
    "unknown",          # submission response unusable or transport-ambiguous
)


def format_run_report(report: Dict[str, int]) -> str:
    """One operator-facing line: counts plus the outcome they add up to.

    Pure formatting over what :meth:`OutboundInferenceWorker.run_forever`
    observed. The ``outcome:`` clause is a description of the counters, not a
    guess about the server: "NO_WORK only" is emitted only when the server
    actually answered NO_WORK and no job was claimed.
    """
    claims = int(report.get("claims", 0))
    failures = int(report.get("claim_failures", 0))
    no_work = int(report.get("no_work", 0))
    claimed = int(report.get("claimed", 0))
    accepted = int(report.get("accepted", 0))
    replayed = int(report.get("replayed", 0))
    rejected = int(report.get("rejected", 0))
    lease_lost = int(report.get("lease_lost", 0))
    bridge = int(report.get("bridge_failures", 0))
    unknown = int(report.get("unknown", 0))

    if claims == 0:
        outcome = "no claim was attempted (poll budget 0, or a stop was already requested)"
    elif claimed == 0 and failures == claims:
        outcome = (
            f"every claim attempt failed ({failures}); "
            "the server returned no claim outcome"
        )
    elif claimed == 0:
        also = f"; {failures} claim attempt(s) also failed" if failures else ""
        outcome = f"NO_WORK only — the server reported no runnable job for this tenant{also}"
    else:
        outcome = (
            f"claimed {claimed} job(s): {accepted} accepted, {replayed} replayed, "
            f"{rejected} rejected, {lease_lost} lease-lost, "
            f"{bridge} bridge-failure(s), {unknown} unknown"
        )

    return (
        f"claims={claims} claim-failures={failures} no-work={no_work} claimed={claimed} "
        f"accepted={accepted} replayed={replayed} rejected={rejected} "
        f"lease-lost={lease_lost} bridge-failures={bridge} unknown={unknown} "
        f"— outcome: {outcome}"
    )


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


class OutboundInferenceWorker:
    """Claims inference jobs outbound, bridges them locally, and submits them."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        session: Optional[Any] = None,
        bridge_session: Optional[Any] = None,
        model_session: Optional[Any] = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._config = config
        self._api_base_url = normalize_api_base_url(config.api_base_url)
        self._bridge_base_url = normalize_bridge_url(config.bridge_url)
        self._api_key = (config.api_key or "").strip()
        if not self._api_key:
            raise WorkerConfigError(f"no API key configured ({API_KEY_ENV} / --api-key)")
        self._worker_id = (config.worker_id or "").strip()
        if not BOUNDED_IDENTIFIER_RE.match(self._worker_id):
            raise WorkerConfigError(
                "worker id must match ^[A-Za-z0-9._-]{1,128}$ (the server discards anything else, "
                "which would make every renewal proof mismatch)"
            )
        self._bridge_key = (config.bridge_key or "").strip() or None
        self._bridge_paths = tuple(config.bridge_paths) or DEFAULT_BRIDGE_PATHS
        # Direct model endpoint (optional): validated exactly like the bridge.
        self._model_base_url = normalize_model_url(config.model_url)
        self._model_id = (config.model_id or "").strip() or DEFAULT_MODEL_ID
        if not BOUNDED_IDENTIFIER_RE.match(self._model_id):
            raise WorkerConfigError(
                "model id must match ^[A-Za-z0-9._-]{1,128}$ "
                f"(got {self._model_id!r})"
            )
        self._model_key = (config.model_key or "").strip() or None
        self._model_timeout_s = float(config.model_timeout_s)
        if self._model_timeout_s <= 0:
            raise WorkerConfigError(
                f"model timeout must be positive (got {config.model_timeout_s!r})"
            )
        dialect = (config.model_dialect or "").strip().lower()
        if dialect not in MODEL_DIALECTS:
            raise WorkerConfigError(
                f"model dialect must be one of {MODEL_DIALECTS} (got {config.model_dialect!r})"
            )
        self._model_dialect = dialect
        self._now = now
        self._session = session if session is not None else requests.Session()
        self._bridge_session = bridge_session if bridge_session is not None else requests.Session()
        self._model_session = model_session if model_session is not None else requests.Session()
        # ONE shutdown signal, shared with the heartbeat, so stopping the worker
        # also stops renewals without waiting on a network call.
        self._stop_event = threading.Event()
        self._sleep = sleep if sleep is not None else self._stop_event.wait
        # Stage 2 O2 reporting: fresh acceptances vs idempotent replays, kept
        # apart so a run log cannot claim more committed results than it made.
        self._last_run_stats: Dict[str, int] = {"accepted": 0, "replayed": 0}
        # Claim-stage diagnostics: distinguishes an idle queue from a run that
        # never got a claim answer at all (see ``format_run_report``).
        self._last_run_report: Dict[str, int] = {key: 0 for key in RUN_REPORT_KEYS}

    # -- introspection ----------------------------------------------------

    @property
    def config(self) -> WorkerConfig:
        return self._config

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def stop_event(self) -> threading.Event:
        """The shutdown signal. Shared with the heartbeat by design."""
        return self._stop_event

    @property
    def last_run_stats(self) -> Dict[str, int]:
        """``{accepted, replayed}`` from the last ``run_forever`` (Stage 2 O2)."""
        return dict(self._last_run_stats)

    @property
    def last_run_report(self) -> Dict[str, int]:
        """The claim/processing counters behind the last ``run_forever``.

        Unlike :attr:`last_run_stats` (a Stage 2 O2 contract other code reads),
        this is diagnostic: it says whether the run saw NO_WORK, whether its
        claims failed, and what happened to the jobs it did claim.
        """
        return dict(self._last_run_report)

    # -- secret hygiene ---------------------------------------------------

    def _redact(self, text: str) -> str:
        out = str(text)
        for secret in (self._api_key, self._bridge_key, self._model_key):
            if secret:
                out = out.replace(secret, "***")
        return out

    def _api_headers(self) -> Dict[str, str]:
        """API credentials ONLY. The bridge key is never sent to the API."""
        return {
            API_KEY_HEADER: self._api_key,
            WORKER_ID_HEADER: self._worker_id,
            "Content-Type": "application/json",
        }

    def _bridge_headers(self) -> Dict[str, str]:
        """Bridge credentials ONLY. The tenant API key is never sent to the bridge."""
        headers = {"Content-Type": "application/json"}
        if self._bridge_key:
            headers[BRIDGE_KEY_HEADER] = self._bridge_key
        return headers

    def _model_headers(self) -> Dict[str, str]:
        """Model endpoint credentials ONLY. The tenant API key never reaches the model."""
        headers = {"Content-Type": "application/json"}
        if self._model_key:
            headers["Authorization"] = f"Bearer {self._model_key}"
        return headers

    # -- transport --------------------------------------------------------

    def _post(
        self,
        url: str,
        payload: Optional[Dict[str, Any]],
        *,
        headers: Dict[str, str],
        timeout: float,
        session: Optional[Any] = None,
    ) -> Any:
        client = session if session is not None else self._session
        try:
            if payload is None:
                return client.post(url, headers=headers, timeout=timeout)
            return client.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise TransportError(self._redact(f"POST {url} failed: {exc}")) from exc

    def _api_post(self, url: str, payload: Optional[Dict[str, Any]]) -> Any:
        return self._post(
            url,
            payload,
            headers=self._api_headers(),
            timeout=float(self._config.request_timeout_s),
        )

    # -- claim ------------------------------------------------------------

    def claim(self) -> ClaimResult:
        """Claim at most ONE inference job for this tenant.

        An explicit NO_WORK answer carries a bounded pacing hint; the worker
        backs off instead of spinning.
        """
        response = self._api_post(self._api_base_url + CLAIM_PATH, None)
        status = int(response.status_code)
        if status == 401 or status == 403:
            raise ApiError(status, error_code_of(response) or "UNAUTHORIZED",
                           "the API rejected this tenant key", retryable=False)
        if status == 503:
            # Two different 503s reach here: the admission guard refusing the
            # request because metering is unreachable (METERING_UNAVAILABLE),
            # and the queue's own store being down (ASYNC_UNAVAILABLE). The
            # status and the code are unchanged; only the human message stops
            # blaming the durable store when the store was never asked.
            claim_code = error_code_of(response) or "ASYNC_UNAVAILABLE"
            claim_reason = (
                "the metering/admission boundary is unavailable; no claim outcome"
                if claim_code == "METERING_UNAVAILABLE"
                else "the durable store is unavailable"
            )
            raise ApiError(status, claim_code, claim_reason, retryable=True)
        if status != 200:
            raise ApiError(status, error_code_of(response) or "UNEXPECTED_STATUS",
                           _response_text(response)[:200], retryable=status >= 500)

        body = self._decode_json_object(response, what="claim response")
        state = body.get("status")
        if state == STATUS_NO_WORK:
            return ClaimResult(
                no_work=True,
                retry_after_seconds=self._clamp_poll(body.get("retry_after_seconds")),
            )
        if state != STATUS_CLAIMED:
            raise MalformedResponseError(f"claim response has an unknown status: {state!r}")

        job = body.get("job")
        if not isinstance(job, dict):
            raise MalformedResponseError("claim response has no job object")
        return ClaimResult(no_work=False, job=self._build_claimed_job(job))

    def _clamp_poll(self, value: Any) -> float:
        try:
            hint = float(value)
        except (TypeError, ValueError):
            hint = self._config.poll_interval_s
        if hint != hint or hint <= 0:
            hint = self._config.poll_interval_s
        return min(max(hint, 0.05), self._config.max_poll_interval_s)

    def _build_claimed_job(self, job: Dict[str, Any]) -> ClaimedJob:
        """Validate a claimed job + its lease, then build the live lease state.

        Everything the server sends is validated before it is trusted: the job
        id, the raw input, the lease proof, the timings, and both URLs.
        """
        job_id = _bounded_identifier(job.get("job_id"), what="job_id")
        raw_input = job.get("raw_input")
        if not isinstance(raw_input, str) or not raw_input.strip():
            raise MalformedResponseError("claimed job has no usable raw_input")

        request_id = job.get("request_id")
        if request_id is not None and not isinstance(request_id, str):
            raise MalformedResponseError("claimed job request_id must be a string when present")

        lease = job.get("lease")
        if not isinstance(lease, dict):
            raise MalformedResponseError("claimed job has no lease object")

        owner = _bounded_identifier(lease.get("owner"), what="lease owner")
        generation = _generation(lease.get("generation"))
        lease_seconds = _positive_number(lease.get("lease_seconds"), what="lease_seconds")
        renew_every = _positive_number(lease.get("renew_every_seconds"), what="renew_every_seconds")
        if renew_every > lease_seconds:
            raise MalformedResponseError(
                "renew_every_seconds must not exceed lease_seconds "
                f"(got {renew_every} > {lease_seconds})"
            )

        renew_url = resolve_lease_endpoint(self._api_base_url, lease.get("renew_url"), job_id, RENEW_SUFFIX)
        result_url = resolve_lease_endpoint(self._api_base_url, lease.get("result_url"), job_id, RESULT_SUFFIX)

        state = LeaseState(
            LeaseProof(owner=owner, generation=generation),
            lease_seconds=lease_seconds,
            renew_every_seconds=renew_every,
            renew_url=renew_url,
            result_url=result_url,
            now=self._now,
        )
        return ClaimedJob(
            job_id=job_id,
            request_id=request_id if isinstance(request_id, str) and request_id else None,
            raw_input=raw_input,
            state=state,
        )

    # -- renewal ----------------------------------------------------------

    def renew_lease(self, state: LeaseState) -> None:
        """Renew ONE lease, proving owner + generation.

        Raises :class:`LeaseLost` on 409 (the fence refused us), and a
        :class:`MalformedResponseError` when the returned proof is not ours —
        a renewal we cannot verify is not evidence of ownership.
        """
        response = self._api_post(state.renew_url, {"lease": state.proof.to_wire()})
        status = int(response.status_code)
        if status == 409:
            raise LeaseLost(
                self._error_code(response) or CODE_LEASE_LOST
            )
        if status == 404:
            raise ApiError(404, self._error_code(response) or CODE_JOB_NOT_FOUND,
                           "no such job for this tenant", retryable=False)
        if status == 503:
            raise ApiError(503, self._error_code(response) or "ASYNC_UNAVAILABLE",
                           "the durable store is unavailable", retryable=True)
        if status != 200:
            raise ApiError(status, self._error_code(response) or "UNEXPECTED_STATUS",
                           _response_text(response)[:200], retryable=status >= 500)

        body = self._decode_json_object(response, what="renewal response")
        lease = body.get("lease")
        if not isinstance(lease, dict):
            raise MalformedResponseError("renewal response has no lease object")
        owner = lease.get("owner")
        generation = lease.get("generation")
        if owner != state.proof.owner or generation != state.proof.generation:
            raise MalformedResponseError(
                "renewal proof does not match this worker: "
                f"expected {state.proof.owner}/{state.proof.generation}, got {owner!r}/{generation!r}"
            )
        renewed_seconds = lease.get("lease_seconds")
        if renewed_seconds is not None:
            _positive_number(renewed_seconds, what="renewal lease_seconds")

    def _error_code(self, response: Any) -> str:
        return error_code_of(response)

    # -- heartbeat lifecycle ----------------------------------------------

    def start_heartbeat(self, state: LeaseState) -> LeaseHeartbeat:
        """Start renewing ``state`` on its own thread.

        The heartbeat gets its OWN stop event (per job) plus the worker-wide
        shutdown event, so finishing one job cannot stop the worker and stopping
        the worker still tears down an in-flight heartbeat.
        """
        heartbeat = LeaseHeartbeat(
            state=state,
            renew=lambda: self.renew_lease(state),
            stop_event=threading.Event(),
            tick_s=float(self._config.heartbeat_tick_s),
            now=self._now,
            shutdown_event=self._stop_event,
        )
        heartbeat.start()
        return heartbeat

    def stop_heartbeat(self, heartbeat: LeaseHeartbeat) -> bool:
        """Signal and join THIS job's heartbeat. True when it exited cleanly.

        Deliberately does NOT set the worker-wide shutdown event: this runs on
        every job boundary, and setting the shutdown signal there would end the
        poll loop after a single job. Shutdown never waits on a renewal either —
        the thread waits on an event, so it returns promptly even if a renewal is
        in flight or the network is black-holing packets.
        """
        heartbeat.request_stop()
        heartbeat.join(timeout=float(self._config.shutdown_timeout_s))
        return not heartbeat.is_alive()

    # -- bridge -----------------------------------------------------------

    def call_bridge(self, raw_input: str) -> Dict[str, Any]:
        """POST the raw input to the LOCAL model bridge; return its FULL body.

        Nothing is parsed, extracted, repaired, or rewritten: the body is passed
        to the API verbatim so the server's shared parser sees exactly what the
        model produced. ``/interpret`` is tried first and ``/`` only when the
        bridge answers 404/405 (the existing fallback rule); a connection error
        short-circuits, because the paths share one host.
        """
        if not isinstance(raw_input, str) or not raw_input.strip():
            raise BridgeResponseError("refusing to bridge an empty input")

        # Direct model mode: when a loopback model endpoint is configured the
        # worker calls the model itself; otherwise the device bridge.
        if self._model_base_url is not None:
            return self.call_model(raw_input)

        connection_error: Optional[str] = None
        for path in self._bridge_paths:
            url = f"{self._bridge_base_url}{path}"
            try:
                response = self._post(
                    url,
                    {"text": raw_input},
                    headers=self._bridge_headers(),
                    timeout=float(self._config.bridge_timeout_s),
                    session=self._bridge_session,
                )
            except TransportError as exc:
                connection_error = self._redact(str(exc))
                break  # same host — further paths cannot help

            status = int(response.status_code)
            if status == 200:
                return self._decode_bridge_body(response)
            if status in (404, 405):
                continue  # wrong mount point; try the next candidate path
            if status == 422:
                raise BridgeResponseError(
                    "the bridge rejected the request shape (422): "
                    + self._redact(_response_text(response))[:200]
                )
            if status == 503:
                raise BridgeUnavailableError(
                    "the bridge reports the model unavailable (503) on "
                    f"{path}: {self._redact(_response_text(response))[:200]}"
                )
            raise BridgeUnavailableError(
                f"bridge error {status} on {path}: {self._redact(_response_text(response))[:200]}"
            )

        raise BridgeUnavailableError(
            connection_error or f"no bridge route matched; tried {list(self._bridge_paths)}"
        )

    # -- direct model endpoint (the phone adapter) ------------------------

    def call_model(self, raw_input: str) -> Dict[str, Any]:
        """POST the raw input to the phone's OpenAI-compatible llama-server.

        The model server runs on the same device as this worker, so the
        endpoint is loopback-only (validated by normalize_model_url); there is
        no inbound port, public endpoint, tunnel, or relay. The prompt is the
        byte-exact Phase-H Alpaca SFT prompt (build_model_prompt mirrors the
        shared contract; the test suite enforces byte-equality).

        Dialects:
          * "chat"       → POST /v1/chat/completions with the prompt as ONE user
                           message (the phone app's native endpoint, OpenAI-style);
          * "completion" → POST /v1/completions with the raw prompt, the exact
                           SFT framing the Phase-H adapter was trained on
                           (platrixa_prompt.py's prompt-fidelity contract).

        STREAMING: the request always asks for a non-streamed answer, but a
        model that streams anyway is handled by collecting EVERY ``data:``
        chunk until ``[DONE]`` and only then parsing — an incomplete stream
        (no ``[DONE]``) fails closed and is never submitted.

        Returns the device-bridge response BODY SHAPE so the submission path,
        the server's shared parser, and the deterministic kernel are unchanged:
        {"interpretation_text", "completion_status", "finish_reason", "model"}.
        No field is added, defaulted, or repaired here; a truncated or
        malformed candidate surfaces as BridgeResponseError and is discarded.
        """
        prompt = build_model_prompt(raw_input)
        if not is_alpaca_prompt(prompt):
            # Cannot happen with the mirrored constants; fail closed anyway so
            # a future edit cannot silently substitute a different prompt.
            raise BridgeResponseError("the canonical Alpaca prompt guard failed")

        if self._model_dialect == MODEL_DIALECT_CHAT:
            url = f"{self._model_base_url}{CHAT_COMPLETIONS_PATH}"
            payload: Dict[str, Any] = {
                "model": self._model_id,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": MODEL_TEMPERATURE,
                "top_p": MODEL_TOP_P,
                "max_tokens": MODEL_MAX_NEW_TOKENS,
            }
        else:
            url = f"{self._model_base_url}{COMPLETIONS_PATH}"
            payload = {
                "model": self._model_id,
                "prompt": prompt,
                "stream": False,
                "temperature": MODEL_TEMPERATURE,
                "top_p": MODEL_TOP_P,
                "max_tokens": MODEL_MAX_NEW_TOKENS,
                "stop": list(MODEL_STOP_SEQUENCES),
            }

        try:
            response = self._post(
                url,
                payload,
                headers=self._model_headers(),
                timeout=self._model_timeout_s,
                session=self._model_session,
            )
        except TransportError as exc:
            raise BridgeUnavailableError(
                f"model endpoint unreachable: {self._redact(str(exc))}"
            ) from exc
        status = int(response.status_code)
        if status != 200:
            raise BridgeUnavailableError(
                f"model endpoint returned HTTP {status}: "
                f"{self._redact(_response_text(response))[:200]}"
            )

        text = _response_text(response)
        size = len(text.encode("utf-8", errors="replace"))
        if size > int(self._config.bridge_max_response_bytes):
            raise BridgeResponseError(
                f"model response is {size} bytes; the limit is "
                f"{self._config.bridge_max_response_bytes}"
            )

        content, finish_reason = self._parse_model_response(text)
        return {
            "interpretation_text": content,
            "completion_status": "TRUNCATED" if finish_reason == "length" else "COMPLETE",
            "finish_reason": finish_reason,
            "model": {
                "provider": "android-llama-server",
                "model": self._model_id,
                "dialect": self._model_dialect,
                "artifact_identity": "PROVENANCE_UNKNOWN",
            },
        }

    def _parse_model_response(self, text: str) -> Tuple[str, Optional[str]]:
        """Extract (content, finish_reason) from a 200; fail closed on anything partial."""
        if text.lstrip().startswith("data:"):
            return self._collect_model_stream(text)
        try:
            body = json.loads(text)
        except ValueError as exc:
            raise BridgeResponseError(f"model response is not valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise BridgeResponseError("model response is not a JSON object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise BridgeResponseError(
                f"model response has no choices; keys={sorted(body.keys())}"
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise BridgeResponseError("model response choice is not an object")
        content: Optional[str] = None
        message = first.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            content = message["content"]
        elif isinstance(first.get("text"), str):
            content = first["text"]
        if content is None or not content.strip():
            raise BridgeResponseError("model returned empty or missing completion text")
        finish = first.get("finish_reason")
        finish_reason = finish if isinstance(finish, str) and finish else None
        return content, finish_reason

    def _collect_model_stream(self, text: str) -> Tuple[str, Optional[str]]:
        """Collect streamed chunks; parse ONLY after [DONE]. Incomplete → fail closed."""
        pieces: List[str] = []
        finish_reason: Optional[str] = None
        saw_done = False
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data:
                continue
            if data == "[DONE]":
                saw_done = True
                continue
            try:
                chunk = json.loads(data)
            except ValueError as exc:
                raise BridgeResponseError(f"stream chunk is not valid JSON: {exc}") from exc
            if not isinstance(chunk, dict):
                raise BridgeResponseError("stream chunk is not a JSON object")
            choices = chunk.get("choices")
            if not isinstance(choices, list):
                continue  # e.g. a usage-only chunk contributes nothing
            for choice in choices:
                if not isinstance(choice, dict):
                    raise BridgeResponseError("stream choice is not an object")
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    pieces.append(delta["content"])
                if isinstance(choice.get("text"), str):
                    pieces.append(choice["text"])
                reason = choice.get("finish_reason")
                if isinstance(reason, str) and reason:
                    finish_reason = reason
        if not saw_done:
            raise BridgeResponseError(
                "model stream ended without [DONE]; the output is incomplete and "
                "will not be submitted"
            )
        content = "".join(pieces)
        if not content.strip():
            raise BridgeResponseError("streamed model output was empty")
        return content, finish_reason

    def _decode_bridge_body(self, response: Any) -> Dict[str, Any]:
        """Fail closed on anything that is not a bounded JSON object."""
        text = _response_text(response)
        size = len(text.encode("utf-8", errors="replace"))
        if size > int(self._config.bridge_max_response_bytes):
            raise BridgeResponseError(
                f"bridge response is {size} bytes; the limit is {self._config.bridge_max_response_bytes}"
            )
        try:
            body = json.loads(text) if text.strip() else None
        except ValueError as exc:
            raise BridgeResponseError(f"bridge 200 response is not valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise BridgeResponseError(
                f"bridge 200 response is not a JSON object (got {type(body).__name__})"
            )
        return body

    # -- submission -------------------------------------------------------

    def submit_result(self, job: ClaimedJob, bridge_body: Dict[str, Any]) -> SubmitOutcome:
        """Submit the COMPLETE bridge body under this lease.

        The payload is ``{"lease": {...}, "bridge_body": {...}}`` exactly as the
        route documents. A 409 means the server fence refused the commit: the
        result is discarded and :class:`LeaseLost` is raised — never retried at
        the same generation.

        Stage 2 O2: the answer is a :class:`SubmitOutcome`, so a 200 that the
        server labelled as an idempotent replay is never reported as a fresh
        acceptance. An uninterpretable replay marker fails closed
        (:class:`MalformedResponseError`): the worker will not guess whether its
        own candidate became the authoritative result.
        """
        state = job.state
        if not isinstance(bridge_body, dict):
            raise BridgeResponseError("refusing to submit a non-object bridge body")

        state.ensure_held(self._now())

        payload: Dict[str, Any] = {"lease": state.proof.to_wire(), "bridge_body": bridge_body}
        if job.request_id:
            payload["request_id"] = job.request_id
        encoded = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        if len(encoded) > int(self._config.max_result_bytes):
            raise BridgeResponseError(
                f"refusing to submit a {len(encoded)}-byte result; the limit is {self._config.max_result_bytes}"
            )

        response = self._api_post(state.result_url, payload)
        status = int(response.status_code)
        if status == 409:
            code = error_code_of(response) or CODE_LEASE_LOST
            state.mark_lost(f"the server refused the submission at this owner/generation ({code})")
            raise LeaseLost(code)
        if status == 404:
            raise ApiError(404, error_code_of(response) or CODE_JOB_NOT_FOUND,
                           "no such job for this tenant", retryable=False)
        if status in (400, 413, 415, 422):
            # 400/413/415: the request shape was refused. 422: the deterministic
            # pipeline REFUSED the candidate and the server stored that terminal
            # envelope, so the job is finished — the candidate is discarded, not
            # retried, and the worker keeps serving other jobs.
            raise ApiError(status, rejection_code_of(response),
                           "the server rejected this submission; nothing was committed",
                           retryable=False)
        if status == 503:
            raise ApiError(503, error_code_of(response) or "SERVICE_UNAVAILABLE",
                           "the API could not validate the submission", retryable=True)
        if status != 200:
            raise ApiError(status, error_code_of(response) or "UNEXPECTED_STATUS",
                           _response_text(response)[:200], retryable=status >= 500)

        envelope = self._decode_json_object(response, what="result response")
        return SubmitOutcome(envelope, self._replay_flag(response))

    def _replay_flag(self, response: Any) -> bool:
        """Read the server's replay marker. Absent means "not replayed".

        The result route sets ``Idempotent-Replayed: true`` on exactly the two
        paths that return a previously committed envelope, and sets the header
        to nothing on a fresh commit (inference_api.py:757-763 and 900-912), so
        "absent" is the route's statement that this response was not a replay.
        Any OTHER value is undefined by the contract and is refused rather than
        guessed at.
        """
        headers = getattr(response, "headers", None) or {}
        raw = headers.get(REPLAY_HEADER)
        if raw is None:
            # requests' headers mapping is case-insensitive, but a test double's
            # plain dict is not: look up the canonical spelling as a fallback.
            for key, value in dict(headers).items():
                if str(key).lower() == REPLAY_HEADER.lower():
                    raw = value
                    break
        if raw is None:
            return False
        text = str(raw).strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
        raise MalformedResponseError(
            f"the server sent an unusable {REPLAY_HEADER} marker: {raw!r}"
        )

    # -- orchestration ----------------------------------------------------

    def process_job(self, job: ClaimedJob) -> SubmitOutcome:
        """Bridge, then submit, while the heartbeat keeps the lease alive.

        On ANY exit path the heartbeat is stopped and joined, so no renewal
        thread outlives the job it was serving.
        """
        # Check BEFORE spending model time: a lease already known lost is not
        # worth a bridge call. (The server fence, not this check, is authority.)
        job.state.ensure_held(self._now())
        heartbeat = self.start_heartbeat(job.state)
        try:
            bridge_body = self.call_bridge(job.raw_input)
            return self.submit_result(job, bridge_body)
        finally:
            self.stop_heartbeat(heartbeat)

    def run_forever(
        self,
        *,
        max_polls: Optional[int] = None,
        on_result: Optional[Callable[[ClaimedJob, Dict[str, Any]], None]] = None,
    ) -> int:
        """Bounded poll loop: claim → process → repeat, with backoff.

        Never spins: NO_WORK honours the server's pacing hint (clamped), and a
        transport failure backs off exponentially up to ``max_backoff_s``.

        Returns the number of FRESH acceptances (Stage 2 O2). A replayed result
        is counted separately in :attr:`last_run_stats` and never added here, so
        "N jobs accepted" cannot silently mean "N candidates committed".
        """
        completed = 0
        replayed = 0
        polls = 0
        # Fresh counters, assigned BEFORE the loop so an abnormal exit still
        # reports exactly how far the run got. Mutated in place.
        report = self._last_run_report = {key: 0 for key in RUN_REPORT_KEYS}
        backoff = float(self._config.poll_interval_s)
        while not self._stop_event.is_set():
            if max_polls is not None and polls >= max_polls:
                break
            polls += 1
            report["claims"] += 1
            try:
                claim = self.claim()
            except ApiError as exc:
                if not exc.retryable:
                    raise
                report["claim_failures"] += 1
                logger.warning("claim attempt %d failed (retryable): %s", report["claims"], exc)
                self._sleep(backoff)
                backoff = min(backoff * 2, float(self._config.max_backoff_s))
                continue
            except TransportError as exc:
                report["claim_failures"] += 1
                logger.warning("claim attempt %d transport failure: %s", report["claims"], exc)
                self._sleep(backoff)
                backoff = min(backoff * 2, float(self._config.max_backoff_s))
                continue

            backoff = float(self._config.poll_interval_s)
            if claim.no_work or claim.job is None:
                # Logged at INFO: an idle queue is normal, but it must be
                # OBSERVABLE. Without this line a NO_WORK run and a run whose
                # every claim was refused produce byte-identical output.
                report["no_work"] += 1
                logger.info(
                    "claim %d: NO_WORK (no runnable job for this tenant); next poll in %.2fs",
                    report["claims"], claim.retry_after_seconds,
                )
                self._sleep(claim.retry_after_seconds)
                continue

            job = claim.job
            report["claimed"] += 1
            try:
                envelope = self.process_job(job)
            except LeaseLost as exc:
                # Expected under contention: the job will be reclaimed by
                # whoever holds the lease now. Nothing was committed here.
                report["lease_lost"] += 1
                logger.info("job %s: lease lost, discarding candidate (%s)", job.job_id, exc)
                continue
            except BridgeUnavailableError as exc:
                report["bridge_failures"] += 1
                logger.warning("job %s: local bridge unavailable (%s)", job.job_id, exc)
                continue
            except BridgeResponseError as exc:
                # Fail closed: a malformed/oversized candidate is NEVER
                # submitted. The lease expires and the job is reclaimed.
                report["bridge_failures"] += 1
                logger.warning("job %s: unusable bridge response, nothing submitted (%s)", job.job_id, exc)
                continue
            except ApiError as exc:
                # A SUBMISSION-stage rejection is the server's decision about this
                # one job: 4xx means the candidate was processed and refused (the
                # job is terminal), 5xx is retryable. Neither is a reason to stop
                # serving, so the worker moves on to the next claim. Only the
                # CLAIM stage treats a non-retryable ApiError as fatal, because a
                # bad credential must not spin in a hot loop.
                report["rejected"] += 1
                logger.warning("job %s: submission rejected by the server (%s)", job.job_id, exc)
                continue
            except TransportError as exc:
                # Stage 2 O2: the submission may or may not have reached the
                # server. The outcome is UNKNOWN, so this is neither an
                # acceptance nor a rejection — it is not counted. The server is
                # idempotent on a committed result, so if the commit did land the
                # job is COMPLETED and any later observation replays it; if it
                # did not, the lease expires and the job is reclaimed. Nothing
                # is inferred from the silence.
                logger.warning(
                    "job %s: submission outcome UNKNOWN (transport failure); "
                    "not counted as accepted (%s)", job.job_id, exc,
                )
                report["unknown"] += 1
                continue
            except MalformedResponseError as exc:
                # A 200 we cannot interpret (bad envelope or undefined replay
                # marker) is not evidence of acceptance either. The candidate is
                # never resubmitted at this generation.
                report["unknown"] += 1
                logger.warning(
                    "job %s: submission response unusable; outcome not claimed (%s)",
                    job.job_id, exc,
                )
                continue

            # The server decides; this only reports what it said.
            if envelope.replayed:
                replayed += 1
                report["replayed"] += 1
                logger.info(
                    "job %s: result REPLAYED (an authoritative result already existed; "
                    "this candidate was not committed)", job.job_id,
                )
            else:
                completed += 1
                report["accepted"] += 1
                logger.info("job %s: result ACCEPTED (fresh fenced commit)", job.job_id)
            if on_result is not None:
                on_result(job, envelope)
        self._last_run_stats = {"accepted": completed, "replayed": replayed}
        return completed

    def stop(self) -> None:
        """Ask the poll loop and any in-flight heartbeat to exit.

        This is the ONLY place the worker-wide shutdown signal is set, so it
        always means "the operator wants this worker to stop" and never "a job
        finished".
        """
        self._stop_event.set()

    # -- helpers ----------------------------------------------------------

    def _decode_json_object(self, response: Any, *, what: str) -> Dict[str, Any]:
        try:
            body = response.json()
        except Exception as exc:
            raise MalformedResponseError(f"{what} is not valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise MalformedResponseError(f"{what} is not a JSON object")
        return body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_key_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def default_worker_id() -> str:
    """A bounded, device-local lease-owner label (never an authorization input)."""
    raw = re.sub(r"[^A-Za-z0-9._-]", "-", (os.uname().nodename if hasattr(os, "uname") else "device"))
    raw = raw.strip("-")[:96] or "device"
    return f"{raw}-{secrets.token_hex(3)}"


def build_config(args: argparse.Namespace) -> WorkerConfig:
    api_key = (args.api_key or "").strip()
    if not api_key and args.api_key_file:
        api_key = _read_key_file(args.api_key_file)
    if not api_key:
        api_key = (os.environ.get(API_KEY_ENV, "") or "").strip()
    model_key = (args.model_key or "").strip()
    if not model_key and args.model_key_file:
        model_key = _read_key_file(args.model_key_file)
    if not model_key:
        model_key = (os.environ.get(MODEL_KEY_ENV, "") or "").strip()
    raw_model_timeout = (os.environ.get(MODEL_TIMEOUT_ENV, "") or "").strip()
    if args.model_timeout is not None:
        model_timeout_s = float(args.model_timeout)
    elif raw_model_timeout:
        try:
            model_timeout_s = float(raw_model_timeout)
        except ValueError as exc:
            raise WorkerConfigError(
                f"{MODEL_TIMEOUT_ENV} must be a number, got {raw_model_timeout!r}"
            ) from exc
    else:
        model_timeout_s = DEFAULT_MODEL_TIMEOUT_S
    return WorkerConfig(
        api_base_url=args.api_base_url or os.environ.get(API_BASE_URL_ENV, ""),
        api_key=api_key,
        worker_id=args.worker_id or os.environ.get(WORKER_ID_ENV, "") or default_worker_id(),
        bridge_url=args.bridge_url or os.environ.get(BRIDGE_URL_ENV, "") or DEFAULT_BRIDGE_URL,
        bridge_key=args.bridge_key or os.environ.get(BRIDGE_KEY_ENV, "") or None,
        bridge_timeout_s=args.bridge_timeout,
        model_url=args.model_url or os.environ.get(MODEL_URL_ENV, "") or None,
        model_id=args.model_id or os.environ.get(MODEL_ID_ENV, "") or DEFAULT_MODEL_ID,
        model_key=model_key or None,
        model_timeout_s=model_timeout_s,
        model_dialect=args.model_dialect or os.environ.get(MODEL_DIALECT_ENV, "") or MODEL_DIALECT_CHAT,
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Platrixa outbound inference worker (Stage 2). Makes only outbound requests: "
                    "HTTPS to the API and loopback HTTP to the local model bridge.",
    )
    parser.add_argument("--api-base-url", default=None,
                        help=f"hosted API origin, https (or loopback http); env {API_BASE_URL_ENV}")
    parser.add_argument("--api-key", default=None,
                        help=f"tenant API key; env {API_KEY_ENV} (never logged)")
    parser.add_argument("--api-key-file", default=None, help="file containing the API key")
    parser.add_argument("--worker-id", default=None,
                        help=f"bounded lease-owner label; env {WORKER_ID_ENV}")
    parser.add_argument("--bridge-url", default=None,
                        help=f"loopback model bridge; env {BRIDGE_URL_ENV} (default {DEFAULT_BRIDGE_URL})")
    parser.add_argument("--bridge-key", default=None,
                        help=f"optional device bridge key; env {BRIDGE_KEY_ENV}")
    parser.add_argument("--bridge-timeout", type=float, default=120.0,
                        help="seconds to wait for the device model (default 120)")
    parser.add_argument("--model-url", default=None,
                        help=f"direct loopback llama-server root, e.g. http://127.0.0.1:11434; "
                             f"env {MODEL_URL_ENV} (unset = use the device bridge)")
    parser.add_argument("--model-id", default=None,
                        help=f"model id sent to the endpoint; env {MODEL_ID_ENV} "
                             f"(default {DEFAULT_MODEL_ID})")
    parser.add_argument("--model-key", default=None,
                        help=f"optional bearer key for the model endpoint; env {MODEL_KEY_ENV}")
    parser.add_argument("--model-key-file", default=None, help="file containing the model key")
    parser.add_argument("--model-timeout", type=float, default=None,
                        help=f"seconds to wait for the model; env {MODEL_TIMEOUT_ENV} "
                             f"(default {DEFAULT_MODEL_TIMEOUT_S})")
    parser.add_argument("--model-dialect", default=None, choices=list(MODEL_DIALECTS),
                        help=f"chat = /v1/chat/completions (default), completion = /v1/completions "
                             f"(exact SFT framing); env {MODEL_DIALECT_ENV}")
    parser.add_argument("--once", action="store_true", help="claim and process at most one job, then exit")
    parser.add_argument("--max-polls", type=int, default=None, help="stop after this many claim attempts")
    parser.add_argument("--verbose", action="store_true", help="log at INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = build_config(args)
        worker = OutboundInferenceWorker(config)
    except (WorkerConfigError, UnsafeEndpointError) as exc:
        # Configuration failures are the operator's problem: say exactly what
        # is wrong and exit non-zero without touching the network.
        print(f"worker configuration error: {exc}", file=sys.stderr)
        return 2

    max_polls = 1 if args.once else args.max_polls
    try:
        completed = worker.run_forever(max_polls=max_polls)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        worker.stop()
        print("interrupted; stopped cleanly", file=sys.stderr)
        return 130
    print(f"worker stopped; {completed} job(s) submitted and accepted")
    # The count alone cannot say WHY it is zero: an idle queue, a run whose
    # claims were all refused, and a run that could not commit all print the
    # line above. This second line is what distinguishes them.
    print(f"  run report: {format_run_report(worker.last_run_report)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
