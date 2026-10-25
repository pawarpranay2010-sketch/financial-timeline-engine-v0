#!/usr/bin/env python3
"""
Readiness diagnostics + Phase-15 readiness correctness (P2/P3, fte_fyjc_93).

Run:
    .venv/bin/python scripts/fte_fyjc_93_readiness_logging_test.py

A  P2 — a FAILED readiness probe now logs exactly one line naming the
   allow-listed cause class. The cause's message below carries a fake DSN,
   which must never reach the log; the negative cache must keep two polls
   inside the TTL to ONE probe and ONE line, and the response envelope must
   be unchanged.
B  P2 — a cause that is not on the allow-list degrades to the fixed token
   ``unclassified``; its class name and message are not echoed.
C  P3 — Phase-15-only with a healthy provider is READY (200): the boundary
   is the shared key and there is no store behind it to probe. Nothing may
   claim a store exists, and the key value must not appear in the body.
D  P3 — metered + unreachable store still reports not_ready with the store
   named as the reason and no connection material in the body.
E  P3 — metered + healthy store reports ready; zero-config open mode still
   reports not_ready with its UNCHANGED reason (regression guard).

Offline: no network and no production database. The store is either a dead
SQLite path (a file that can never be opened) or a temp file under
``tempfile.mkdtemp()``, or ``_session_factory`` is patched in-process.

Env vars are saved and restored; nothing outside this process is touched.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_GATED_ENV = ("PLATRIXA_METERING_DATABASE_URL", "PLATRIXA_DEV_API_KEY")
_SAVED_ENV = {name: os.environ.get(name) for name in _GATED_ENV}
for _name in _GATED_ENV:
    os.environ.pop(_name, None)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routes import developer  # noqa: E402
from backend.auth import admission as admission_boundary  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

FAKE_DSN = "postgresql://probe_user:fake-secret-9x7@meter-db.internal:5432/platrixa"
SECRET_MARKERS = (
    FAKE_DSN,
    "fake-secret-9x7",
    "probe_user",
    "meter-db.internal",
    "sqlite:///",
)
WELL_KNOWN_MISSING_DIR = "/nonexistent-platrixa-readiness-dir"
DEAD_STORE = f"sqlite:///{WELL_KNOWN_MISSING_DIR}/store.db"
ZERO_CONFIG_REASON = (
    "admission not configured: no PLATRIXA_DEV_API_KEY and no metering store "
    "(zero-config open mode)"
)
METERED_REASON = "metering store unreachable"
READY_BODY_KEYS = {"status", "api_version", "provider", "rule_pack", "reason", "admission"}

CHECKS: list[tuple[str, bool, str]] = []
SKIPS: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def skip(name: str, reason: str) -> None:
    SKIPS.append((name, reason))
    print(f"  [SKIP] {name} — {reason}")


# Driver-class names used as probe causes: one allow-listed, one not.
class OperationalError(Exception):  # noqa: A001 - mimics the DBAPI class name
    """Allow-listed class name; the message carries the fake DSN."""


class NotAllowlistedError(Exception):
    """Class name that must never be written to the log."""


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


class _StubClient:
    """Provider stand-in: ready, and never loads a model."""

    def provider_status(self):
        return {"available": True, "loadable": True, "model_id": "stub", "reason": ""}

    def rule_pack_summary(self):
        return {"id": "stub", "version": "0"}


def _clear(url: str | None = None, *, dev_key: str | None = None) -> None:
    """Deterministic store/mode setup between cases (no inherited state)."""
    for name in _GATED_ENV:
        os.environ.pop(name, None)
    if url is not None:
        os.environ[metered_gate.METERING_ENV_VAR] = url
    if dev_key is not None:
        os.environ[admission_boundary.PHASE15_ENV_VAR] = dev_key
    metered_gate._session_factory_cache.clear()
    admission_boundary.reset_readiness_cache()


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(developer.router)
    developer.register_developer_error_handlers(app)
    developer.set_client(_StubClient())
    return TestClient(app, raise_server_exceptions=False)


def _capture(fn, *args):
    """Run ``fn`` with the admission logger recorded; return (result, lines)."""
    rec = _Recorder()
    logger = logging.getLogger("platrixa.api")
    previous = logger.level
    logger.addHandler(rec)
    logger.setLevel(logging.INFO)
    try:
        result = fn(*args)
    finally:
        logger.removeHandler(rec)
        logger.setLevel(previous)
    return result, rec.lines


def _probe_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if "readiness probe failed" in ln]


def _leaks(text: str) -> list[str]:
    return [marker for marker in SECRET_MARKERS if marker in text]


def _dead_factory(cause: type[Exception]):
    """A factory whose probe session raises ``cause`` carrying the fake DSN."""

    class _Session:
        def execute(self, *_args, **_kwargs):
            raise cause("could not connect to " + FAKE_DSN)

        def close(self):
            return None

    return lambda: (lambda: _Session())


# ---------------------------------------------------------------------------
# A — P2: one secret-free diagnostic line per failed probe
# ---------------------------------------------------------------------------

def section_a(client: TestClient) -> None:
    print("\nA — P2 failed probe: one line, allow-listed cause, unchanged envelope")
    _clear(FAKE_DSN)
    probe_calls: list[int] = []

    class _Session:
        def execute(self, *_args, **_kwargs):
            probe_calls.append(1)
            raise OperationalError("could not connect to " + FAKE_DSN)

        def close(self):
            return None

    original_factory = metered_gate._session_factory
    metered_gate._session_factory = lambda: (lambda: _Session())
    try:
        first, lines = _capture(client.get, "/v1/ready")
        second, lines2 = _capture(client.get, "/v1/ready")
    finally:
        metered_gate._session_factory = original_factory
        _clear(None)
    lines.extend(lines2)

    probe = _probe_lines(lines)
    check("A1 both polls answer 503 (verdict unchanged)",
          first.status_code == 503 and second.status_code == 503,
          f"{first.status_code}/{second.status_code}")
    body = first.json()
    check("A2 envelope shape unchanged", set(body) == READY_BODY_KEYS, str(sorted(body)))
    check("A3 reason still names the store",
          body.get("reason") == METERED_REASON, str(body.get("reason")))
    adm = body.get("admission") or {}
    check("A4 admission block unchanged (metered, store down, not ready)",
          adm.get("mode") == "metered-tenants"
          and adm.get("metering_configured") is True
          and adm.get("metering_store_available") is False
          and adm.get("production_ready") is False,
          str(adm))
    check("A5 both polls return the identical body", first.text == second.text,
          f"{first.text[:120]} vs {second.text[:120]}")
    check("A6 the failed probe is logged EXACTLY once (negative cache)",
          len(probe) == 1, f"lines={probe}")
    check("A7 the log names the allow-listed cause class",
          bool(probe) and "cause=OperationalError" in probe[0],
          probe[0] if probe else "")
    check("A8 one probe ran, not two", probe_calls == [1], str(probe_calls))
    leaked_logs = [ln for ln in lines if _leaks(ln)]
    check("A9 no DSN/user/host in ANY log line", not leaked_logs, str(leaked_logs))
    check("A10 no DSN/user/host in the response", not _leaks(first.text + second.text),
          first.text[:200])


# ---------------------------------------------------------------------------
# B — P2: unlisted cause degrades to a fixed token
# ---------------------------------------------------------------------------

def section_b(client: TestClient) -> None:
    print("\nB — P2 an unlisted cause class degrades to a fixed token")
    _clear(FAKE_DSN)
    original_factory = metered_gate._session_factory
    metered_gate._session_factory = _dead_factory(NotAllowlistedError)
    try:
        response, lines = _capture(client.get, "/v1/ready")
    finally:
        metered_gate._session_factory = original_factory
        _clear(None)

    probe = _probe_lines(lines)
    check("B1 response still 503 not_ready with the store named",
          response.status_code == 503
          and response.json().get("reason") == METERED_REASON,
          f"{response.status_code} {response.json().get('reason')}")
    check("B2 exactly one probe-failure line", len(probe) == 1, f"lines={probe}")
    check("B3 cause degrades to the fixed token 'unclassified'",
          bool(probe) and "cause=unclassified" in probe[0],
          probe[0] if probe else "")
    check("B4 the unlisted class name is not echoed",
          bool(probe) and "NotAllowlisted" not in probe[0],
          probe[0] if probe else "")
    leaked = [ln for ln in lines if _leaks(ln) or "NotAllowlisted" in ln]
    check("B5 no class name, message or secret from the unlisted cause",
          not leaked, str(leaked))


# ---------------------------------------------------------------------------
# C — P3: Phase-15-only is READY without any metering store
# ---------------------------------------------------------------------------

def section_c(client: TestClient) -> None:
    print("\nC — P3 Phase-15-only deployment readiness")
    dev_key = "plx_phase15_readiness_key_value"
    _clear(None, dev_key=dev_key)
    block = admission_boundary.readiness_block()["admission"]
    check("C1 readiness_block reports production_ready=true without a store",
          block.get("production_ready") is True, str(block))
    check("C2 no store is claimed to exist (availability is null)",
          block.get("metering_store_available") is None, str(block))
    check("C3 mode is phase15-shared-key",
          block.get("mode") == "phase15-shared-key", str(block.get("mode")))

    response, lines = _capture(client.get, "/v1/ready")
    body = response.json()
    check("C4 /v1/ready answers 200 ready (no false 503)",
          response.status_code == 200 and body.get("status") == "ready",
          f"code={response.status_code} status={body.get('status')}")
    check("C5 no reason: a healthy Phase-15 deployment is not 'unreachable'",
          body.get("reason") is None, str(body.get("reason")))
    adm = body.get("admission") or {}
    check("C6 admission block: boundary configured, production-ready",
          adm.get("phase15_shared_key_configured") is True
          and adm.get("metering_configured") is False
          and adm.get("production_ready") is True
          and adm.get("metering_store_available") is None,
          str(adm))
    check("C7 nothing was probed (there is no store to probe)",
          not _probe_lines(lines), str(_probe_lines(lines)))
    check("C8 the Phase 15 key value never appears in the body",
          dev_key not in response.text and dev_key[:12] not in response.text,
          response.text[:200])
    check("C9 envelope shape unchanged", set(body) == READY_BODY_KEYS, str(sorted(body)))
    _clear(None)


# ---------------------------------------------------------------------------
# D — P3: metered + unreachable store still fails closed
# ---------------------------------------------------------------------------

def section_d(client: TestClient) -> None:
    print("\nD — P3 metered + unreachable store")
    _clear(DEAD_STORE)
    response, lines = _capture(client.get, "/v1/ready")
    body = response.json()
    adm = body.get("admission") or {}
    check("D1 503 not_ready (fail closed)",
          response.status_code == 503 and body.get("status") == "not_ready",
          f"code={response.status_code} status={body.get('status')}")
    check("D2 production_ready=false, store reported unavailable",
          adm.get("production_ready") is False and adm.get("metering_store_available") is False,
          str(adm))
    check("D3 the reason names the store that was actually probed",
          body.get("reason") == METERED_REASON, str(body.get("reason")))
    check("D4 no connection material in the response",
          not _leaks(response.text) and WELL_KNOWN_MISSING_DIR not in response.text,
          response.text[:200])
    probe = _probe_lines(lines)
    check("D5 the failed probe is logged exactly once", len(probe) == 1, str(probe))
    leaked_logs = [ln for ln in lines if _leaks(ln) or WELL_KNOWN_MISSING_DIR in ln]
    check("D6 no path/DSN in the log", not leaked_logs, str(leaked_logs))
    _clear(None)


# ---------------------------------------------------------------------------
# E — P3: metered + healthy store is ready; zero-config stays not_ready
# ---------------------------------------------------------------------------

def section_e(client: TestClient) -> None:
    print("\nE — P3 metered + healthy store, and the zero-config regression guard")
    path = os.path.join(tempfile.mkdtemp(prefix="fte93_"), "readiness_ok.db")
    _clear("sqlite:///" + path)
    response, _lines = _capture(client.get, "/v1/ready")
    body = response.json()
    adm = body.get("admission") or {}
    check("E1 metered + healthy store → 200 ready",
          response.status_code == 200 and body.get("status") == "ready",
          f"code={response.status_code} status={body.get('status')}")
    check("E2 production_ready=true and the store measured available",
          adm.get("production_ready") is True and adm.get("metering_store_available") is True,
          str(adm))
    check("E3 no reason on a healthy deployment", body.get("reason") is None,
          str(body.get("reason")))
    check("E4 the probe really connected (the temp store file exists)",
          os.path.exists(path), path)

    _clear(None)
    zero = client.get("/v1/ready")
    zero_body = zero.json()
    check("E5 zero-config still answers 503 not_ready",
          zero.status_code == 503 and zero_body.get("status") == "not_ready",
          f"code={zero.status_code}")
    check("E6 zero-config reason string is UNCHANGED",
          zero_body.get("reason") == ZERO_CONFIG_REASON, str(zero_body.get("reason")))
    check("E7 zero-config admission block unchanged",
          (zero_body.get("admission") or {}).get("mode") == "open-anonymous"
          and (zero_body.get("admission") or {}).get("production_ready") is False,
          str(zero_body.get("admission")))
    _clear(None)


def main() -> int:
    print("=" * 78)
    print("P2/P3 — readiness probe logging + Phase-15 readiness (fte_fyjc_93)")
    print("=" * 78)
    client = _make_client()
    try:
        section_a(client)
        section_b(client)
        section_c(client)
        section_d(client)
        section_e(client)
    finally:
        developer.reset_client()
        for name, value in _SAVED_ENV.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    failed = [(n, d) for n, ok, d in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"TOTAL: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS"
          + (f"  —  {len(failed)} FAILED" if failed else "")
          + (f"  —  {len(SKIPS)} SKIP" if SKIPS else ""))
    for name, detail in failed:
        print(f"  FAIL: {name} {detail}")
    for name, reason in SKIPS:
        print(f"  SKIP: {name} {reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
