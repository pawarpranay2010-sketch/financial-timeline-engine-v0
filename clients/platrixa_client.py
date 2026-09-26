"""Platrixa hosted-API client (Phase 5F) — the smallest useful transport wrapper.

Scope (deliberate):

  * authentication (X-Platrixa-API-Key)
  * POST /v1/process                       → :meth:`PlatrixaClient.process`
  * POST /v1/documents (async submission)  → :meth:`PlatrixaClient.submit_document`
  * GET  /v1/jobs/{job_id} (+ wait helper) → :meth:`PlatrixaClient.job_status` / ``wait_for_job``
  * GET  /v1/results/{result_id}           → :meth:`PlatrixaClient.result`
  * GET  /v1/capabilities                  → :meth:`PlatrixaClient.capabilities`
  * GET  /v1/health (no auth)              → :meth:`PlatrixaClient.health`
  * Idempotency-Key pass-through on process/submit_document

NOT in scope (deliberately): business or financial logic, key issuance,
webhook RECEIVING infrastructure (only verification helpers are provided),
retry policy beyond ``requests``' transport defaults. The client is a
transport wrapper — the engine remains the sole authority and the server
remains the sole status owner.

Usage:

    from clients.platrixa_client import PlatrixaClient

    client = PlatrixaClient("http://127.0.0.1:8000", api_key="plx_…")
    result = client.process("Purchased furniture for cash Rs. 15,000",
                            idempotency_key="my-key-~._0123456789")
    if result["api_status"] == "VERIFIED":
        post(result["accounting_result"])
    elif result["api_status"] == "REVIEW_REQUIRED":
        queue_for_review(result)

    job = client.submit_document(pdf_bytes, source_name="invoice.pdf")
    final = client.wait_for_job(job["job_id"])          # polls until done
    envelope = client.result(job["result_id"])          # the 5D envelope
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, Optional

import requests

DEFAULT_TIMEOUT_S = 120.0
# ApiError.api_status values for which retrying may eventually succeed.
_RETRYABLE_API_STATUSES = frozenset({"PROCESSING"})
_SIGNATURE_TOLERANCE_S = 300


class ApiError(RuntimeError):
    """A /v1 error envelope (transport rejection) — codes, never internals."""

    def __init__(self, status_code: int, error: Dict[str, Any]) -> None:
        self.status_code = status_code
        self.code = error.get("code", "ERROR")
        self.message = error.get("message", "")
        self.api_status = error.get("api_status", "")
        self.retryable = bool(error.get("retryable", False))
        super().__init__(f"HTTP {status_code} {self.code}: {self.message}")


class PlatrixaClient:
    """Thin, typed transport wrapper over the Platrixa hosted /v1 API."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = session or requests.Session()

    # -- internals -------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Platrixa-API-Key"] = self.api_key
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        expected: tuple = (200, 202),
    ) -> Dict[str, Any]:
        resp = self._session.request(
            method,
            self._url(path),
            json=json_body,
            headers=self._headers(idempotency_key),
            timeout=self.timeout,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code not in expected:
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                raise ApiError(resp.status_code, error)
            raise ApiError(resp.status_code, {"code": "ERROR", "message": resp.text[:200]})
        return body

    # -- public surface ----------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """GET /v1/health — liveness (no auth)."""
        return self._request("GET", "/v1/health", expected=(200,))

    def capabilities(self) -> Dict[str, Any]:
        """GET /v1/capabilities — deterministic capability discovery."""
        return self._request("GET", "/v1/capabilities", expected=(200,))

    def process(
        self,
        raw_input: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /v1/process — synchronous transaction validation.

        Returns the canonical 5D envelope. Branch on ``api_status``;
        ``success`` is true only for VERIFIED. Never raises for engine
        outcomes (REVIEW_REQUIRED/UNSUPPORTED are results, not errors) —
        raises :class:`ApiError` only for transport rejections.
        """
        return self._request(
            "POST",
            "/v1/process",
            json_body={"raw_input": raw_input},
            idempotency_key=idempotency_key,
            expected=(200, 422),
        )

    def submit_document(
        self,
        document_bytes: bytes,
        source_name: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /v1/documents — asynchronous document submission (202).

        Returns ``{job_id, result_id, status_url, result_url, …}``.
        202 means ADMITTED — nothing about the final status.
        """
        import base64

        return self._request(
            "POST",
            "/v1/documents",
            json_body={
                "document_b64": base64.b64encode(document_bytes).decode("ascii"),
                "source_name": source_name,
            },
            idempotency_key=idempotency_key,
            expected=(202,),
        )

    def job_status(self, job_id: str) -> Dict[str, Any]:
        """GET /v1/jobs/{job_id} — poll. status == "PROCESSING" until done.

        Completion is NOT VERIFIED — the final status is the real engine
        outcome (or FAILED with retryable/reason_codes).
        """
        return self._request("GET", f"/v1/jobs/{job_id}", expected=(200,))

    def wait_for_job(
        self,
        job_id: str,
        *,
        poll_interval_s: float = 2.0,
        timeout_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Poll until the job leaves PROCESSING. Returns the final status.

        Raises :class:`TimeoutError` if the deadline passes (the job may
        still complete later — its result_url remains valid).
        """
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.job_status(job_id)
            if status.get("status") != "PROCESSING":
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(f"job {job_id} still PROCESSING after {timeout_s}s")
            time.sleep(poll_interval_s)

    def result(self, result_id: str) -> Dict[str, Any]:
        """GET /v1/results/{result_id} — THE 5D canonical envelope.

        Same contract as :meth:`process`. Raises ApiError with
        RESULT_NOT_READY until the job completes.
        """
        return self._request("GET", f"/v1/results/{result_id}", expected=(200,))

    # -- webhook verification helpers (consumer side) ----------------------

    @staticmethod
    def verify_webhook_signature(
        secret: str,
        signature_header: str,
        raw_body: str,
        *,
        now_ts: Optional[int] = None,
        tolerance_s: int = _SIGNATURE_TOLERANCE_S,
    ) -> bool:
        """Verify ``Platrixa-Signature: t=<unix>,v1=<hex>``.

        Checks the HMAC-SHA256 over ``"{t}.{raw_body}"`` and the ±300 s
        timestamp window (replay protection). Constant-time comparison.
        """
        try:
            parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
            ts = int(parts["t"])
            sig = parts["v1"]
        except Exception:
            return False
        current = now_ts if now_ts is not None else int(time.time())
        if abs(current - ts) > tolerance_s:
            return False
        expected = hmac.new(
            secret.encode("utf-8"), f"{ts}.{raw_body}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig)


__all__ = ["PlatrixaClient", "ApiError"]
