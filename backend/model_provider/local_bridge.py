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
            "contract": {"passed": False, "reject_reason": "", "checked": False},
        }

        if not raw_input or not raw_input.strip():
            raise MalformedOutputError("Empty input")

        body, latency_ms = self._call_bridge(raw_input)
        self.last_trace["http_ok"] = True
        self.last_trace["latency_ms"] = latency_ms

        candidate, model_info, envelope = self._unwrap(body)
        self.last_trace["envelope"] = envelope

        # --- Layer 2: provider contract, fail closed, never repair ---------
        missing = [f for f in REQUIRED_FIELDS_18 if f not in candidate]
        if missing:
            self.last_trace["contract"] = {
                "checked": True,
                "passed": False,
                "reject_reason": "missing required fields: " + ", ".join(missing),
                "missing_fields": missing,
            }
            raise MalformedOutputError(
                "bridge interpretation missing required fields: " + ", ".join(missing)
            )

        forbidden = contains_forbidden_accounting_fields(candidate)
        if forbidden:
            self.last_trace["contract"] = {
                "checked": True,
                "passed": False,
                "reject_reason": "forbidden accounting fields present: " + ", ".join(forbidden),
                "forbidden_fields": forbidden,
            }
            raise ForbiddenAccountingFieldError(
                "forbidden accounting fields present: " + ", ".join(forbidden)
            )

        # Status-authority contract: the model never declares VERIFIED.
        # Normalization is mirrored from RemoteHFModelProvider and is
        # recorded, not hidden.
        status_raw = str(candidate.get("suggested_status", "")).strip().upper()
        clamped = status_raw == "VERIFIED"
        if clamped:
            candidate["suggested_status"] = "REVIEW_REQUIRED"

        self.last_trace["contract"] = {
            "checked": True,
            "passed": True,
            "reject_reason": "",
            "missing_fields": [],
            "forbidden_fields": [],
            "suggested_status_raw": status_raw,
            "suggested_status_clamped_to_review_required": clamped,
        }
        self._last_call_ok = True

        return InterpretationResult(
            raw_input=raw_input,
            candidate=candidate,
            model_id=self._config.model_id,
            provider_revision=self._config.adapter_revision,
            generated_profile={
                "bridge_url": self._url,
                "latency_ms": latency_ms,
                "envelope": envelope,
                "artifact_identity": self._config.adapter_revision,
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
                self.last_trace["raw_response_text"] = body_excerpt
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

    # ------------------------------------------------------------------
    # Envelope → candidate (strict, no repair)
    # ------------------------------------------------------------------

    def _unwrap(
        self, body: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        """
        Return (candidate, model_info, envelope_name).

        Accepts, in order:
          1. {"interpretation": {...}, "model": {...}}   (Platrixa envelope)
          2. {"interpretation_text": "<json string>"}    (device bridge envelope)
        Anything else is rejected with the actual keys recorded.
        """
        candidate = body.get("interpretation")
        if isinstance(candidate, dict):
            model_info = body.get("model", {})
            return candidate, model_info if isinstance(model_info, dict) else {}, "interpretation"

        text = body.get("interpretation_text")
        if isinstance(text, str):
            self.last_trace["raw_response_text"] = text
            parsed = self._parse_strict(text)
            if parsed is None:
                self.last_trace["contract"] = {
                    "checked": True,
                    "passed": False,
                    "reject_reason": "interpretation_text is not valid JSON",
                }
                raise MalformedOutputError(
                    "bridge interpretation_text is not valid JSON "
                    f"(length={len(text)}); raw text recorded in trace"
                )
            self.last_trace["parse_mode"] = self._parse_mode(text)
            return parsed, {}, "interpretation_text"

        self.last_trace["contract"] = {
            "checked": True,
            "passed": False,
            "reject_reason": "bridge response has neither 'interpretation' nor "
            f"'interpretation_text'; keys={sorted(body.keys())}",
        }
        raise MalformedOutputError(
            "bridge response has neither 'interpretation' nor 'interpretation_text'; "
            f"keys={sorted(body.keys())}"
        )

    @staticmethod
    def _parse_strict(text: str) -> Optional[Dict[str, Any]]:
        """Parse the model's JSON text. Extraction only — never repair."""
        stripped = (text or "").strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        # Repo-sanctioned extraction (markdown fences / surrounding prose).
        # base.extract_json_candidate only locates a JSON object; it never
        # alters values or invents keys.
        return extract_json_candidate(text)

    @staticmethod
    def _parse_mode(text: str) -> str:
        stripped = (text or "").strip()
        try:
            if isinstance(json.loads(stripped), dict):
                return "plain_json"
        except (json.JSONDecodeError, ValueError):
            pass
        return "extracted_json (fences/surrounding prose)"


__all__ = [
    "LocalBridgeModelProvider",
    "BRIDGE_URL_ENV",
    "BRIDGE_PATH_ENV",
    "BRIDGE_TIMEOUT_ENV",
    "BRIDGE_ARTIFACT_ENV",
]
