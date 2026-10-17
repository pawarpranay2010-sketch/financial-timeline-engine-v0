#!/usr/bin/env python3
"""Stage 2 O1 — lease EXPIRY as commit authority (isolated, deterministic).

The defect this suite pins down
===============================
Before Stage 2 O1 the server fenced a result commit on

    status = 'PROCESSING' AND lease_owner = :owner AND lease_generation = :gen

Only. ``lease_expires_at`` made a job CLAIMABLE again (crash recovery), but
expiry alone revoked nothing: a worker whose lease had silently lapsed kept its
commit authority until some OTHER worker happened to reclaim the job. So an
expired-but-unsuperseded lease could still publish an authoritative result —
and could even RENEW itself back to life first.

The rule this suite asserts
===========================
A result becomes authoritative only if, AT THE ATOMIC COMMIT POINT, the
submitted owner and generation match the row, the lease has NOT expired on the
SERVER's clock, and the job can still accept a result. Expiry is a real
revocation: a lapsed lease cannot be renewed, and the only way forward is a
fresh claim at a new generation.

Everything here runs against the REAL local stack — no mocked routes:
  * the real FastAPI app served by ``uvicorn`` on a loopback socket, so every
    status/body below is a real HTTP response;
  * the real PostgreSQL job store (``backend/auth/async_jobs.py``) in a
    disposable embedded cluster;
  * the real claim / renew / result routes and their fenced SQL.
Only the device model bridge is substituted. No production host, credential or
database is touched; every origin is loopback (the imported audit suite's
guardrails enforce this at import time).

Determinism
===========
No test outcome depends on sleeping until a guessed moment:

  * "time has passed" is expressed by moving the job's persisted
    ``lease_expires_at`` into the past in the DISPOSABLE store — the server's
    own clock then does the judging, exactly as for a crashed worker;
  * the two ordering cases (C1, D1, E3) hold a real ``SELECT ... FOR UPDATE``
    on the job row in a control connection, start the real operation, wait
    until PostgreSQL reports that statement BLOCKED on the lock
    (``pg_stat_activity``), then change the row and commit. The ordering is
    therefore a fact about the database, not about timing.

Run:
    .venv/bin/python scripts/platrixa_stage2_o1_lease_expiry_test.py [iterations]
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import requests  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Importing the Stage 2 audit suite runs its isolation guardrails first.
import platrixa_stage2_lease_safety_integration_test as S2  # noqa: E402
import fte_fyjc_89_outbound_inference_bridge_test as S1  # noqa: E402
from api.main import create_app  # noqa: E402
from api.routes import developer  # noqa: E402
from backend.auth import async_jobs  # noqa: E402

KEY = S2.KEY_A
KEY_OTHER = S2.KEY_B
TENANT = S2.TENANT_A
WORKER_A = "o1-worker-a"
WORKER_B = "o1-worker-b"
CREATOR = "o1-creator"
RAW_INPUT = S1.RAW_INPUT

# Worker A's candidate carries a marker: if a stale or expired write ever
# landed, the marker would appear in the authoritative result.
MARKER = "o1-stale-candidate-marker"
_STALE = copy.deepcopy(S1.VALID_CANDIDATE)
_STALE["ambiguities"] = [MARKER]
BODY_A = S1.bridge_body(_STALE)
BODY_B = S1.bridge_body(S1.VALID_CANDIDATE)

_PG_DATA = Path("/tmp/platrixa_fte89_pgdata")
LEASE_ENV = async_jobs.INFERENCE_LEASE_ENV_VAR
# Long enough that no test relies on wall-clock passage; "expiry" is expressed
# by moving the stored expiry into the past.
LEASE_SECONDS = "60"

check = S2.check
scenario = S2.scenario


# ---------------------------------------------------------------------------
# Real HTTP (loopback uvicorn — the same transport production uses)
# ---------------------------------------------------------------------------


def _headers(worker_id: str, key: str = KEY) -> dict:
    return {"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id}


def create_job(api_base: str, *, key: str = KEY):
    return requests.post(
        f"{api_base}/v1/inference-jobs",
        json={"raw_input": RAW_INPUT},
        headers=_headers(CREATOR, key),
        timeout=20,
    )


def claim(api_base: str, worker_id: str, *, key: str = KEY):
    return requests.post(
        f"{api_base}/v1/worker/jobs/claim", headers=_headers(worker_id, key), timeout=20
    )


def submit(api_base: str, job_id: str, lease: dict, body: dict, worker_id: str, *, key: str = KEY):
    return requests.post(
        f"{api_base}/v1/worker/jobs/{job_id}/result",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]},
              "bridge_body": body},
        headers=_headers(worker_id, key),
        timeout=30,
    )


def renew(api_base: str, job_id: str, lease: dict, worker_id: str, *, key: str = KEY):
    return requests.post(
        f"{api_base}/v1/worker/jobs/{job_id}/lease",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]}},
        headers=_headers(worker_id, key),
        timeout=30,
    )


def read_result(api_base: str, result_id: str, *, key: str = KEY):
    return requests.get(
        f"{api_base}/v1/results/{result_id}", headers=_headers("o1-result-reader", key), timeout=20
    )


def code_of(response) -> str:
    try:
        return str((response.json().get("error") or {}).get("code") or "")
    except Exception:
        return ""


class Assertions:
    """Collects every failure in an iteration instead of stopping at the first."""

    def __init__(self) -> None:
        self.failures: list = []

    def require(self, condition, message) -> bool:
        if not condition:
            self.failures.append(message)
        return bool(condition)


# ---------------------------------------------------------------------------
# Store-side helpers (the REAL store module, the real PostgreSQL)
# ---------------------------------------------------------------------------


def fresh(engine) -> None:
    """One iteration = fresh isolated job/store state."""
    S2.reset(engine)


def row(engine, job_id: str) -> dict:
    return S2.job_row(engine, job_id) or {}


def db_now(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT now()")).scalar()


def expire_in_store(engine, job_id: str) -> None:
    """Move the persisted expiry into the past: a crashed/partitioned worker."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_async_jobs SET lease_expires_at = now() - interval '1 hour' "
                "WHERE job_id = :j"
            ),
            {"j": job_id},
        )


def hold_row(engine, job_id: str):
    """Open a control transaction holding ``FOR UPDATE`` on the job row."""
    conn = engine.connect()
    tx = conn.begin()
    conn.execute(
        text("SELECT job_id FROM platrixa_async_jobs WHERE job_id = :j FOR UPDATE"),
        {"j": job_id},
    )
    return conn, tx


def wait_blocked(engine, needle: str, timeout: float = 20.0) -> bool:
    """Wait until PostgreSQL reports a statement blocked on a lock.

    This is how the ordering cases become deterministic: the test proceeds only
    once the database itself says the operation is waiting.
    """
    deadline = time.monotonic() + timeout
    sql = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND state = 'active' "
        "  AND wait_event_type = 'Lock' AND query LIKE :n"
    )
    while time.monotonic() < deadline:
        # A fresh connection per poll: a long-lived one would keep an implicit
        # transaction open across the whole wait.
        with engine.connect() as conn:
            if (conn.execute(sql, {"n": f"%{needle}%"}).scalar() or 0) > 0:
                return True
        time.sleep(0.02)
    return False


class Background:
    """Run one real request in a thread and keep its outcome."""

    def __init__(self, fn) -> None:
        self.outcome: dict = {}
        self.thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self.thread.start()

    def _run(self, fn) -> None:
        try:
            self.outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported, then asserted on
            self.outcome["error"] = exc

    def join(self, timeout: float = 30.0) -> bool:
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()


def _claim_and_lease(api_base: str, engine, worker_id: str) -> tuple:
    created = create_job(api_base)
    job_id = created.json()["job_id"]
    result_id = created.json()["result_id"]
    claimed = claim(api_base, worker_id)
    lease = claimed.json()["job"]["lease"]
    return job_id, result_id, lease


def _state(engine, job_id: str) -> dict:
    r = row(engine, job_id)
    return {k: (str(v) if k in ("lease_expires_at",) else v) for k, v in r.items()}


# ---------------------------------------------------------------------------
# Case A — expired, unsuperseded lease cannot commit
# ---------------------------------------------------------------------------


def case_a(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)
    expire_in_store(engine, job_id)
    expired_row = row(engine, job_id)
    a.require(expired_row["lease_expires_at"] < db_now(engine),
              f"the store is not actually expired: {expired_row['lease_expires_at']}")

    response = submit(api_base, job_id, lease, BODY_A, WORKER_A)
    after = row(engine, job_id)
    a.require(response.status_code == 409,
              f"A's expired-but-unsuperseded commit must be refused, got {response.status_code} "
              f"{response.text[:200]}")
    a.require(code_of(response) == "LEASE_EXPIRED",
              f"refusal code must name expiry, got {code_of(response)!r} {response.text[:200]}")
    a.require(after["status"] == "PROCESSING",
              f"the job must stay PROCESSING after refusal, got {after['status']}")
    a.require(after["result_json"] is None and after["http_status"] is None,
              "a refused commit must store NO result (no partial commit)")
    a.require(after["lease_owner"] == WORKER_A and after["lease_generation"] == lease["generation"],
              f"a refusal must not mutate ownership/generation: {_state(engine, job_id)}")
    a.require(after["attempt_count"] == 1, f"attempt_count changed: {after['attempt_count']}")

    served = read_result(api_base, result_id)
    a.require(served.status_code == 404 and code_of(served) == "RESULT_NOT_READY",
              f"the result route must not serve an uncommitted result: "
              f"{served.status_code} {served.text[:160]}")

    # Renewal must not resurrect the expired lease, and a repeat submission must
    # stay refused (no path by which the same proof becomes valid again).
    expiry_before = row(engine, job_id)["lease_expires_at"]
    renewed = renew(api_base, job_id, lease, WORKER_A)
    expiry_after = row(engine, job_id)["lease_expires_at"]
    a.require(renewed.status_code == 409 and code_of(renewed) == "LEASE_EXPIRED",
              f"renewal of an expired lease must be refused: {renewed.status_code} {renewed.text[:160]}")
    a.require(expiry_after == expiry_before,
              f"a refused renewal must not move the expiry: {expiry_before} -> {expiry_after}")
    again = submit(api_base, job_id, lease, BODY_A, WORKER_A)
    a.require(again.status_code == 409 and row(engine, job_id)["result_json"] is None,
              f"a repeated expired submission must stay refused: {again.status_code}")
    return {"job_id": job_id, "lease": lease, "status": response.status_code,
            "code": code_of(response), "renew": [renewed.status_code, code_of(renewed)],
            "row": _state(engine, job_id)}, a.failures


# ---------------------------------------------------------------------------
# Case B — expired lease, then a reclaim at generation G+1
# ---------------------------------------------------------------------------


def case_b(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, result_id, lease_a = _claim_and_lease(api_base, engine, WORKER_A)
    expire_in_store(engine, job_id)

    reclaimed = claim(api_base, WORKER_B)
    lease_b = reclaimed.json()["job"]["lease"]
    a.require(reclaimed.status_code == 200 and reclaimed.json()["status"] == "CLAIMED",
              f"B's reclaim must be legal after expiry: {reclaimed.status_code} {reclaimed.text[:160]}")
    a.require(lease_b["owner"] == WORKER_B and lease_a["owner"] == WORKER_A,
              "the two actors must be separate worker identities")
    a.require(lease_b["generation"] == lease_a["generation"] + 1,
              f"B must hold a strictly newer generation: {lease_a} -> {lease_b}")

    stale = submit(api_base, job_id, lease_a, BODY_A, WORKER_A)
    mid = row(engine, job_id)
    a.require(stale.status_code == 409 and code_of(stale) == "LEASE_LOST",
              f"a SUPERSEDED worker is refused as LEASE_LOST, not LEASE_EXPIRED: "
              f"{stale.status_code} {code_of(stale)!r} {stale.text[:160]}")
    a.require(mid["result_json"] is None and mid["lease_owner"] == WORKER_B,
              f"A's stale candidate must not be stored: {_state(engine, job_id)}")

    committed = submit(api_base, job_id, lease_b, BODY_B, WORKER_B)
    final = row(engine, job_id)
    envelope = committed.json() if committed.status_code == 200 else {}
    a.require(committed.status_code == 200 and envelope.get("api_status") == "VERIFIED",
              f"B's commit must be accepted: {committed.status_code} {committed.text[:160]}")
    a.require(final["status"] == "COMPLETED" and final["result_json"] is not None,
              f"the job must be COMPLETED with a stored result: {final['status']}")
    a.require(final["lease_generation"] == lease_b["generation"],
              f"the authoritative generation must be B's: {final['lease_generation']}")
    a.require(final["result_json"] is not None
              and json.loads(final["result_json"]) == envelope,
              "the stored result must be exactly B's committed envelope")
    a.require(MARKER not in (final["result_json"] or ""),
              "A's stale candidate marker leaked into the authoritative result")
    served = read_result(api_base, result_id)
    a.require(served.status_code == 200 and served.json() == envelope,
              f"the result route must serve B's envelope: {served.status_code}")
    return {"job_id": job_id, "gen_a": lease_a["generation"], "gen_b": lease_b["generation"],
            "stale": [stale.status_code, code_of(stale)],
            "final": _state(engine, job_id)}, a.failures


# ---------------------------------------------------------------------------
# Case C1 — a commit racing an expiry that commits first: expiry wins
# ---------------------------------------------------------------------------


def case_c1(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, _result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)

    conn, tx = hold_row(engine, job_id)
    try:
        request = Background(lambda: submit(api_base, job_id, lease, BODY_A, WORKER_A))
        blocked = wait_blocked(engine, "SET status = 'COMPLETED'")
        a.require(blocked, "the result commit never reached the database inside the bound")
        a.require(row(engine, job_id)["result_json"] is None,
                  "nothing may be committed while the commit statement is blocked")
        conn.execute(
            text("UPDATE platrixa_async_jobs SET lease_expires_at = now() - interval '1 hour' "
                 "WHERE job_id = :j"),
            {"j": job_id},
        )
        tx.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    a.require(request.join(), "the blocked commit request never finished")
    response = request.outcome.get("value")
    a.require(response is not None, f"the commit request failed: {request.outcome.get('error')!r}")
    if response is not None:
        a.require(response.status_code == 409 and code_of(response) == "LEASE_EXPIRED",
                  f"expiry committed BEFORE the UPDATE must win: "
                  f"{response.status_code} {code_of(response)!r} {response.text[:300]}")
    final = row(engine, job_id)
    a.require(final["status"] == "PROCESSING" and final["result_json"] is None,
              f"the expiry-winning serialization must store nothing: {_state(engine, job_id)}")
    return {"job_id": job_id, "blocked": True,
            "status": getattr(response, "status_code", None),
            "code": code_of(response) if response is not None else "",
            "row": _state(engine, job_id)}, a.failures


# ---------------------------------------------------------------------------
# Case C2 — a commit that legitimately happened before expiry is not undone
# ---------------------------------------------------------------------------


def case_c2(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)

    committed = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.require(committed.status_code == 200 and committed.json().get("api_status") == "VERIFIED",
              f"a live lease must commit: {committed.status_code} {committed.text[:160]}")
    envelope = committed.json() if committed.status_code == 200 else {}
    stored_after_commit = row(engine, job_id)["result_json"]

    # Retroactive expiry must not invalidate an already-committed transaction.
    expire_in_store(engine, job_id)
    after = row(engine, job_id)
    a.require(after["status"] == "COMPLETED" and after["result_json"] == stored_after_commit,
              "a later expiry must not invalidate a committed result")

    replay = submit(api_base, job_id, lease, BODY_A, WORKER_A)
    a.require(replay.status_code == 200 and replay.headers.get("Idempotent-Replayed") == "true",
              f"a post-commit submission is a labelled replay: {replay.status_code} "
              f"{replay.headers.get('Idempotent-Replayed')}")
    a.require(replay.json() == envelope, "the replay must return the COMMITTED envelope, not A's body")
    a.require(row(engine, job_id)["result_json"] == stored_after_commit,
              "the replay must not overwrite the authoritative result")
    a.require(MARKER not in (stored_after_commit or ""),
              "A's later candidate leaked into the authoritative result")
    served = read_result(api_base, result_id)
    a.require(served.status_code == 200 and served.json() == envelope,
              "the served result must remain the committed envelope")
    return {"job_id": job_id, "commit": committed.status_code, "replay": replay.status_code,
            "replayed": replay.headers.get("Idempotent-Replayed"),
            "retroactive_expiry_kept_result": after["status"]}, a.failures


# ---------------------------------------------------------------------------
# Case D1 — a concurrent claim cannot preempt an in-flight commit
# ---------------------------------------------------------------------------


def case_d1(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, _result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)

    conn, tx = hold_row(engine, job_id)
    try:
        request = Background(lambda: submit(api_base, job_id, lease, BODY_A, WORKER_A))
        a.require(wait_blocked(engine, "SET status = 'COMPLETED'"),
                  "the result commit never reached the database inside the bound")
        # The REAL claim statement, concurrently: SELECT ... FOR UPDATE SKIP LOCKED
        # must skip a row a live transaction is touching, not steal it.
        racer = async_jobs.claim_next_job(
            kind=async_jobs.JOB_KIND_INFERENCE, tenant_id=TENANT, owner=WORKER_B
        )
        a.require(racer is None, f"a concurrent claim must skip the in-flight row, got {racer}")
        a.require(row(engine, job_id)["lease_generation"] == lease["generation"],
                  "the concurrent claim must not have bumped the generation")
        tx.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    a.require(request.join(), "the blocked commit request never finished")
    response = request.outcome.get("value")
    detail = (f"{response.status_code} {response.text[:160]}" if response is not None
              else repr(request.outcome.get("error")))
    a.require(response is not None and response.status_code == 200,
              f"the earlier-ordered commit must be accepted: {detail}")
    final = row(engine, job_id)
    a.require(final["status"] == "COMPLETED" and final["lease_generation"] == lease["generation"],
              f"the authoritative state must be the commit that serialized first: {_state(engine, job_id)}")
    a.require(async_jobs.claim_next_job(
        kind=async_jobs.JOB_KIND_INFERENCE, tenant_id=TENANT, owner=WORKER_B) is None,
        "a COMPLETED job must never be claimable")
    return {"job_id": job_id, "concurrent_claim": "NONE (skipped)",
            "commit": getattr(response, "status_code", None), "row": _state(engine, job_id)}, a.failures


# ---------------------------------------------------------------------------
# Case D2 — reclaim ordered first: the old generation must lose
# ---------------------------------------------------------------------------


def case_d2(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, _result_id, lease_a = _claim_and_lease(api_base, engine, WORKER_A)
    expire_in_store(engine, job_id)
    reclaimed = claim(api_base, WORKER_B)
    lease_b = reclaimed.json()["job"]["lease"]
    a.require(lease_b["generation"] == lease_a["generation"] + 1,
              f"reclaim generation: {lease_a['generation']} -> {lease_b['generation']}")

    a_submit = submit(api_base, job_id, lease_a, BODY_A, WORKER_A)
    a.require(a_submit.status_code == 409 and code_of(a_submit) == "LEASE_LOST",
              f"A (old generation) must lose: {a_submit.status_code} {code_of(a_submit)!r}")
    a.require(row(engine, job_id)["result_json"] is None, "A must not have stored a candidate")

    b_submit = submit(api_base, job_id, lease_b, BODY_B, WORKER_B)
    envelope = b_submit.json() if b_submit.status_code == 200 else {}
    a.require(b_submit.status_code == 200 and envelope.get("api_status") == "VERIFIED",
              f"B (new generation) must commit: {b_submit.status_code}")
    stored_b = row(engine, job_id)["result_json"]

    late = submit(api_base, job_id, lease_a, BODY_A, WORKER_A)
    a.require(late.status_code == 200 and late.headers.get("Idempotent-Replayed") == "true",
              f"a late old-generation submission after commit replays: {late.status_code} "
              f"{late.headers.get('Idempotent-Replayed')}")
    a.require(late.json() == envelope, "the replay must carry B's envelope, not A's candidate")
    a.require(row(engine, job_id)["result_json"] == stored_b,
              "the old generation must not overwrite B's authoritative result")
    return {"job_id": job_id, "gen_a": lease_a["generation"], "gen_b": lease_b["generation"],
            "a": [a_submit.status_code, code_of(a_submit)],
            "late": [late.status_code, late.headers.get("Idempotent-Replayed")]}, a.failures


# ---------------------------------------------------------------------------
# Case E — renewal versus expiry
# ---------------------------------------------------------------------------


def case_e1(api_base, engine) -> tuple:
    """A renewal accepted BEFORE expiry really extends the persisted lease."""
    fresh(engine)
    a = Assertions()
    job_id, _result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)
    before = row(engine, job_id)["lease_expires_at"]
    renewed = renew(api_base, job_id, lease, WORKER_A)
    after = row(engine, job_id)["lease_expires_at"]
    a.require(renewed.status_code == 200 and renewed.json().get("renewed") is True,
              f"a live lease must renew: {renewed.status_code} {renewed.text[:160]}")
    a.require(after > before, f"the persisted expiry must move forward: {before} -> {after}")
    a.require(after > db_now(engine), f"the renewed lease must still be in the future: {after}")
    committed = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.require(committed.status_code == 200 and committed.json().get("api_status") == "VERIFIED",
              f"a renewed lease must authorize its commit: {committed.status_code}")
    return {"job_id": job_id, "renew": renewed.status_code, "extended": after > before,
            "commit": committed.status_code}, a.failures


def case_e2(api_base, engine) -> tuple:
    """A renewal attempted AFTER expiry is refused and cannot resurrect the lease."""
    fresh(engine)
    a = Assertions()
    job_id, _result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)
    expire_in_store(engine, job_id)
    expired_at = row(engine, job_id)["lease_expires_at"]
    renewed = renew(api_base, job_id, lease, WORKER_A)
    after = row(engine, job_id)["lease_expires_at"]
    a.require(renewed.status_code == 409 and code_of(renewed) == "LEASE_EXPIRED",
              f"an expired lease cannot renew: {renewed.status_code} {code_of(renewed)!r} "
              f"{renewed.text[:160]}")
    a.require(after == expired_at,
              f"the refused renewal must not move the expiry (no resurrection): {expired_at} -> {after}")
    a.require(after < db_now(engine), "the lease must still be expired after the refusal")
    committed = submit(api_base, job_id, lease, BODY_A, WORKER_A)
    a.require(committed.status_code == 409 and code_of(committed) == "LEASE_EXPIRED",
              f"and the commit stays refused: {committed.status_code} {code_of(committed)!r}")
    return {"job_id": job_id, "renew": [renewed.status_code, code_of(renewed)],
            "commit": [committed.status_code, code_of(committed)],
            "resurrected": after != expired_at}, a.failures


def case_e3(api_base, engine) -> tuple:
    """A renewal BLOCKED on the row, overtaken by a committed expiry, must lose."""
    fresh(engine)
    a = Assertions()
    job_id, _result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)
    conn, tx = hold_row(engine, job_id)
    try:
        request = Background(lambda: renew(api_base, job_id, lease, WORKER_A))
        blocked = wait_blocked(engine, "SET lease_expires_at = now() + make_interval")
        a.require(blocked, "the renewal never reached the database inside the bound")
        conn.execute(
            text("UPDATE platrixa_async_jobs SET lease_expires_at = now() - interval '1 hour' "
                 "WHERE job_id = :j"),
            {"j": job_id},
        )
        tx.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    a.require(request.join(), "the blocked renewal never finished")
    response = request.outcome.get("value")
    a.require(response is not None and response.status_code == 409,
              f"a renewal that lost to a committed expiry must be refused: "
              f"{getattr(response, 'status_code', None)}")
    if response is not None:
        a.require(code_of(response) == "LEASE_EXPIRED",
                  f"the refusal must name expiry: {code_of(response)!r}")
    final = row(engine, job_id)
    a.require(final["lease_expires_at"] < db_now(engine),
              f"the lease must remain expired: {final['lease_expires_at']}")
    return {"job_id": job_id, "renew": getattr(response, "status_code", None),
            "code": code_of(response) if response is not None else ""}, a.failures


# ---------------------------------------------------------------------------
# Case F — duplicate and replay
# ---------------------------------------------------------------------------


def case_f(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, result_id, lease = _claim_and_lease(api_base, engine, WORKER_A)
    first = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.require(first.status_code == 200 and first.json().get("api_status") == "VERIFIED",
              f"the first submission must commit: {first.status_code} {first.text[:160]}")
    envelope = first.json() if first.status_code == 200 else {}
    stored = row(engine, job_id)["result_json"]
    gen = row(engine, job_id)["lease_generation"]

    second = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.require(second.status_code == 200 and second.headers.get("Idempotent-Replayed") == "true",
              f"the duplicate must be labelled as a replay: {second.status_code} "
              f"{second.headers.get('Idempotent-Replayed')}")
    a.require(first.json() == second.json(),
              "the replay must return the SAME envelope (a recompute would differ)")
    a.require(row(engine, job_id)["result_json"] == stored,
              "the duplicate must not replace the authoritative result")
    a.require(row(engine, job_id)["lease_generation"] == gen,
              "the duplicate must not bump the generation")
    served = read_result(api_base, result_id)
    a.require(served.status_code == 200 and served.json() == envelope,
              "the result route must serve the same envelope")
    return {"job_id": job_id, "first": first.status_code, "second": second.status_code,
            "replayed": second.headers.get("Idempotent-Replayed")}, a.failures


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

RUNNERS = [
    ("A expired-unsuperseded lease cannot commit", case_a),
    ("B expired lease + reclaim at G+1", case_b),
    ("C1 commit racing an expiry that commits first", case_c1),
    ("C2 commit before expiry is not retroactively undone", case_c2),
    ("D1 concurrent claim cannot preempt an in-flight commit", case_d1),
    ("D2 reclaim ordered first: old generation loses", case_d2),
    ("E1 renewal before expiry extends the persisted lease", case_e1),
    ("E2 renewal after expiry cannot resurrect it", case_e2),
    ("E3 renewal blocked, overtaken by a committed expiry", case_e3),
    ("F duplicate submission is a replay, never a rewrite", case_f),
]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    iterations = int(argv[0]) if argv else 20

    print("=" * 78)
    print("Stage 2 O1 — lease EXPIRY as commit authority (isolated, deterministic)")
    print("=" * 78)
    print("isolation guardrails: OK (loopback only, no PLATRIXA_* origin, no real credential)")

    previous_lease = os.environ.get(LEASE_ENV)
    os.environ[LEASE_ENV] = LEASE_SECONDS
    srv, engine = S1.pg_backend()
    developer.set_client(S1.HarnessClient())
    app = create_app()
    bridge_server, bridge_url = S2.start_bridge()
    api_server, api_thread, api_base = S2.start_api(app)
    print(f"real API on {api_base} (uvicorn, loopback) | mock model bridge on {bridge_url}")
    print(f"real PostgreSQL store (disposable embedded cluster) | iterations={iterations}")
    check("the O1 API origin is loopback", S2._is_loopback(api_base), api_base)
    check("the mock bridge origin is loopback", S2._is_loopback(bridge_url), bridge_url)

    evidence: dict = {}
    try:
        for label, fn in RUNNERS:
            scenario(f"{label} — {iterations} iterations, fresh state each time")
            runs = []
            for index in range(iterations):
                payload, failures = fn(api_base, engine)
                runs.append({"index": index, "payload": payload, "failures": failures})
            bad = [(r["index"], r["failures"]) for r in runs if r["failures"]]
            check(f"{label}: all {len(runs)} iteration(s) satisfied every invariant",
                  not bad, f"{len(bad)} failed: {bad[:2]}")
            evidence[label] = runs

        # ---- cross-iteration aggregates (the observable contract) -----------
        scenario("Aggregate evidence across every iteration")
        seed = {label: runs[0]["payload"] for label, runs in evidence.items()}
        check("A: the expired-but-unsuperseded commit was refused as 409 LEASE_EXPIRED every time",
              all(r["payload"]["status"] == 409 and r["payload"]["code"] == "LEASE_EXPIRED"
                  for r in evidence["A expired-unsuperseded lease cannot commit"]),
              json.dumps(seed["A expired-unsuperseded lease cannot commit"]))
        check("A: the result route kept answering RESULT_NOT_READY (nothing was committed)",
              seed["A expired-unsuperseded lease cannot commit"]["row"]["result_json"] is None)
        check("B: every reclaim gave a strictly newer generation and the stale worker got LEASE_LOST",
              all(r["payload"]["gen_b"] == r["payload"]["gen_a"] + 1
                  and r["payload"]["stale"] == [409, "LEASE_LOST"]
                  for r in evidence["B expired lease + reclaim at G+1"]),
              json.dumps(seed["B expired lease + reclaim at G+1"]))
        check("C1: expiry committed before the blocked UPDATE won in every iteration (409 LEASE_EXPIRED)",
              all(r["payload"]["status"] == 409 and r["payload"]["code"] == "LEASE_EXPIRED"
                  for r in evidence["C1 commit racing an expiry that commits first"]),
              json.dumps(seed["C1 commit racing an expiry that commits first"]))
        check("C2: a commit that serialized before expiry stayed authoritative and replays",
              all(r["payload"]["commit"] == 200 and r["payload"]["replayed"] == "true"
                  and r["payload"]["retroactive_expiry_kept_result"] == "COMPLETED"
                  for r in evidence["C2 commit before expiry is not retroactively undone"]),
              json.dumps(seed["C2 commit before expiry is not retroactively undone"]))
        check("D1: a concurrent claim skipped the in-flight row and the commit kept its generation",
              all(r["payload"]["commit"] == 200 for r in evidence[
                  "D1 concurrent claim cannot preempt an in-flight commit"]),
              json.dumps(seed["D1 concurrent claim cannot preempt an in-flight commit"]))
        check("D2: the reclaim-first order made the old generation lose, and its late arrival replayed",
              all(r["payload"]["a"] == [409, "LEASE_LOST"]
                  and r["payload"]["late"][0] == 200 and r["payload"]["late"][1] == "true"
                  for r in evidence["D2 reclaim ordered first: old generation loses"]),
              json.dumps(seed["D2 reclaim ordered first: old generation loses"]))
        check("E1: an accepted renewal extended the persisted expiry and then authorized the commit",
              all(r["payload"]["renew"] == 200 and r["payload"]["commit"] == 200
                  and r["payload"]["extended"] is True
                  for r in evidence["E1 renewal before expiry extends the persisted lease"]),
              json.dumps(seed["E1 renewal before expiry extends the persisted lease"]))
        check("E2/E3: no refused renewal resurrected an expired lease (expiry is a revocation)",
              all(r["payload"]["resurrected"] is False
                  for r in evidence["E2 renewal after expiry cannot resurrect it"])
              and all(r["payload"]["code"] == "LEASE_EXPIRED"
                      for r in evidence["E3 renewal blocked, overtaken by a committed expiry"]),
              json.dumps(seed["E2 renewal after expiry cannot resurrect it"]))
        check("F: a duplicate submission returned the identical envelope, labelled as a replay",
              all(r["payload"]["first"] == 200 and r["payload"]["second"] == 200
                  and r["payload"]["replayed"] == "true"
                  for r in evidence["F duplicate submission is a replay, never a rewrite"]),
              json.dumps(seed["F duplicate submission is a replay, never a rewrite"]))
    finally:
        developer.reset_client()
        bridge_server.shutdown()
        api_server.should_exit = True
        api_thread.join(timeout=5)
        if previous_lease is None:
            os.environ.pop(LEASE_ENV, None)
        else:
            os.environ[LEASE_ENV] = previous_lease
        try:
            srv.cleanup()
        except Exception as exc:  # pragma: no cover
            print(f"note: pgserver cleanup raised {type(exc).__name__}: {exc}")
        # The embedded cluster's data directory is disposable: remove it so this
        # suite leaves no temporary database behind.
        shutil.rmtree(str(_PG_DATA), ignore_errors=True)

    passed = sum(1 for _, ok, _, _ in S2.CHECKS if ok)
    total = len(S2.CHECKS)
    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{total} checks passed")
    for name, ok, detail, kind in S2.CHECKS:
        if not ok:
            print(f"  FAIL ({kind}) {name} — {detail}")
    print("=" * 78)
    leftovers = S2.heartbeat_threads()
    # Server-owned threads (uvicorn's loop thread, the mock bridge's server
    # thread, AnyIO's workers and the app's own request-log consumer) are
    # infrastructure, exactly as the Stage 2 audit suite classifies them. This
    # suite starts NO worker, so any heartbeat thread here would be a leak.
    infrastructure = {"o1-api-server", "o1-bridge", "audit-api-server",
                      "audit-mock-bridge", "platrixa-request-log"}
    strays = [t.name for t in threading.enumerate()
              if t.is_alive() and t is not threading.main_thread()
              and t.name not in infrastructure
              and not t.name.startswith(("Thread-", "AnyIO", "asyncio"))]
    print(f"worker heartbeat threads still alive after teardown: {leftovers}")
    print(f"stray non-infrastructure threads after teardown: {sorted(set(strays))}")
    print(f"disposable PostgreSQL directory present: {_PG_DATA.exists()}")
    ok = passed == total and not leftovers and not strays and not _PG_DATA.exists()
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(2)


