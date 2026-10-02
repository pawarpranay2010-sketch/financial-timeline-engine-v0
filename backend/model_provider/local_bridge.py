"""
Platrixa — Local llama.cpp Bridge ModelProvider (local-only adapter)
====================================================================

WHY THIS EXISTS
---------------
The Android/Termux experiment serves a GGUF through `llama-server` and a small
FastAPI bridge (`local_llama_bridge.py`, device-side only) that answers on
127.0.0.1:8081 with an envelope like:

    {"interpretation_text": "<raw model JSON text>"}

Platrixa's existing remote provider (`RemoteHFModelProvider`) POSTs
{"text": ...} to `{url}/interpret` and expects:

    {"interpretation": {...18 fields...}, "model": {...}}

Nothing in the repository bridges those two shapes. This module is that bridge —
and nothing more. It is the smallest adapter that connects the device-side
llama.cpp bridge to Platrixa's EXISTING provider and validation architecture.

BOUNDARY RULES (this is an experiment path; production is untouched)
--------------------------------------------------------------------
  - NOT registered in `get_model_provider()`. Production provider selection
    (`PLATRIXA_MODEL_ENDPOINT_URL` → remote, else local in-process) is
    byte-for-byte unchanged. This class is instantiated explicitly by
    `scripts/local_bridge_acceptance.py` (and tests), never implicitly.
  - Reuses the existing FYJC prompt contract indirectly: the bridge/model side
    is responsible for building the byte-exact Alpaca prompt
    (`backend/model_provider/platrixa_prompt.py`); this adapter transports the
    raw input text and the raw model response only.
  - Preserves all 18 required fields: presence is enforced fail-closed
    (`MalformedOutputError` lists exactly which fields are missing).
  - Strict parsing: the model's JSON text must parse as-is. Markdown-fence or
    surrounding-text extraction uses the repo-sanctioned
    `base.extract_json_candidate` (extraction only — it never adds, repairs, or
    defaults any field). Unparseable output is rejected with the raw text
    recorded in the trace. No field is ever invented.
  - The model may never declare VERIFIED: `suggested_status == "VERIFIED"` is
    normalized to `REVIEW_REQUIRED` at this boundary (mirroring
    `RemoteHFModelProvider.interpret`), and the normalization is recorded in
    the trace — not hidden. The grounding gate's Rule 0 remains the backstop.
  - No accounting: forbidden accounting-truth keys are rejected with
    `ForbiddenAccountingFieldError`.
  - status() never makes a network call.

PROVENANCE (Gate 0 — reports/LOCAL_MODEL_PROVENANCE.md)
-------------------------------------------------------
The device GGUF is `PROVENANCE_UNKNOWN`: no manifest, no SHA-256 match, and the
build script names its outputs "phase-h" unconditionally. This provider's
default identity therefore reports PROVENANCE_UNKNOWN for every revision field
instead of borrowing the pinned Phase 6C revisions from base.py — no downstream
evidence chain can silently claim an unverified adapter identity.
Set PLATRIXA_LOCAL_BRIDGE_ARTIFACT (free text, e.g. "sha256:abc…") to record a
verified identity once Gate 0 is closed.

CONFIGURATION (all optional, all local-only)
--------------------------------------------
  PLATRIXA_LOCAL_BRIDGE_URL      default http://127.0.0.1:8081
  PLATRIXA_LOCAL_BRIDGE_PATH     request path; empty → try /interpret then /
  PLATRIXA_LOCAL_BRIDGE_TIMEOUT  seconds, default 120 (device speed ≈12 tok/s)
  PLATRIXA_LOCAL_BRIDGE_ARTIFACT artifact identity string, default PROVENANCE_UNKNOWN
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from backend.model_provider.base import (
    ForbiddenAccountingFieldError,
    InterpretationResult,
    MalformedOutputError,
    ModelUnavailableError,
    ProviderConfig,
    ProviderStatus,
    contains_forbidden_accounting_fields,
    extract_json_candidate,
)
from backend.model_provider.remote_hf import REQUIRED_FIELDS_18

BRIDGE_URL_ENV = "PLATRIXA_LOCAL_BRIDGE_URL"
BRIDGE_PATH_ENV = "PLATRIXA_LOCAL_BRIDGE_PATH"
BRIDGE_TIMEOUT_ENV = "PLATRIXA_LOCAL_BRIDGE_TIMEOUT"
BRIDGE_ARTIFACT_ENV = "PLATRIXA_LOCAL_BRIDGE_ARTIFACT"

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8081"
DEFAULT_TIMEOUT = 120.0
# Candidate request paths, tried in order, first match wins. "/interpret" is
# the Platrixa Modal contract ({"text": ...}); "/" covers bridges that mount
# the same handler at the root. Connection errors short-circuit (same host).
DEFAULT_PATHS = ("/interpret", "/")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# ---------------------------------------------------------------------------
# Envelope construction — the exact provider contract shape
# ---------------------------------------------------------------------------
# The device bridge answers {"interpretation_text": "<raw model JSON text>"}.
# RemoteHFModelProvider consumes {"interpretation": {...}, "model": {...}}.
# The functions below are the ONE place that gap is closed: parse the model
# response as JSON, construct the exact envelope, preserve the raw output for
# audit — and fabricate nothing.

def parse_model_json(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Parse raw model text into a dict. Returns (dict|None, parse_mode).

    Extraction only (markdown fences / surrounding prose via the repo's
    base.extract_json_candidate): it never repairs, defaults, or rewrites a
    value, and it never invents a key.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None, "empty"
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed, "plain_json"
    except (json.JSONDecodeError, ValueError):
        pass
    extracted = extract_json_candidate(text)
    if extracted is not None:
        return extracted, "extracted_json (fences/surrounding prose)"
    return None, "unparseable"


def build_provider_envelope(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bridge response body → the EXACT provider envelope:

        {"interpretation": {...18-field candidate...}, "model": {...audit...}}

    Accepts, in order:
      1. {"interpretation": {...}, "model": {...}}  (Platrixa envelope)
      2. {"interpretation_text": "<json string>"}   (device bridge envelope)
         → strict JSON parse first

    Audit: the verbatim raw model output is preserved in
    envelope["model"]["raw_model_output"] (None only when the bridge already
    parsed the model output server-side).

    Fail-closed:
      - neither key present          → MalformedOutputError (keys recorded)
      - interpretation_text not JSON → MalformedOutputError

    This function does NOT add, default, or repair any interpretation field;
    enforce_interpretation_contract() owns the 18-field contract. Raised
    errors carry a `.contract` audit dict for the caller's trace.
    """
    if not isinstance(body, dict):
        err = MalformedOutputError("bridge response body is not a JSON object")
        err.contract = {"checked": True, "passed": False, "reject_reason": str(err)}
        raise err

    candidate = body.get("interpretation")
    if isinstance(candidate, dict):
        raw = body.get("interpretation_text")
        envelope_name = "interpretation"
        raw_model_output = raw if isinstance(raw, str) else None
        parse_mode = "passthrough_envelope"
    else:
        text = body.get("interpretation_text")
        if not isinstance(text, str):
            reason = (
                "bridge response has neither 'interpretation' nor "
                f"'interpretation_text'; keys={sorted(body.keys())}"
            )
            err = MalformedOutputError(reason)
            err.contract = {"checked": True, "passed": False, "reject_reason": reason}
            raise err
        parsed, parse_mode = parse_model_json(text)
        if parsed is None:
            reason = (
                "bridge interpretation_text is not valid JSON "
                f"(length={len(text)}); raw text preserved in audit fields"
            )
            err = MalformedOutputError(reason)
            err.contract = {"checked": True, "passed": False, "reject_reason": reason}
            raise err
        candidate = parsed
        envelope_name = "interpretation_text"
        raw_model_output = text

    model_info = dict(body.get("model") or {})
    model_info["source_envelope"] = envelope_name
    model_info["parse_mode"] = parse_mode
    model_info["raw_model_output"] = raw_model_output
    return {"interpretation": candidate, "model": model_info}


def enforce_interpretation_contract(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fail-closed contract checks on a parsed candidate — the provider's
    Layer-2 duty. NEVER adds, defaults, or repairs a field and never
    substitutes an accounting value.

      - any of the 18 required fields missing
            → MalformedOutputError naming exactly the missing fields
              (e.g. a model that omits `suggested_status` is rejected, not
              silently completed)
      - forbidden accounting-authority keys (journal / ledger / balances / …)
            → ForbiddenAccountingFieldError
      - suggested_status == "VERIFIED"
            → normalized to "REVIEW_REQUIRED": the model may never declare a
              trusted final status. The normalization is recorded in the
              returned contract info (never silent); the grounding gate and
              the Kernel remain the downstream backstops.

    Returns the contract info dict for the audit trace. Raised errors carry
    the same dict on `.contract`.
    """
    missing = [f for f in REQUIRED_FIELDS_18 if f not in (candidate or {})]
    if missing:
        reason = "missing required fields: " + ", ".join(missing)
        err = MalformedOutputError(
            "bridge interpretation missing required fields: " + ", ".join(missing)
        )
        err.contract = {
            "checked": True,
            "passed": False,
            "reject_reason": reason,
            "missing_fields": missing,
        }
        raise err

    forbidden = contains_forbidden_accounting_fields(candidate)
    if forbidden:
        reason = "forbidden accounting fields present: " + ", ".join(forbidden)
        err = ForbiddenAccountingFieldError(reason)
        err.contract = {
            "checked": True,
            "passed": False,
            "reject_reason": reason,
            "forbidden_fields": forbidden,
        }
        raise err

    status_raw = str(candidate.get("suggested_status", "")).strip().upper()
    clamped = status_raw == "VERIFIED"
    if clamped:
        candidate["suggested_status"] = "REVIEW_REQUIRED"

    return {
        "checked": True,
        "passed": True,
        "reject_reason": "",
        "missing_fields": [],
        "forbidden_fields": [],
        "suggested_status_raw": status_raw,
        "suggested_status_clamped_to_review_required": clamped,
    }


class LocalBridgeModelProvider:
    """
    ModelProvider over the device-side llama.cpp bridge.

    Fail-closed taxonomy (same mapping as RemoteHFModelProvider):
      unreachable / timeout / HTTP 5xx / 503        → ModelUnavailableError
      wrong envelope / unparseable JSON / missing
      18-field contract / bridge 422 request reject → MalformedOutputError
      forbidden accounting-truth keys                → ForbiddenAccountingFieldError

    Never falls back to another provider, never repairs output, performs no
    accounting, persists nothing.
    """

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        path: Optional[str] = None,
        timeout: Optional[float] = None,
        config: Optional[ProviderConfig] = None,
    ) -> None:
        # Explicit url="" means "unconfigured" (fail closed); url=None falls
        # back to env, then to the localhost default.
        if url is not None:
            self._url = url.rstrip("/")
        else:
            self._url = (_env(BRIDGE_URL_ENV) or DEFAULT_BRIDGE_URL).rstrip("/")
        explicit_path = path if path is not None else _env(BRIDGE_PATH_ENV)
        self._paths: Tuple[str, ...] = (explicit_path,) if explicit_path else DEFAULT_PATHS
        if timeout is not None:
            self._timeout = float(timeout)
        else:
            try:
                self._timeout = float(_env(BRIDGE_TIMEOUT_ENV, str(DEFAULT_TIMEOUT)))
            except ValueError:
                self._timeout = DEFAULT_TIMEOUT

        # Identity: PROVENANCE_UNKNOWN by default (see module docstring).
        artifact = _env(BRIDGE_ARTIFACT_ENV) or "PROVENANCE_UNKNOWN"
        self._config = config or ProviderConfig(
            model_id="llama.cpp local bridge (local experiment)",
            base_model_revision="PROVENANCE_UNKNOWN",
            adapter_repo_id="PROVENANCE_UNKNOWN",
            adapter_revision=artifact,
        )
        self._last_call_ok = False
        # Full record of the most recent interpret() attempt. Read by the
        # acceptance harness for layer-1/layer-2 evidence; JSON-safe.
        self.last_trace: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # ModelProvider contract
    # ------------------------------------------------------------------

    @property
    def config(self) -> ProviderConfig:
        return self._config

    def status(self) -> ProviderStatus:
        """Non-blocking: no network call, no inference, by contract."""
        configured = bool(self._url)
        return ProviderStatus(
            available=self._last_call_ok,
            model_id=self._config.model_id,
            base_model_revision=self._config.base_model_revision,
            adapter_repo_id=self._config.adapter_repo_id,
            adapter_revision=self._config.adapter_revision,
            reason=(
                "local llama.cpp bridge configured"
                if configured
                else "local llama.cpp bridge not configured"
            ),
            error="" if configured else f"{BRIDGE_URL_ENV} is empty (bridge disabled)",
            loadable=configured,
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        self.last_trace = {
            "bridge_url": self._url,
            "timeout_s": self._timeout,
            "attempts": [],
            "http_ok": False,
            "latency_ms": None,
            "http_status": None,
            "envelope": None,
            "parse_mode": None,
            "raw_response_text": None,
            "raw_http_body": None,
            "contract": {"passed": False, "reject_reason": "", "checked": False},
        }

        if not raw_input or not raw_input.strip():
            raise MalformedOutputError("Empty input")

        body, latency_ms = self._call_bridge(raw_input)
        self.last_trace["http_ok"] = True
        self.last_trace["latency_ms"] = latency_ms

        # --- Layer 2a: parse the model response as JSON and construct the
        # exact provider envelope {"interpretation": ..., "model": ...}.
        # The verbatim raw model output is preserved for audit, never rewritten.
        try:
            envelope = build_provider_envelope(body)
        except MalformedOutputError as exc:
            self.last_trace["contract"] = getattr(
                exc,
                "contract",
                {"checked": True, "passed": False, "reject_reason": str(exc)},
            )
            # Audit: preserve the raw model output even when it is rejected.
            raw = body.get("interpretation_text") if isinstance(body, dict) else None
            self.last_trace["raw_response_text"] = (
                raw if isinstance(raw, str) else self.last_trace.get("raw_http_body")
            )
            raise

        candidate = envelope["interpretation"]
        model_info = envelope["model"]
        raw_model_output = model_info.get("raw_model_output")
        if raw_model_output is None:
            # Bridge already parsed the model output server-side (passthrough
            # envelope): the auditable raw is the full HTTP body, verbatim.
            raw_model_output = self.last_trace.get("raw_http_body")
        self.last_trace["envelope"] = model_info.get("source_envelope")
        self.last_trace["parse_mode"] = model_info.get("parse_mode")
        self.last_trace["raw_response_text"] = raw_model_output

        # --- Layer 2b: 18-field contract, forbidden accounting fields, and
        # the VERIFIED ban — fail closed, never repair, never invent.
        try:
            contract_info = enforce_interpretation_contract(candidate)
        except (MalformedOutputError, ForbiddenAccountingFieldError) as exc:
            self.last_trace["contract"] = getattr(
                exc,
                "contract",
                {"checked": True, "passed": False, "reject_reason": str(exc)},
            )
            raise
        self.last_trace["contract"] = contract_info
        self._last_call_ok = True

        return InterpretationResult(
            raw_input=raw_input,
            candidate=candidate,
            model_id=self._config.model_id,
            provider_revision=self._config.adapter_revision,
            generated_profile={
                "bridge_url": self._url,
                "latency_ms": latency_ms,
                "envelope": model_info.get("source_envelope"),
                "artifact_identity": self._config.adapter_revision,
                # Auditable field: the model's raw output, verbatim.
                "raw_model_output": raw_model_output,
            },
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _call_bridge(self, raw_input: str) -> Tuple[Dict[str, Any], float]:
        """POST to the bridge; return (json body, latency_ms). Trace every attempt."""
        attempts: List[Dict[str, Any]] = []
        self.last_trace["attempts"] = attempts
        connection_error: Optional[str] = None

        for path in self._paths:
            url = f"{self._url}{path}"
            started = time.monotonic()
            try:
                resp = requests.post(
                    url,
                    json={"text": raw_input},
                    headers={"Content-Type": "application/json"},
                    timeout=self._timeout,
                )
            except requests.Timeout:
                attempts.append({"path": path, "error": "timeout"})
                connection_error = f"bridge timeout on {path}"
                break  # same host — further paths will not help
            except requests.RequestException as exc:
                attempts.append({"path": path, "error": f"unreachable: {exc}"})
                connection_error = f"bridge unreachable: {exc}"
                break  # same host — further paths will not help

            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            self.last_trace["latency_ms"] = elapsed_ms
            self.last_trace["http_status"] = resp.status_code
            body_excerpt = (resp.text or "")[:2000]
            attempts.append({
                "path": path,
                "http_status": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "response_excerpt": body_excerpt,
            })

            if resp.status_code == 200:
                # Full verbatim body — audit only, never rewritten/truncated.
                self.last_trace["raw_http_body"] = resp.text
                try:
                    body = resp.json()
                except ValueError:
                    self.last_trace["raw_response_text"] = resp.text
                    raise MalformedOutputError(
                        f"bridge 200 response on {path} is not valid JSON"
                    )
                if not isinstance(body, dict):
                    self.last_trace["raw_response_text"] = resp.text
                    raise MalformedOutputError(
                        f"bridge 200 response on {path} is not a JSON object"
                    )
                return body, elapsed_ms
            if resp.status_code in (404, 405):
                continue  # wrong mount point — try the next candidate path
            if resp.status_code == 422:
                raise MalformedOutputError(
                    f"bridge rejected request shape on {path} (422): {body_excerpt[:400]}"
                )
            if resp.status_code == 503:
                raise ModelUnavailableError(
                    f"bridge reports model unavailable (503) on {path}: {body_excerpt[:400]}"
                )
            # 5xx / anything else → infrastructure failure
            raise ModelUnavailableError(
                f"bridge error {resp.status_code} on {path}: {body_excerpt[:400]}"
            )

        # All candidate paths exhausted (or connection failed).
        raise ModelUnavailableError(
            connection_error
            or f"no bridge route matched; tried paths {[a.get('path') for a in attempts]}"
        )


__all__ = [
    "LocalBridgeModelProvider",
    "build_provider_envelope",
    "enforce_interpretation_contract",
    "parse_model_json",
    "BRIDGE_URL_ENV",
    "BRIDGE_PATH_ENV",
    "BRIDGE_TIMEOUT_ENV",
    "BRIDGE_ARTIFACT_ENV",
]
