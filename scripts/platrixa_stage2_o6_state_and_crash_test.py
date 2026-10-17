#!/usr/bin/env python3
"""Stage 2 O6 — failure/release state transitions, reclaim interleaving, crash.

What this suite answers, with real state rather than inference
==============================================================
O6 (the audit's open question): ``fail_job`` and the document path's
``_release_lease`` were the two writes that never had a liveness predicate.
Rather than adding expiry "for symmetry", this suite establishes what each path
actually guarantees and pins the one real defect that was there:

  * ``fail_job`` is ownership-fenced (owner + generation + PROCESSING) but NOT
    expiry-fenced, and that is correct: it can never publish a result, can never
    touch a newer generation, and its two terminal branches (RETRY_WAIT while
    budget remains, FAILED otherwise/deterministic) already coincide with what a
    reclaim would reach. Cases G1-G6 measure that.
  * ``_release_lease`` was an UNFENCED ``WHERE job_id = :j`` — a stale record
    could re-queue a job a NEWER worker owns and destroy that worker's live
    lease. It now goes through the fenced ``async_jobs.release_job``.
    Cases H1-H7 measure the new transition table, including a counterfactual
    that runs the pre-O6 statement to show the hazard was real.

Concurrency case A (§I): the reclaim that lands BETWEEN the kernel's validation
and the commit UPDATE — the exact ordering the earlier audit could only cover
"adjacent" to. A real request is paused inside the real kernel call (after the
real pipeline ran, before the route reaches ``complete_job``), the lease is
expired and reclaimed at G+1, and only then is the request resumed.

Two app instances are used, and that is a deliberate, documented property:
``submit_inference_result`` is an ``async def`` handler that runs the kernel
SYNCHRONOUSLY, so while A's request is parked inside the kernel its server's
event loop is occupied — every other request to that server waits. Worker B is
therefore served by a SECOND uvicorn instance in the same process (its own
event loop, the same app, the same store, the same kernel stub), so B's claim
and commit are genuine HTTP requests through the real routes rather than a
weaker in-process substitute. (The blocking-loop property itself is recorded as
observation O8 in the audit report; it is not changed here.)

Concurrency case B (§J): SIGKILL, with the three outcomes the request demands
and no claim of coverage beyond them:

  J1 crash BEFORE the commit statement is issued   → nothing committed;
  J2 SIGKILL while this process's commit UPDATE is blocked inside PostgreSQL
     → PostgreSQL ROLLS BACK (measured: row byte-identical, same generation
       still commits afterwards);
  J3 the client dies while the SERVER is mid-commit → the commit still lands;
     a retry at the same generation replays one identical authoritative result.

Real layers only: the real FastAPI app on a loopback socket, the real
PostgreSQL store (disposable embedded cluster) and the real ``async_jobs`` SQL.
The only substitutions are the Stage 1 kernel stub and (in §J) small driver
processes that call the real store function over the real database.

Run:
    .venv/bin/python scripts/platrixa_stage2_o6_state_and_crash_test.py [iterations]
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
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
TENANT = S2.TENANT_A
CREATOR = "o6-creator"
WORKER_A = "o6-worker-a"
WORKER_B = "o6-worker-b"
RAW_INPUT = S1.RAW_INPUT

# A's candidate carries a marker: if a stale/expired write ever landed, the
# marker would appear in the authoritative result.
MARKER = "o6-stale-candidate-marker"
_STALE = json.loads(json.dumps(S1.VALID_CANDIDATE))
_STALE["ambiguities"] = [MARKER]
BODY_A = S1.bridge_body(_STALE)
BODY_B = S1.bridge_body(S1.VALID_CANDIDATE)

_PG_DATA = Path("/tmp/platrixa_fte89_pgdata")
LEASE_ENV = async_jobs.INFERENCE_LEASE_ENV_VAR
LEASE_SECONDS = "60"

check = S2.check
scenario = S2.scenario


# ---------------------------------------------------------------------------
# Real HTTP (loopback uvicorn)
# ---------------------------------------------------------------------------


def _headers(worker_id: str, key: str = KEY) -> dict:
    return {"X-Platrixa-API-Key": key, "X-Platrixa-Worker-Id": worker_id}


def create_job(api_base: str):
    response = requests.post(
        f"{api_base}/v1/inference-jobs",
        json={"raw_input": RAW_INPUT},
        headers=_headers(CREATOR),
        timeout=20,
    )
    assert response.status_code in (200, 201, 202), (response.status_code, response.text[:300])
    return response.json()["job_id"], response.json()["result_id"]


def claim(api_base: str, worker_id: str):
    return requests.post(
        f"{api_base}/v1/worker/jobs/claim", headers=_headers(worker_id), timeout=20
    )


def claim_job(api_base: str, worker_id: str, *, create: bool = True, job_id: str | None = None):
    """Create (when ``create``) one new job and claim it, through the real routes.

    With ``create=False`` the claim is a RECLAIM: it must return exactly the job
    the caller expects, which is how a later generation is obtained.
    """
    expected = job_id
    if create and job_id is None:
        expected, _result_id = create_job(api_base)
    response = claim(api_base, worker_id)
    assert response.status_code == 200, (response.status_code, response.text[:300])
    body = response.json()
    assert body.get("status") == "CLAIMED", body
    if expected is not None:
        assert body["job"]["job_id"] == expected, (body["job"]["job_id"], expected)
    return body["job"]["job_id"], body["job"]["lease"]


def submit(api_base: str, job_id: str, lease: dict, body: dict, worker_id: str):
    return requests.post(
        f"{api_base}/v1/worker/jobs/{job_id}/result",
        json={
            "lease": {"owner": lease["owner"], "generation": lease["generation"]},
            "bridge_body": body,
        },
        headers=_headers(worker_id),
        timeout=60,
    )


def renew(api_base: str, job_id: str, lease: dict, worker_id: str):
    return requests.post(
        f"{api_base}/v1/worker/jobs/{job_id}/lease",
        json={"lease": {"owner": lease["owner"], "generation": lease["generation"]}},
        headers=_headers(worker_id),
        timeout=20,
    )


def code_of(response) -> str:
    try:
        return str((response.json().get("error") or {}).get("code") or "")
    except Exception:
        return ""


class Assertions:
    """Collects every failure instead of stopping at the first."""

    def __init__(self) -> None:
        self.failures: list = []

    def require(self, condition, message) -> bool:
        if not condition:
            self.failures.append(message)
        return bool(condition)

    def equal(self, actual, expected, message) -> bool:
        return self.require(actual == expected, f"{message} (got {actual!r}, expected {expected!r})")


# ---------------------------------------------------------------------------
# Store-side helpers (real store module, real PostgreSQL)
# ---------------------------------------------------------------------------

_JOB_COLUMNS = (
    "status, lease_owner, lease_generation, result_json, error_json, http_status, "
    "retryable, attempt_count, max_attempts, next_attempt_at, lease_expires_at, "
    "updated_at, request_json"
)


def snapshot(engine, job_id: str) -> dict:
    """Every mutable column, so a refusal can be shown to have written NOTHING."""
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {_JOB_COLUMNS} FROM platrixa_async_jobs WHERE job_id = :j"),
            {"j": job_id},
        ).mappings().first()
    return dict(row) if row else {}


def expire(engine, job_id: str) -> None:
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

    This is what makes the ordering a fact about the database instead of a
    guess about timing.
    """
    deadline = time.monotonic() + timeout
    sql = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND state = 'active' "
        "  AND wait_event_type = 'Lock' AND query LIKE :n"
    )
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            if (conn.execute(sql, {"n": f"%{needle}%"}).scalar() or 0) > 0:
                return True
        time.sleep(0.02)
    return False


def wait_unblocked(engine, needle: str, timeout: float = 20.0) -> bool:
    """Wait until no statement matching ``needle`` is waiting on a lock."""
    deadline = time.monotonic() + timeout
    sql = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND state = 'active' "
        "  AND query LIKE :n"
    )
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            if (conn.execute(sql, {"n": f"%{needle}%"}).scalar() or 0) == 0:
                return True
        time.sleep(0.02)
    return False


class Background:
    """Run one real call in a thread and keep its outcome."""

    def __init__(self, fn) -> None:
        self.outcome: dict = {}
        self.thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self.thread.start()

    def _run(self, fn) -> None:
        try:
            self.outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported, then asserted on
            self.outcome["error"] = exc

    def join(self, timeout: float = 60.0) -> bool:
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()


def fresh(engine) -> None:
    S2.reset(engine)


def stored_result(engine, job_id: str):
    raw = snapshot(engine, job_id).get("result_json")
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# G — fail_job state transitions (real store statement, real PostgreSQL)
# ---------------------------------------------------------------------------

_FAIL_ENVELOPE = {"error": {"code": "PROVIDER_UNAVAILABLE", "message": "provider down"}}


def g1_valid_failure(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    before = snapshot(engine, job_id)

    accepted = async_jobs.fail_job(
        job_id,
        envelope=_FAIL_ENVELOPE,
        http_status=503,
        retryable=True,
        lease_owner=lease["owner"],
        lease_generation=lease["generation"],
    )
    after = snapshot(engine, job_id)

    a.require(accepted is True, "a current owner must be able to record a failure")
    a.equal(after["status"], "RETRY_WAIT", "a retryable failure with budget left defers")
    a.equal(after["lease_owner"], None, "the lease must be cleared")
    a.equal(after["lease_expires_at"], None, "the lease must be cleared")
    a.equal(after["lease_generation"], before["lease_generation"], "failing must not bump the generation")
    a.equal(after["result_json"], None, "a failure must never write an authoritative result")
    a.require("PROVIDER_UNAVAILABLE" in (after["error_json"] or ""), "the error envelope must be stored")
    a.require(after["next_attempt_at"] is not None, "RETRY_WAIT must carry the backoff gate")
    # The backoff gate is real: an immediate claim must find no work.
    a.require(claim(api_base, WORKER_B).json().get("status") == "NO_WORK",
              "a job in backoff must not be claimable immediately")
    return {"job_id": job_id, "status": after["status"], "accepted": accepted}, a.failures


def g2_expired_failure(api_base, engine) -> tuple:
    """An expired-but-unsuperseded owner may still record its own failure.

    Documented behaviour, not an oversight: this path asserts no authority over
    a result, and the state it writes is the state a reclaim would reach.
    """
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    gen = lease["generation"]
    expire(engine, job_id)

    accepted = async_jobs.fail_job(
        job_id,
        envelope=_FAIL_ENVELOPE,
        http_status=503,
        retryable=True,
        lease_owner=lease["owner"],
        lease_generation=lease["generation"],
    )
    after = snapshot(engine, job_id)

    a.require(accepted is True, "the owner's own failure is still recorded after expiry")
    a.equal(after["status"], "RETRY_WAIT", "the outcome equals what a reclaim would produce")
    a.equal(after["result_json"], None, "an expired failure must never be authoritative")
    a.equal(after["lease_generation"], gen, "the generation is untouched")
    # Recovery is preserved: the job IS claimable once the backoff gate opens.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE platrixa_async_jobs SET next_attempt_at = NULL WHERE job_id = :j"),
            {"j": job_id},
        )
    reclaimed = claim(api_base, WORKER_B)
    a.require(reclaimed.status_code == 200 and reclaimed.json().get("status") == "CLAIMED",
              f"the job must still be recoverable ({reclaimed.status_code})")
    a.equal(reclaimed.json()["job"]["lease"]["generation"], gen + 1, "a reclaim bumps the generation")
    return {"job_id": job_id, "status": after["status"], "reclaim_gen": gen + 1}, a.failures


def g3_stale_failure_after_reclaim(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, lease_a = claim_job(api_base, WORKER_A)
    expire(engine, job_id)
    _job_b, lease_b = claim_job(api_base, WORKER_B, create=False, job_id=job_id)
    before = snapshot(engine, job_id)

    accepted = async_jobs.fail_job(
        job_id,
        envelope=_FAIL_ENVELOPE,
        http_status=503,
        retryable=True,
        lease_owner=lease_a["owner"],
        lease_generation=lease_a["generation"],
    )
    after = snapshot(engine, job_id)

    a.require(accepted is False, "a superseded generation must not record a failure")
    a.equal(after, before, "a refused failure must write NOTHING (every column unchanged)")
    a.equal(after["status"], "PROCESSING", "B's claim survives")
    a.equal(after["lease_owner"], WORKER_B, "B still owns the job")
    a.equal(after["lease_generation"], lease_b["generation"], "B's generation is intact")
    # And B can still complete, which is the consequence that matters.
    committed = submit(api_base, job_id, lease_b, BODY_B, WORKER_B)
    a.require(committed.status_code == 200, f"B must still be able to commit ({committed.status_code})")
    a.equal(snapshot(engine, job_id)["status"], "COMPLETED", "B's commit landed")
    return {"job_id": job_id, "refused": accepted is False, "gen_b": lease_b["generation"]}, a.failures


def g4_repeated_failure(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    first = async_jobs.fail_job(
        job_id, envelope=_FAIL_ENVELOPE, http_status=503, retryable=True,
        lease_owner=lease["owner"], lease_generation=lease["generation"],
    )
    after_first = snapshot(engine, job_id)
    second = async_jobs.fail_job(
        job_id, envelope=_FAIL_ENVELOPE, http_status=503, retryable=True,
        lease_owner=lease["owner"], lease_generation=lease["generation"],
    )
    after_second = snapshot(engine, job_id)

    a.require(first is True and second is False, f"first={first} second={second}")
    a.equal(after_second, after_first, "a repeated failure must be a no-op")
    return {"job_id": job_id, "first": first, "second": second}, a.failures


def g5_failure_overtaken_by_rank(api_base, engine) -> tuple:
    """A failure statement WAITING on the row while a reclaim commits loses.

    The control transaction commits the claim's own UPDATE shape (status →
    PROCESSING, owner → B, generation + 1) while A's failure is blocked, which is
    the ordering a reclaim produces; the real claim path is exercised by G2/G3.
    """
    fresh(engine)
    a = Assertions()
    job_id, lease_a = claim_job(api_base, WORKER_A)
    expire(engine, job_id)

    conn, tx = hold_row(engine, job_id)
    try:
        pending = Background(
            lambda: async_jobs.fail_job(
                job_id, envelope=_FAIL_ENVELOPE, http_status=503, retryable=True,
                lease_owner=lease_a["owner"], lease_generation=lease_a["generation"],
            )
        )
        blocked = wait_blocked(engine, "UPDATE platrixa_async_jobs SET status = CASE")
        a.require(blocked, "A's failure statement never reached the lock")
        conn.execute(
            text(
                "UPDATE platrixa_async_jobs SET status = 'PROCESSING', lease_owner = :o, "
                "lease_generation = lease_generation + 1, attempt_count = attempt_count + 1, "
                "lease_expires_at = now() + interval '60 seconds', updated_at = now() "
                "WHERE job_id = :j"
            ),
            {"o": WORKER_B, "j": job_id},
        )
        tx.commit()
    finally:
        if tx.is_active:
            tx.rollback()
        conn.close()

    a.require(pending.join(), "the blocked failure never returned")
    after = snapshot(engine, job_id)

    a.require(pending.outcome.get("value") is False, f"A's failure must lose: {pending.outcome}")
    a.equal(after["status"], "PROCESSING", "the reclaim's state survives")
    a.equal(after["lease_owner"], WORKER_B, "the newer owner is untouched")
    a.equal(after["error_json"], None, "no failure envelope was written")
    return {"job_id": job_id, "refused": pending.outcome.get("value") is False}, a.failures


def g6_failure_racing_a_real_claim(api_base, engine) -> tuple:
    """Genuine concurrent race: A's failure vs B's claim, both through real code.

    Either serialization order is valid; the invariant is that the job is never
    double-processed and never carries an authoritative result from A.
    """
    fresh(engine)
    a = Assertions()
    job_id, lease_a = claim_job(api_base, WORKER_A)
    expire(engine, job_id)

    failer = Background(
        lambda: async_jobs.fail_job(
            job_id, envelope=_FAIL_ENVELOPE, http_status=503, retryable=True,
            lease_owner=lease_a["owner"], lease_generation=lease_a["generation"],
        )
    )
    claimed = claim(api_base, WORKER_B)
    a.require(failer.join(), "the failure call never returned")

    after = snapshot(engine, job_id)
    failed = failer.outcome.get("value")
    claim_body = claimed.json() if claimed.status_code == 200 else {}
    claimed_job = claim_body.get("status") == "CLAIMED"

    # Exactly one of the two serializations happened, and each is coherent.
    if failed is True:
        a.equal(after["status"], "RETRY_WAIT", "failure won → the job is deferred")
        a.require(not claimed_job, "failure won → the reclaim must find no work")
    else:
        a.require(failed is False, f"the failure must report a refusal, got {failed!r}")
        a.require(claimed_job, "the claim won → it must have got the job")
        a.equal(after["status"], "PROCESSING", "the claim's state survives")
        a.require(after["error_json"] is None, "no failure envelope may be written")
    a.equal(after["result_json"], None, "neither order may publish a result")
    return {
        "job_id": job_id,
        "failed": failed,
        "claimed": claimed_job,
        "order": "failure-first" if failed is True else "claim-first",
    }, a.failures


# ---------------------------------------------------------------------------
# H — release_job state transitions (the O6 fix)
# ---------------------------------------------------------------------------


def h1_valid_release(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    before = snapshot(engine, job_id)

    released = async_jobs.release_job(
        job_id, lease_owner=lease["owner"], lease_generation=lease["generation"]
    )
    after = snapshot(engine, job_id)

    a.require(released is True, "the current owner must be able to release")
    a.equal(after["status"], "QUEUED", "a release re-queues the job")
    a.equal(after["lease_expires_at"], None, "a release clears the deadline")
    a.equal(after["lease_generation"], before["lease_generation"], "a release is not a claim")
    a.equal(after["result_json"], None, "a release never publishes")
    next_claim = claim(api_base, WORKER_B)
    a.require(next_claim.json().get("status") == "CLAIMED", "the released job is immediately claimable")
    a.equal(next_claim.json()["job"]["lease"]["generation"], before["lease_generation"] + 1,
            "the next claim takes the next generation")
    return {"job_id": job_id, "status": after["status"]}, a.failures


def h2_expired_release(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    expire(engine, job_id)

    released = async_jobs.release_job(
        job_id, lease_owner=lease["owner"], lease_generation=lease["generation"]
    )
    after = snapshot(engine, job_id)

    a.require(released is True, "an owning-but-expired worker may hand its own job back")
    a.equal(after["status"], "QUEUED", "the job returns to the queue")
    a.equal(after["result_json"], None, "a release can never be authoritative")
    return {"job_id": job_id, "released": released, "status": after["status"]}, a.failures


def h3_stale_release_cannot_touch_a_newer_lease(api_base, engine) -> tuple:
    """The O6 defect: a stale record must not re-queue a newer worker's job."""
    fresh(engine)
    a = Assertions()
    job_id, lease_a = claim_job(api_base, WORKER_A)
    expire(engine, job_id)
    _job_b, lease_b = claim_job(api_base, WORKER_B, create=False, job_id=job_id)
    before = snapshot(engine, job_id)

    released = async_jobs.release_job(
        job_id, lease_owner=lease_a["owner"], lease_generation=lease_a["generation"]
    )
    after = snapshot(engine, job_id)

    a.require(released is False, "a superseded owner must not release the lease")
    a.equal(after, before, "a refused release must write NOTHING (every column unchanged)")
    a.equal(after["status"], "PROCESSING", "B's job stays PROCESSING")
    a.equal(after["lease_owner"], WORKER_B, "B still owns the lease")
    # The consequence that matters: B's in-flight work still commits.
    committed = submit(api_base, job_id, lease_b, BODY_B, WORKER_B)
    a.require(committed.status_code == 200, f"B must still commit ({committed.status_code})")
    return {"job_id": job_id, "released": released, "status": after["status"]}, a.failures


def h4_repeated_release(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    first = async_jobs.release_job(job_id, lease_owner=lease["owner"],
                                   lease_generation=lease["generation"])
    after_first = snapshot(engine, job_id)
    second = async_jobs.release_job(job_id, lease_owner=lease["owner"],
                                    lease_generation=lease["generation"])
    after_second = snapshot(engine, job_id)
    a.require(first is True and second is False, f"first={first} second={second}")
    a.equal(after_second, after_first, "a repeated release must be a no-op")
    return {"job_id": job_id, "first": first, "second": second}, a.failures


def h5_release_cannot_touch_a_completed_job(api_base, engine) -> tuple:
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    committed = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.require(committed.status_code == 200, f"the commit must land ({committed.status_code})")
    before = snapshot(engine, job_id)

    released = async_jobs.release_job(
        job_id, lease_owner=lease["owner"], lease_generation=lease["generation"]
    )
    after = snapshot(engine, job_id)
    a.require(released is False, "a COMPLETED job must never be re-queued")
    a.equal(after, before, "the authoritative result must be byte-identical")
    a.equal(after["status"], "COMPLETED", "the terminal state stands")
    return {"job_id": job_id, "released": released}, a.failures


def h6_release_overtaken_by_a_reclaim(api_base, engine) -> tuple:
    """A release BLOCKED on the row while a reclaim commits must lose."""
    fresh(engine)
    a = Assertions()
    job_id, lease_a = claim_job(api_base, WORKER_A)
    expire(engine, job_id)

    conn, tx = hold_row(engine, job_id)
    try:
        pending = Background(
            lambda: async_jobs.release_job(
                job_id, lease_owner=lease_a["owner"], lease_generation=lease_a["generation"]
            )
        )
        blocked = wait_blocked(engine, "UPDATE platrixa_async_jobs SET status = 'QUEUED'")
        a.require(blocked, "A's release never reached the lock")
        conn.execute(
            text(
                "UPDATE platrixa_async_jobs SET status = 'PROCESSING', lease_owner = :o, "
                "lease_generation = lease_generation + 1, attempt_count = attempt_count + 1, "
                "lease_expires_at = now() + interval '60 seconds', updated_at = now() "
                "WHERE job_id = :j"
            ),
            {"o": WORKER_B, "j": job_id},
        )
        tx.commit()
    finally:
        if tx.is_active:
            tx.rollback()
        conn.close()

    a.require(pending.join(), "the blocked release never returned")
    after = snapshot(engine, job_id)
    a.require(pending.outcome.get("value") is False, f"the release must lose: {pending.outcome}")
    a.equal(after["status"], "PROCESSING", "the reclaim's state survives")
    a.equal(after["lease_owner"], WORKER_B, "the newer owner is untouched")
    return {"job_id": job_id, "refused": pending.outcome.get("value") is False}, a.failures


def h7_counterfactual_unfenced_release(api_base, engine) -> tuple:
    """Prove the hazard was real: the PRE-O6 statement does clobber a newer lease.

    This is a counterfactual measurement, labelled as such: it runs the exact
    SQL the route used before the O6 fix against a job a NEWER worker owns.
    """
    fresh(engine)
    a = Assertions()
    job_id, lease_a = claim_job(api_base, WORKER_A)
    expire(engine, job_id)
    _job_b, lease_b = claim_job(api_base, WORKER_B, create=False, job_id=job_id)
    before = snapshot(engine, job_id)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_async_jobs SET status = 'QUEUED', "
                "lease_expires_at = NULL, updated_at = now() WHERE job_id = :j"
            ),
            {"j": job_id},
        )
    after = snapshot(engine, job_id)

    a.equal(before["lease_owner"], WORKER_B, "the counterfactual starts from B's live lease")
    a.equal(after["status"], "QUEUED", "the pre-O6 statement re-queues a job B owns")
    a.require(after["result_json"] is None, "the hazard is wasted work, not a wrong result")
    # And the concrete consequence: B's commit is now impossible.
    blocked_commit = submit(api_base, job_id, lease_b, BODY_B, WORKER_B)
    a.require(blocked_commit.status_code == 409,
              f"B's result is refused after the stale re-queue ({blocked_commit.status_code})")
    a.equal(snapshot(engine, job_id)["result_json"], None, "B's work is discarded")
    return {
        "job_id": job_id,
        "pre_o6_status": after["status"],
        "b_commit_after": blocked_commit.status_code,
    }, a.failures


# ---------------------------------------------------------------------------
# I — Concurrency case A: reclaim between kernel validation and the UPDATE
# ---------------------------------------------------------------------------


class _GatedKernel:
    """The REAL kernel, with a test-only pause after it returns.

    ``process_from_source`` runs the whole production pipeline (validation,
    grounding, accounting, rules) and only then waits, which is exactly the
    window the audit asked about: the candidate is validated, the authoritative
    UPDATE has not been issued yet.
    """

    def __init__(self, inner, state: dict) -> None:
        self._inner = inner
        self._state = state

    def process_from_source(self, *args, **kwargs):
        result = self._inner.process_from_source(*args, **kwargs)
        if self._state.get("gate_next"):
            self._state["gate_next"] = False
            self._state["entered"].set()
            self._state["release"].wait(timeout=60)
        return result


class _GatedClient(S1.HarnessClient):
    """Stage 1 kernel stub whose kernel() is the real Kernel, gated once."""

    def __init__(self, state: dict) -> None:
        super().__init__()
        self._gated = _GatedKernel(self._kernel, state)

    def kernel(self):
        return self._gated


# The second app instance's base URL (set by main). Used ONLY where the first
# server's event loop is provably occupied by the parked request.
_ALT: dict = {"base": None}


def _gated_state() -> dict:
    return {"gate_next": False, "entered": threading.Event(), "release": threading.Event()}


def i1_reclaim_between_kernel_and_update(api_base, engine) -> tuple:
    """A validates, B reclaims, A resumes: A must not commit or renew."""
    fresh(engine)
    a = Assertions()
    state = _gated_state()
    developer.set_client(_GatedClient(state))
    try:
        job_id, lease_a = claim_job(api_base, WORKER_A)
        gen_a = lease_a["generation"]
        state["gate_next"] = True

        pending = Background(lambda: submit(api_base, job_id, lease_a, BODY_A, WORKER_A))
        a.require(state["entered"].wait(30), "A's request never reached the post-validation pause")

        # A is parked AFTER validation and BEFORE its UPDATE: expire + reclaim.
        # B is served by the second app instance (see the module docstring).
        expire(engine, job_id)
        _job_b, lease_b = claim_job(_ALT["base"], WORKER_B, create=False, job_id=job_id)
        a.equal(lease_b["generation"], gen_a + 1, "the reclaim took the next generation")

        state["release"].set()
        a.require(pending.join(), "A's request never returned")
        response = pending.outcome.get("value")
        a.require(response is not None, f"A's request failed: {pending.outcome.get('error')}")

        a.equal(response.status_code, 409, "A's kernel-validated candidate must be refused")
        a.equal(code_of(response), "LEASE_LOST", "the refusal names the newer owner")
        after = snapshot(engine, job_id)
        a.equal(after["status"], "PROCESSING", "the job is still B's")
        a.equal(after["lease_owner"], WORKER_B, "B owns it")
        a.equal(after["lease_generation"], lease_b["generation"], "A's generation is gone")
        a.equal(after["result_json"], None, "A committed nothing")

        # A also cannot renew, release, or otherwise touch B's lease.
        a.equal(renew(api_base, job_id, lease_a, WORKER_A).status_code, 409, "A cannot renew")
        a.require(async_jobs.release_job(job_id, lease_owner=lease_a["owner"],
                                         lease_generation=lease_a["generation"]) is False,
                  "A cannot release B's lease")
        a.equal(snapshot(engine, job_id), after, "A's refused calls wrote NOTHING")

        # B's authority is intact (A's request has returned, so this server's
        # loop is free again).
        committed = submit(api_base, job_id, lease_b, BODY_B, WORKER_B)
        a.equal(committed.status_code, 200, "B must still be able to commit")
        stored = stored_result(engine, job_id) or {}
        a.require(MARKER not in json.dumps(stored), "A's marker must never reach the result")
        return {
            "job_id": job_id,
            "a_status": response.status_code,
            "gen_b": lease_b["generation"],
            "b_status": committed.status_code,
        }, a.failures
    finally:
        developer.set_client(S1.HarnessClient())


def i2_reclaim_and_commit_while_a_is_paused(api_base, engine) -> tuple:
    """B reclaims AND commits while A is paused: A's late answer is a replay."""
    fresh(engine)
    a = Assertions()
    state = _gated_state()
    developer.set_client(_GatedClient(state))
    try:
        job_id, lease_a = claim_job(api_base, WORKER_A)
        gen_a = lease_a["generation"]
        state["gate_next"] = True

        pending = Background(lambda: submit(api_base, job_id, lease_a, BODY_A, WORKER_A))
        a.require(state["entered"].wait(30), "A's request never reached the post-validation pause")

        expire(engine, job_id)
        _job_b, lease_b = claim_job(_ALT["base"], WORKER_B, create=False, job_id=job_id)
        committed_b = submit(_ALT["base"], job_id, lease_b, BODY_B, WORKER_B)
        a.equal(committed_b.status_code, 200, "B's commit must land")
        envelope_b = committed_b.json()
        after_b = snapshot(engine, job_id)
        a.equal(after_b["status"], "COMPLETED", "B's result is authoritative")

        state["release"].set()
        a.require(pending.join(), "A's request never returned")
        response = pending.outcome.get("value")
        a.require(response is not None, f"A's request failed: {pending.outcome.get('error')}")

        a.equal(response.status_code, 200, "a late candidate on a committed job is a replay")
        a.equal(response.headers.get("Idempotent-Replayed"), "true", "the server labels the replay")
        a.equal(response.json(), envelope_b, "the replay carries B's envelope verbatim")
        after = snapshot(engine, job_id)
        a.equal(after, after_b, "A's arrival changed nothing in the row")
        a.require(MARKER not in (after["result_json"] or ""), "A's marker must never reach the result")
        return {
            "job_id": job_id,
            "a_status": response.status_code,
            "replayed": response.headers.get("Idempotent-Replayed"),
            "gen_b": lease_b["generation"],
        }, a.failures
    finally:
        developer.set_client(S1.HarnessClient())


# ---------------------------------------------------------------------------
# J — Concurrency case B: SIGKILL
# ---------------------------------------------------------------------------

# A store driver: imports the REAL store module, warms its idempotent
# schema-ensure, announces itself, then waits for the parent's GO.
#
# The warming step is ordered BEFORE the barrier on purpose: otherwise the
# driver's first statement is the CREATE/ALTER DDL, which takes an ACCESS
# EXCLUSIVE lock and would block *there* instead of on the commit UPDATE — the
# measurement would then describe the wrong statement. GO is only sent once the
# parent holds the row lock, so the UPDATE is provably the next thing to run.
_DRIVER_PRELUDE = """
import json, os, sys
sys.path.insert(0, {repo!r})
from backend.auth import async_jobs  # real store module, real SQL
async_jobs._session_factory()
print("READY", flush=True)
sys.stdin.readline()  # GO
"""

# An HTTP driver: no store import, so it is ready as soon as it prints.
_CLIENT_PRELUDE = """
import json, os, sys
import requests
print("READY", flush=True)
"""

_DRIVER_COMMIT = """
ok = async_jobs.complete_job(
    {job!r},
    envelope={{"api_status": "VERIFIED", "engine_status": "VERIFIED"}},
    http_status=200,
    lease_owner={owner!r},
    lease_generation={gen!r},
)
print("RESULT %r" % (ok,), flush=True)
"""


def _trace(message: str) -> None:
    if os.environ.get("O6_DEBUG"):
        print(f"    [trace] {message}", flush=True)


def _write_driver(directory: Path, name: str, body: str, *, prelude: str = "") -> Path:
    path = directory / name
    path.write_text(
        (prelude or _DRIVER_PRELUDE).format(repo=str(_REPO_ROOT))
        + textwrap.dedent(body).lstrip("\n"),
        encoding="utf-8",
    )
    return path


def _go(proc: subprocess.Popen) -> None:
    """Release a driver that is waiting at the GO barrier."""
    proc.stdin.write("\n")
    proc.stdin.flush()


def _driver_error(pump) -> str:
    """Whatever the driver printed on stderr so far (never blocks on a live child)."""
    try:
        return pump.drain()[-800:]
    except Exception:  # pragma: no cover
        return ""


class _LinePump:
    """Bounded stdout reading: a driver that never speaks must not hang the suite."""

    def __init__(self, stream) -> None:
        import queue

        self._lines: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        try:
            for line in stream:
                self._lines.put(line)
        except Exception:  # pragma: no cover - stream closed under us
            pass
        finally:
            self._lines.put(None)

    def readline(self, timeout: float):
        import queue

        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return ""

    def drain(self) -> str:
        """Everything received so far, without blocking on a live child."""
        import queue

        out = []
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                break
            out.append(line)
        return "".join(out)


def _spawn(path: Path):
    proc = subprocess.Popen(
        [sys.executable, str(path)],
        cwd=str(_REPO_ROOT),
        env=os.environ.copy(),  # PLATRIXA_METERING_DATABASE_URL points at the disposable store
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc, _LinePump(proc.stdout), _LinePump(proc.stderr)


def _await_line(pump: _LinePump, expected: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = pump.readline(max(0.05, deadline - time.monotonic()))
        if not line:
            return False
        if line.strip() == expected:
            return True
    return False


def _kill_and_reap(proc: subprocess.Popen) -> int:
    proc.kill()  # SIGKILL on POSIX
    try:
        return proc.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - would be a real hang
        proc.kill()
        return proc.wait(timeout=30)


def _cleanup_driver(proc: subprocess.Popen) -> None:
    if proc.poll() is None:  # pragma: no cover - defensive
        proc.kill()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:  # pragma: no cover
            pass


def j1_crash_before_the_commit(api_base, engine, workdir: Path) -> tuple:
    """SIGKILL after the claim, BEFORE the commit statement is issued."""
    fresh(engine)
    a = Assertions()
    _trace("J1: reset done")
    job_id, lease = claim_job(api_base, WORKER_A)
    _trace("J1: claimed")
    before = snapshot(engine, job_id)

    # The driver is killed at the GO barrier: no statement was ever issued.
    body = _DRIVER_COMMIT.format(job=job_id, owner=lease["owner"], gen=lease["generation"])
    driver = _write_driver(workdir, "crash_before_commit.py", body)
    _trace("J1: driver written")
    proc, pump, err_pump = _spawn(driver)
    _trace("J1: spawned")
    try:
        a.require(_await_line(pump, "READY"),
                  f"the driver never signalled readiness: {_driver_error(err_pump)!r}")
        a.equal(_kill_and_reap(proc), -signal.SIGKILL, "the driver must die by SIGKILL")
    finally:
        _cleanup_driver(proc)

    after = snapshot(engine, job_id)
    a.equal(after, before, "a process killed before the statement wrote NOTHING")
    a.equal(after["status"], "PROCESSING", "the job is still leased")
    a.equal(after["result_json"], None, "nothing became authoritative")

    # Recovery: the same generation can still commit once the lease is refreshed.
    refreshed = renew(api_base, job_id, lease, WORKER_A)
    a.equal(refreshed.status_code, 200, "the untouched lease can still be renewed")
    committed = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.equal(committed.status_code, 200, "the job recovers cleanly")
    a.equal(snapshot(engine, job_id)["status"], "COMPLETED", "the recovery commit landed")
    return {"job_id": job_id, "returncode": -signal.SIGKILL, "recovered": committed.status_code}, a.failures


def j2_sigkill_mid_commit_rolls_back(api_base, engine, workdir: Path) -> tuple:
    """SIGKILL while this process's commit UPDATE is blocked inside PostgreSQL.

    The transaction-owning process dies, so PostgreSQL aborts it. This is the
    "the commit request reached the database" outcome — and it ROLLED BACK.
    """
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)
    before = snapshot(engine, job_id)

    body = _DRIVER_COMMIT.format(job=job_id, owner=lease["owner"], gen=lease["generation"])
    driver = _write_driver(workdir, "crash_mid_commit.py", body)
    _trace("J2: spawning the committing process")
    proc, pump, err_pump = _spawn(driver)
    _trace("J2: spawned")
    conn = tx = None
    try:
        a.require(_await_line(pump, "READY"),
                  f"the driver never signalled readiness: {_driver_error(err_pump)!r}")
        _trace("J2: driver ready (schema warmed, waiting at GO)")
        # Take the row lock FIRST, then release the driver: its next statement
        # is the commit UPDATE, and it can only wait on that row.
        _trace("J2: holding the row lock")
        conn, tx = hold_row(engine, job_id)
        _go(proc)
        # The statement is provably blocked INSIDE PostgreSQL's UPDATE.
        blocked = wait_blocked(engine, "UPDATE platrixa_async_jobs SET status = 'COMPLETED'")
        _trace(f"J2: blocked={blocked} stdout={pump.drain()!r} stderr={_driver_error(err_pump)!r} "
               f"alive={proc.poll() is None}")
        a.require(blocked, "the driver's commit never reached the database")
        a.equal(_kill_and_reap(proc), -signal.SIGKILL, "the driver must die by SIGKILL")
        # A backend waiting on a row lock is not reading its socket, so
        # PostgreSQL cannot notice the death yet. The lock is released first,
        # which is what a real dead client's transaction would experience: the
        # backend wakes, discovers the closed connection, and aborts.
        tx.commit()
        a.require(wait_unblocked(engine, "UPDATE platrixa_async_jobs SET status = 'COMPLETED'"),
                  "PostgreSQL never finished with the killed transaction")
    finally:
        _cleanup_driver(proc)
        if tx is not None and tx.is_active:
            tx.rollback()
        if conn is not None:
            conn.close()

    after = snapshot(engine, job_id)
    a.equal(after, before, "the aborted transaction left the row byte-identical")
    a.equal(after["status"], "PROCESSING", "the job is untouched, not half-committed")
    a.equal(after["result_json"], None, "nothing became authoritative")
    a.equal(after["lease_generation"], lease["generation"], "the generation is intact")

    # The SAME generation can still commit: no residue, no mystery lock.
    committed = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.equal(committed.status_code, 200, f"the same generation must still commit ({committed.status_code})")
    stored = stored_result(engine, job_id) or {}
    a.require(stored.get("api_status"), "a real envelope was stored")
    return {
        "job_id": job_id,
        "blocked_then_killed": True,
        "recovered": committed.status_code,
    }, a.failures


def j3_client_death_after_the_server_commits(api_base, engine, workdir: Path) -> tuple:
    """The HTTP client dies while the SERVER is mid-commit: the commit still lands.

    This is the "outcome unknown to the caller" outcome. The retry that follows
    must replay exactly one authoritative result.
    """
    fresh(engine)
    a = Assertions()
    job_id, lease = claim_job(api_base, WORKER_A)

    payload = {
        "lease": {"owner": lease["owner"], "generation": lease["generation"]},
        "bridge_body": BODY_B,
    }
    driver = _write_driver(
        workdir,
        "client_dies.py",
        f"payload = json.loads({json.dumps(payload)!r})\n"
        'print("READY", flush=True)\n'
        f"r = requests.post({api_base + '/v1/worker/jobs/' + job_id + '/result'!r}, "
        f"json=payload, headers={_headers(WORKER_A)!r}, timeout=120)\n"
        'print("STATUS %s" % (r.status_code,), flush=True)\n',
        prelude=_CLIENT_PRELUDE,
    )

    _trace("J3: holding the row lock")
    conn, tx = hold_row(engine, job_id)
    _trace("J3: spawning the HTTP client")
    proc, pump, err_pump = _spawn(driver)
    _trace("J3: spawned")
    try:
        a.require(_await_line(pump, "READY"),
                  f"the client never signalled readiness: {_driver_error(err_pump)!r}")
        blocked = wait_blocked(engine, "UPDATE platrixa_async_jobs SET status = 'COMPLETED'")
        a.require(blocked, "the server's commit never reached the database")
        a.equal(_kill_and_reap(proc), -signal.SIGKILL, "the client must die by SIGKILL")
        # The client is gone; the server's transaction is still in flight.
        tx.commit()
    finally:
        _cleanup_driver(proc)
        if tx.is_active:
            tx.rollback()
        conn.close()

    # The commit that the client never learned about must land exactly once.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and snapshot(engine, job_id)["status"] != "COMPLETED":
        time.sleep(0.02)
    after = snapshot(engine, job_id)
    a.equal(after["status"], "COMPLETED", "the server committed despite the client's death")
    a.equal(after["lease_generation"], lease["generation"], "the generation is unchanged")
    first_bytes = after["result_json"]
    a.require(first_bytes is not None, "exactly one authoritative result exists")

    # A retry at the same generation replays it; nothing is duplicated.
    retry = submit(api_base, job_id, lease, BODY_B, WORKER_A)
    a.equal(retry.status_code, 200, "a retry after an unknown outcome is answered")
    a.equal(retry.headers.get("Idempotent-Replayed"), "true", "the retry is a replay, not a second commit")
    a.equal(snapshot(engine, job_id)["result_json"], first_bytes, "the authoritative bytes are unchanged")
    return {
        "job_id": job_id,
        "client_killed": True,
        "committed": after["status"],
        "retry": retry.status_code,
        "replayed": retry.headers.get("Idempotent-Replayed"),
    }, a.failures


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

SECTIONS = [
    ("G1 valid owner fails a current unexpired job", g1_valid_failure, True),
    ("G2 expired owner fails its own unsuperseded job", g2_expired_failure, True),
    ("G3 stale owner cannot fail a newer generation", g3_stale_failure_after_reclaim, True),
    ("G4 repeated failure is a no-op", g4_repeated_failure, True),
    ("G5 failure blocked on the row, overtaken by a reclaim", g5_failure_overtaken_by_rank, True),
    ("G6 failure racing a real claim", g6_failure_racing_a_real_claim, True),
    ("H1 valid owner releases a current job", h1_valid_release, True),
    ("H2 expired owner releases its own job", h2_expired_release, True),
    ("H3 stale release cannot touch a newer lease", h3_stale_release_cannot_touch_a_newer_lease, True),
    ("H4 repeated release is a no-op", h4_repeated_release, True),
    ("H5 release cannot touch a COMPLETED job", h5_release_cannot_touch_a_completed_job, True),
    ("H6 release blocked on the row, overtaken by a reclaim", h6_release_overtaken_by_a_reclaim, True),
    ("H7 counterfactual: the pre-O6 unfenced release clobbers a newer lease",
     h7_counterfactual_unfenced_release, True),
    ("I1 reclaim between kernel validation and the UPDATE", i1_reclaim_between_kernel_and_update, True),
    ("I2 reclaim+commit while the candidate is paused", i2_reclaim_and_commit_while_a_is_paused, True),
]

CRASH_SECTIONS = [
    ("J1 SIGKILL before the commit statement", j1_crash_before_the_commit),
    ("J2 SIGKILL while the commit UPDATE is blocked (PostgreSQL rolls back)", j2_sigkill_mid_commit_rolls_back),
    ("J3 client death while the SERVER commits (retry replays once)", j3_client_death_after_the_server_commits),
]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    iterations = int(argv[0]) if argv else 8

    print("=" * 78)
    print("Stage 2 O6 — failure/release transitions, reclaim interleaving, SIGKILL")
    print("=" * 78)
    print("isolation guardrails: OK (loopback only, no PLATRIXA_* origin, no real credential)")

    previous_lease = os.environ.get(LEASE_ENV)
    os.environ[LEASE_ENV] = LEASE_SECONDS
    srv, engine = S1.pg_backend()
    developer.set_client(S1.HarnessClient())
    app = create_app()
    api_server, api_thread, api_base = S2.start_api(app)
    api2_server, api2_thread, api_alt = S2.start_api(app)
    _ALT["base"] = api_alt
    print(f"real API on {api_base} (uvicorn, loopback)")
    print(f"second real API instance on {api_alt} (own event loop, for §I)")
    print(f"real PostgreSQL store (disposable embedded cluster) | iterations={iterations}")
    check("the O6 API origin is loopback", S2._is_loopback(api_base), api_base)
    check("the second API origin is a distinct loopback origin",
          S2._is_loopback(api_alt) and api_alt != api_base, api_alt)

    workdir = Path(tempfile.mkdtemp(prefix="platrixa-o6-drivers-"))
    evidence: dict = {}
    try:
        for label, fn, _ in SECTIONS:
            scenario(f"{label} — {iterations} iterations, fresh state each time")
            runs = []
            for index in range(iterations):
                payload, failures = fn(api_base, engine)
                runs.append({"index": index, "payload": payload, "failures": failures})
            bad = [(r["index"], r["failures"]) for r in runs if r["failures"]]
            check(f"{label}: all {len(runs)} iteration(s) satisfied every invariant",
                  not bad, f"{len(bad)} failed: {bad[:2]}")
            evidence[label] = runs

        scenario(f"SIGKILL scenarios — {iterations} iterations each, fresh state and a fresh driver process")
        for label, fn in CRASH_SECTIONS:
            runs = []
            for index in range(iterations):
                payload, failures = fn(api_base, engine, workdir)
                runs.append({"index": index, "payload": payload, "failures": failures})
            bad = [(r["index"], r["failures"]) for r in runs if r["failures"]]
            check(f"{label}: all {len(runs)} iteration(s) satisfied every invariant",
                  not bad, f"{len(bad)} failed: {bad[:2]}")
            evidence[label] = runs

        # ---- the observable contract, one line per conclusion ---------------
        scenario("Aggregate evidence")
        seed = {label: runs[0]["payload"] for label, runs in evidence.items()}
        check("G1: a current owner's retryable failure defers to RETRY_WAIT with the error stored",
              seed["G1 valid owner fails a current unexpired job"]["status"] == "RETRY_WAIT",
              json.dumps(seed["G1 valid owner fails a current unexpired job"]))
        check("G2: an expired owner's own failure is recorded and the job stays recoverable",
              seed["G2 expired owner fails its own unsuperseded job"]["status"] == "RETRY_WAIT",
              json.dumps(seed["G2 expired owner fails its own unsuperseded job"]))
        check("G3: a superseded owner's failure is refused and B's lease/commit survive",
              seed["G3 stale owner cannot fail a newer generation"]["refused"] is True,
              json.dumps(seed["G3 stale owner cannot fail a newer generation"]))
        check("G4: the second failure call is refused with no state change",
              seed["G4 repeated failure is a no-op"]["first"] is True
              and seed["G4 repeated failure is a no-op"]["second"] is False,
              json.dumps(seed["G4 repeated failure is a no-op"]))
        check("G5: a failure blocked on the row loses to a committed reclaim",
              seed["G5 failure blocked on the row, overtaken by a reclaim"]["refused"] is True,
              json.dumps(seed["G5 failure blocked on the row, overtaken by a reclaim"]))
        orders = sorted({r["payload"]["order"] for r in evidence["G6 failure racing a real claim"]})
        check("G6: every real fail-vs-claim race produced one of the two valid serializations",
              len(orders) in (1, 2) and all(
                  r["payload"]["failed"] in (True, False) for r in evidence["G6 failure racing a real claim"]
              ),
              f"orders observed: {orders}")
        check("H1: a release re-queues at the same generation and the next claim takes G+1",
              seed["H1 valid owner releases a current job"]["status"] == "QUEUED",
              json.dumps(seed["H1 valid owner releases a current job"]))
        check("H2: an expired owner may release its own job (never authoritative)",
              seed["H2 expired owner releases its own job"]["released"] is True,
              json.dumps(seed["H2 expired owner releases its own job"]))
        check("H3: a stale release is refused and the newer worker's commit still lands",
              seed["H3 stale release cannot touch a newer lease"]["released"] is False,
              json.dumps(seed["H3 stale release cannot touch a newer lease"]))
        check("H5: a COMPLETED job can never be re-queued",
              seed["H5 release cannot touch a COMPLETED job"]["released"] is False,
              json.dumps(seed["H5 release cannot touch a COMPLETED job"]))
        check("H7 (counterfactual): the pre-O6 statement re-queued a job B owned and blocked B's commit",
              seed["H7 counterfactual: the pre-O6 unfenced release clobbers a newer lease"]["b_commit_after"] == 409,
              json.dumps(seed["H7 counterfactual: the pre-O6 unfenced release clobbers a newer lease"]))
        check("I1: a candidate validated BEFORE the reclaim is refused and cannot renew/release",
              seed["I1 reclaim between kernel validation and the UPDATE"]["a_status"] == 409,
              json.dumps(seed["I1 reclaim between kernel validation and the UPDATE"]))
        check("I2: the same ordering with B committed is a labelled replay of B's envelope",
              seed["I2 reclaim+commit while the candidate is paused"]["replayed"] == "true",
              json.dumps(seed["I2 reclaim+commit while the candidate is paused"]))
        check("J1: killed before the statement → nothing written, job recovers",
              seed["J1 SIGKILL before the commit statement"]["returncode"] == -signal.SIGKILL,
              json.dumps(seed["J1 SIGKILL before the commit statement"]))
        check("J2: killed mid-commit → PostgreSQL rolled back and the same generation still commits",
              seed["J2 SIGKILL while the commit UPDATE is blocked (PostgreSQL rolls back)"]["recovered"] == 200,
              json.dumps(seed["J2 SIGKILL while the commit UPDATE is blocked (PostgreSQL rolls back)"]))
        check("J3: client death while the server commits → one result, and the retry replays it",
              seed["J3 client death while the SERVER commits (retry replays once)"]["committed"] == "COMPLETED"
              and seed["J3 client death while the SERVER commits (retry replays once)"]["replayed"] == "true",
              json.dumps(seed["J3 client death while the SERVER commits (retry replays once)"]))
    finally:
        developer.reset_client()
        api_server.should_exit = True
        api2_server.should_exit = True
        api_thread.join(timeout=5)
        api2_thread.join(timeout=5)
        if previous_lease is None:
            os.environ.pop(LEASE_ENV, None)
        else:
            os.environ[LEASE_ENV] = previous_lease
        shutil.rmtree(str(workdir), ignore_errors=True)
        try:
            srv.cleanup()
        except Exception as exc:  # pragma: no cover
            print(f"note: pgserver cleanup raised {type(exc).__name__}: {exc}")
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
    infrastructure = {"o6-api-server", "audit-api-server", "audit-mock-bridge",
                      "platrixa-request-log"}  # both app instances use the S2 name
    strays = [t.name for t in threading.enumerate()
              if t.is_alive() and t is not threading.main_thread()
              and t.name not in infrastructure
              and not t.name.startswith(("Thread-", "AnyIO", "asyncio"))]
    print(f"worker heartbeat threads still alive after teardown: {leftovers}")
    print(f"stray non-infrastructure threads after teardown: {sorted(set(strays))}")
    print(f"disposable PostgreSQL directory present: {_PG_DATA.exists()}")
    print(f"driver scratch directory present: {workdir.exists()}")
    ok = (passed == total and not leftovers and not strays
          and not _PG_DATA.exists() and not workdir.exists())
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(2)
