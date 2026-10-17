# Stage 2 — Lease Safety Audit, Isolated Integration and Device Benchmark

**Subject:** `scripts/platrixa_outbound_inference_worker.py` and the server-side
lease/fence implementation it depends on.
**Audited revision:** `570330b` on `platrixa/stage2-outbound-worker`
(base `021a77d`; commit `578a47d` = worker + suite, `570330b` = bounded-polling test).
**Audit branch:** `platrixa/stage2-lease-audit`, created at `570330b`.
**Audit-race branch:** `platrixa/stage2-lease-audit-race`, created at `570330b` (the
live two-worker race and the shutdown-assertion audit, §7.1/§7.2).
**O1/O2/O6 branch:** `platrixa/stage2-o1-lease-expiry`, created at `570330b` from the
same tip (the O1 server fix §3 F3 + suite §7.3, then the O2 worker reporting change
§6, the O6 release fence §3 F4, and the O6/crash suite §7.4). Still uncommitted.
**Status of this document:** uncommitted working notes on the audit branches. No push,
no deploy, no commit, no Railway change, no production credential used.

Evidence classes used throughout: **SRC** (source inspection), **UNIT** (worker
unit suite, in-process fakes), **INT** (integration: real worker over a real
localhost socket against the real FastAPI app, real PostgreSQL and the real
server SQL), **OBS** (a raw observation quoted verbatim), **PENDING** (not
measured, with the reason).

---

## 1. Traced control flow (SRC)

```
client → POST /v1/inference-jobs                        api/routes/inference_api.py:186
  admission → idempotency claim → reservation → durable job (kind='inference')

worker.claim()                                          worker:753
  POST  {api_base}/v1/worker/jobs/claim                 headers: X-Platrixa-API-Key,
  ← 200 NO_WORK | 200 CLAIMED {job_id, request_id, raw_input, lease{owner,          X-Platrixa-Worker-Id
    generation, lease_seconds, renew_every_seconds, renew_url, result_url}}
  server: claim_inference_job                           inference_api.py:459
    async_jobs.claim_next_job(kind='inference', tenant_id=…)   async_jobs.py:1096
      ONE UPDATE … WHERE job_id IN (SELECT … ORDER BY created_at LIMIT 1
                                    FOR UPDATE SKIP LOCKED)     async_jobs.py:1169-1180
        lease_generation = j.lease_generation + 1      async_jobs.py:1171   ← fencing token
  client: _build_claimed_job validates id / input / owner / generation / timings /
          both URLs                                    worker:795-844 (resolve_lease_endpoint:279)

worker.start_heartbeat(state)                           worker:887
  LeaseHeartbeat(tick=heartbeat_tick_s=0.25s, per-job stop_event,
                 worker-wide shutdown_event)            worker:546-612
    POST {renew_url} {lease:{owner,generation}}         worker:844
    ← 200 {renewed:true, lease:{owner,generation,…}}   → state.note_renewed (deadline
                                                         extended, ONLY on confirmation)
    ← 409 LEASE_LOST          → state.mark_lost (immediate)
    ← 200 with a foreign proof→ state.mark_lost (fail closed)
    ← transport failure       → state.note_renewal_failure → lost once the
                                CONFIRMED deadline passes

worker.call_bridge(raw_input)                           worker:920
  POST {bridge_url}/interpret {"text": raw_input}       then "/" only on 404/405;
  ← complete body, verbatim (bounded, object-only)      connection error short-circuits

worker.submit_result(job, body)                         worker:990
  state.ensure_held()  (advisory local check)           worker:499
  POST {result_url} {lease:{owner,generation}, bridge_body:{…complete body…}}
  server: submit_inference_result                       inference_api.py:686
    transport-shape checks → lease proof (_lease_proof, inference_api.py:159)
    → tenant+kind scoped get_job → duplicate/completed → replay path
    → status=='PROCESSING' and owner/generation match   inference_api.py:786-791
       (owner/generation only — expiry is judged in SQL, not in Python)
    → Kernel.process_from_source(_interpret)  ← the SHARED deterministic pipeline
    → complete_job(…, lease_owner, lease_generation)    async_jobs.py:1196
       UPDATE … SET status='COMPLETED', result_json=…,
                  lease_expires_at=NULL, lease_owner=NULL
       WHERE job_id=:j AND status='PROCESSING'
         AND lease_owner=:owner AND lease_generation=:gen
         AND lease_expires_at IS NOT NULL
         AND lease_expires_at > clock_timestamp()      async_jobs.py:1268-1271
       rowcount == 1 → accepted, else refused (returns False)
    ← 200 authoritative envelope (or 422 engine terminal state, or
       409 LEASE_LOST / 409 LEASE_EXPIRED)

worker.process_job(job)                                 worker:1041
  ensure_held → start_heartbeat → call_bridge → ensure_held → submit_result
  finally: stop_heartbeat (per-job signal; worker-wide shutdown untouched)
```

Distinction that matters everywhere below: a candidate is **computed locally**
whenever `call_bridge` returns; it is **accepted** only when the server returns
200 with its own envelope. The worker never equates the two.

---

## 2. Invariant register

| # | Invariant | Source evidence | Verdict |
|---|---|---|---|
| A1 | Lease validity is checked immediately before inference and again before submission | `process_job` `ensure_held` at worker:1049 (pre-inference) and `submit_result` worker:1002; `ensure_held` worker:499 | **PASS** — and it CANNOT prove the server still holds it; see C1 |
| A2 | A claimed job is validated before it is trusted | `_build_claimed_job` worker:795-844: job id, non-empty `raw_input`, owner bounded, generation ≥0 int, positive finite timings, `renew ≤ lease`, both URLs exact | **PASS** |
| A3 | The lease check uses a trustworthy local state | `LeaseState` deadline moves only on a CONFIRMED renewal (worker:512); failures never extend it (worker:522) | **PASS** |
| B1 | Heartbeat start/stop is per job and does not stop the worker | `start_heartbeat` worker:887 / `stop_heartbeat` worker:905 with a per-job `stop_event`; `stop()` worker:1126 is the only writer of the shutdown signal | **PASS after F1** (was FAIL) |
| B2 | Renewal cadence uses the server's own hint | `renew_every_seconds` from the claim (inference_api.py:536) drives `next_renew_at` (worker:512/522); tick 0.25 s polls it | **PASS** |
| B3 | Renewal requests are timeout-bounded | `request_timeout_s` (default 30 s) threaded through `_api_post` worker:742 | **PASS** |
| B4 | 409 on renewal is immediate lease loss | `renew_lease` worker:844 → `mark_lost` in `LeaseHeartbeat._tick_once` worker:607 | **PASS** (INT: real 409 `LEASE_LOST`) |
| B5 | Malformed / foreign renewal proof fails closed | worker:880-885 compares owner+generation, else raises `MalformedResponseError` → `mark_lost` worker:608 | **PASS** |
| B6 | Transient renewal failures are tolerated until the confirmed deadline | `note_renewal_failure` worker:522; UNIT/CLOCK + INT transient-then-success | **PASS** (window derives from the LOCAL clock — see §6 O3) |
| B7 | Heartbeat and main thread share state safely | `LeaseState` mutations under `self._lock` (worker:440); `lost_event` for cross-thread waits | **PASS** |
| B8 | A lost lease stops the COMMIT (in-flight inference is not cancelled) | `submit_result` `ensure_held` → `LeaseLost`; the bridge call itself is not interruptible | **PASS for correctness**; documented behaviour: a lost lease discards the candidate at submission time rather than aborting the model run |
| C1 | The check-to-submit race cannot be closed client-side | The local check is explicitly advisory (worker:499 docstring); the 409 from the fenced UPDATE is authority | **PASS** (no false claim of atomicity anywhere in the code or docs) |
| D1 | An obsolete generation cannot commit | `complete_job` worker-relevant SQL `WHERE status='PROCESSING' AND lease_owner=:owner AND lease_generation=:gen`, single conditional UPDATE, `rowcount==1` (async_jobs.py:1237-1252) | **PASS** (INT: 409 `LEASE_LOST`; job unchanged) |
| D2 | Duplicate submissions cannot double-commit | Same `status='PROCESSING'` guard + the replay branch (inference_api.py:757-763, 900-912) | **PASS** (INT: one commit, second replayed) |
| D3 | A stale worker cannot even renew | `renew_lease` SQL carries the same owner+generation predicate AND the liveness predicate (async_jobs.py:1438-1443) | **PASS** |
| D4 | Two concurrent claims cannot take one job | `FOR UPDATE SKIP LOCKED` inside the claim's UPDATE (async_jobs.py:1178) | **PASS** (SRC; also Stage 1 suite D20 with a real barrier) |
| D5 | Expiry alone revokes commit authority, on the SERVER's clock, atomically with the write | `AND lease_expires_at IS NOT NULL AND lease_expires_at > clock_timestamp()` in the SAME conditional UPDATE as the fence (async_jobs.py:1270-1271), and the same liveness predicate on renewal (async_jobs.py:1442-1443) | **PASS after F3** (was BOUNDARY/O1; INT 200 iterations, §7.3) |
| E1 | Shutdown signals the heartbeat and joins it boundedly | `request_stop` worker:584 + `join(shutdown_timeout_s)` worker:916 | **PASS** (INT: a renewal blocked for 10 s did not delay shutdown) |
| E2 | Shutdown does not invoke a shadowed `threading.Thread` internal | no `self._stop` assignment exists (grep-verified); the class carries an explicit warning comment | **PASS** (this exact bug was found and fixed during Stage 2 development) |
| E3 | No uncontrolled background threads | INT: after 101 checks `threads still alive after teardown: []`; heartbeat is `daemon=True` and always joined | **PASS** |
| E4 | Nothing is submitted after loss is observed | INT: lost-before-inference, lost-by-deadline, superseded-in-race, in-flight 409 | **PASS** |
| D6 | A stale worker cannot re-queue a job a NEWER worker owns | `release_job` predicate `status='PROCESSING' AND lease_owner=:owner AND lease_generation=:gen` (async_jobs.py), route delegates (async_api.py `_release_lease`) | **PASS after F4** (was FAIL; INT 8 iterations, incl. the H7 counterfactual) |
| D7 | A failure write can never publish a result nor reach a newer generation | `fail_job` writes an error envelope + clears the lease, generation-fenced, never `result_json` | **PASS** (INT G1-G6; expiry deliberately not required — §6 O6) |
| D8 | A replayed result is never reported as a fresh acceptance | `SubmitOutcome.replayed` from the server's `Idempotent-Replayed` marker; unknown values refused | **PASS after O2** (UNIT 23-27) |
| D9 | A commit interrupted by process death leaves exactly one of two clean outcomes | J2 rollback / J3 committed-and-replayable, both measured | **PASS** (INT J1-J3, 8 iterations each) |

---

## 3. Verified defects (fixed) and their regression tests

### F1 — per-job heartbeat teardown stopped the whole worker (HIGH, liveness)

*Root cause (SRC).* `stop_heartbeat()` set the worker-wide `self._stop_event`, and
`run_forever()` loops on that same event (worker:1057). `process_job()` calls
`stop_heartbeat()` in its `finally` on every job, so the loop exited after the
**first** job it processed — success or rejection — and every later job stayed
`QUEUED` forever.

*Before evidence.* `test_poll_loop_serves_more_than_one_job`:
`AssertionError: expected two served jobs, got 1`. Integration S8: after the
first (rejected) job, `run_forever(max_polls=3)` returned with `completed=0` and
the second job still `{"status": "QUEUED", "attempt_count": 0}`.

*Fix.* The heartbeat now has TWO signals: a per-job `stop_event` (set by
`stop_heartbeat`) and the worker-wide shutdown event (set only by `stop()`), and
it exits when either is set (worker:546-612, 887-918).

*Regression tests.* `test_poll_loop_serves_more_than_one_job`,
`test_terminal_rejection_does_not_kill_the_poll_loop` (asserts
`worker.stop_event.is_set() is False` after jobs),
`test_job_lifecycle_is_distinct_from_worker_shutdown` (UNIT: a normal job AND a
fenced-off job both leave the worker running; `stop()` is what ends it — see
§7.2), integration S8, and the race suite's S3 (two real jobs in one
`run_forever`, then `stop()`).

### F2 — a deterministic rejection escaped the poll loop as a fatal error (MEDIUM, liveness)

*Root cause (SRC).* For a candidate the deterministic pipeline refuses, the
result route stores the engine's terminal envelope, sets the job `COMPLETED` and
answers **HTTP 422** (`retryable: false`). The worker mapped 422 to the generic
`ApiError(422, "UNEXPECTED_STATUS", …, retryable=False)` and `run_forever`
re-raised every non-retryable `ApiError`, so one refused candidate tore the
device worker down.

*Observed (INT, quoted).*
`observed: worker outcome=ApiError | job=COMPLETED | http_status=422 | api_status=FAILED`

*Fix.* (a) Submission-stage `ApiError` is logged and the loop continues — the
server has already decided that job's fate, and only the CLAIM stage treats a
non-retryable `ApiError` as fatal (a bad credential must not spin). (b) 422 is
reported with the engine's own code via `rejection_code_of` (worker:356) instead
of `UNEXPECTED_STATUS`.

*Regression tests.* `test_terminal_rejection_does_not_kill_the_poll_loop`
(also asserts the direct caller still sees `ApiError(422, code="VALIDATION_FAILED",
retryable=False)`, and that a 401 on the claim path IS still fatal), integration S8.

**Both fixes are uncommitted** (`git diff --numstat` at `570330b`:
`scripts/platrixa_outbound_inference_worker.py` `+78/-18`;
`scripts/platrixa_outbound_inference_worker_test.py` `+169/-1`, and the single
removed line is exactly the defect-encoding assertion quoted in §7.2).

### F3 — an expired-but-unsuperseded lease still authorized a commit (HIGH, integrity of the freshness rule; formerly O1)

*Root cause (SRC).* The commit fence was `status='PROCESSING' AND
lease_owner=:owner AND lease_generation=:gen` only (async_jobs.py, pre-fix lines
1237-1242). `lease_expires_at` was consulted **only by the claim**
(async_jobs.py:1151: `lease_expires_at IS NULL OR lease_expires_at < now()`), i.e.
expiry made a job *claimable again* but revoked nothing. Failure sequence: worker
A claims at generation G → A's lease lapses (paused process, frozen device,
partition) → **no other worker claims** → A submits at G → the predicate still
matches → A commits. Nothing bounded how late that commit could be. Worse, the
same worker could first call `POST …/lease`, whose SQL had no liveness predicate
either, and silently **resurrect** the expired lease and then commit inside the
new window.

*Observed before the fix (INT, real server):* `renew → 200` and `submit → 200`
with `api_status=VERIFIED` after `lease_expires_at` had been moved one hour into
the past. Those two observations were previously *asserted as the contract* in
this repository's own tests — see §7.3 for the two assertions that had to be
corrected, not weakened.

*Fix (smallest correct change, two predicates + one diagnostic).*

1. `complete_job` now requires, in the SAME conditional UPDATE, `status='PROCESSING'`,
   owner match, generation match, `lease_expires_at IS NOT NULL`, and
   `lease_expires_at > clock_timestamp()` (async_jobs.py:1268-1271).
2. `renew_lease` requires the same liveness predicate (async_jobs.py:1438-1443),
   so expiry is irreversible without a fresh claim at a new generation.
3. `lease_commit_refusal_reason` (async_jobs.py:1291-1333) is a read-only,
   DB-clock diagnostic that names `EXPIRED` vs `STALE` **after** the commit has
   already refused, so the API can answer `409 LEASE_EXPIRED` instead of
   collapsing every refusal into `LEASE_LOST`. It never decides anything.

No schema change, no migration, no new table, no change to the claim or to the
poll loop. Route pre-checks were left owner/generation-only on purpose: a second,
Python-side clock would be a non-authoritative duplicate of the decision (§5).

*Regression tests.* `scripts/platrixa_stage2_o1_lease_expiry_test.py` cases A–F
(§7.3, 200 iterations), plus the corrected assertions in the Stage 2 suite
(S2 2b, S4 c). No worker change was needed: `submit_result` already raises
`LeaseLost(error_code_of(response))`, so the worker now surfaces `LEASE_EXPIRED`
verbatim while treating it exactly like `LEASE_LOST` (discard, never retry at the
same generation).

---

### F4 — the document release path re-queued a job with no ownership proof (MEDIUM, wasted work; formerly O6)

*Root cause (SRC).* `_release_lease` (api/routes/async_api.py, pre-fix lines
802-817) ran

```sql
UPDATE platrixa_async_jobs SET status = 'QUEUED', lease_expires_at = NULL,
       updated_at = now() WHERE job_id = :j
```

Targeted by `job_id` alone: **no owner, no generation, no status, no expiry**. It
is called from `_process_job_inner` when `_load_job_request` raises
`AsyncStoreUnavailableError` (async_api.py:570-573). Failure sequence: worker A
claims at G → A hits a store hiccup while reading its payload → the store recovers
and **B claims the job at G+1** → A's stale record now releases the row → B's row
is set to `QUEUED` while B is still processing it → B's fenced `complete_job` no
longer matches (`status='PROCESSING'` fails) → **B's finished work is discarded**
and the job is processed a third time.

*What it cannot do:* publish a result. Measured counterfactual (§7.4 H7): after the
pre-O6 statement re-queued B's job, B's submission answered **409** and
`result_json` stayed `NULL`. The hazard is wasted work and an unreliable lease
record, not a wrong number.

*Fix (smallest correct change).* A new ownership-fenced store function,
`async_jobs.release_job(job_id, *, lease_owner, lease_generation)` (async_jobs.py),
whose predicate is `status='PROCESSING' AND lease_owner=:owner AND
lease_generation=:gen`; the route helper delegates to it and logs a refusal. No
new column, no migration, no route contract change. Expiry is **deliberately not**
part of this predicate — see §6 O6 for the four reasons, measured in §7.4 H2/H3.

*Regression tests.* `scripts/platrixa_stage2_o6_state_and_crash_test.py` H1-H7
(§7.4), plus G3/H3 which prove the newer worker's commit still lands after the
stale call is refused.

---

## 4. Failure interleavings

**I1 — late commit after expiry (CLOSED by F3).**
A claims (gen 1) → A's lease expires server-side → **no other worker claims** →
A submits at gen 1 → the commit predicate now sees `lease_expires_at <=
clock_timestamp()` → **0 rows** → `409 LEASE_EXPIRED`, nothing stored, job stays
`PROCESSING` at gen 1 and is reclaimable at gen 2 (INT: §7.3 case A, 20/20).
Before F3 this same sequence committed (renew 200, submit 200), which is exactly
what made expiry a claimability signal rather than a revocation.

**I2 — superseded worker (FENCED).** A expires → B claims (gen 2) → A renews →
**409 `LEASE_LOST`**; A submits → **409 `LEASE_LOST`**; the job stays
`PROCESSING` with `result_json IS NULL`, and B's submission then commits. (INT)

**I3 — late submission after the job is already committed (NO OVERWRITE).**
B claims and commits → A submits late → **200 with `Idempotent-Replayed: true`
and B's stored envelope**; the row is byte-identical to what B wrote. A cannot
overwrite, but A *is* told 200 — see O2. (INT)

**I4 — in-flight 409 (UNIT + INT).** A result request answered 409 mid-flight
becomes `LeaseLost` and marks the lease lost; the candidate is not retried. The
real-server ordering is now observed directly (§7.4 I1): a request paused after
kernel validation and resumed after a reclaim at G+1 is refused with
`409 LEASE_LOST`, and cannot renew or release afterwards.

**I5 — duplicate submissions at one generation (SAFE).** Two submissions with
the same valid proof: the first wins, the second replays. Rowcount gating makes
double-commit impossible. (INT)

**I6 — concurrent claims (SAFE).** `FOR UPDATE SKIP LOCKED` means at most one
worker takes a given job per statement, and each claim bumps the generation, so
two workers can never hold the "same" generation. (SRC + Stage 1 D20)

**I7 — crash mid-inference (SAFE, not exactly-once).** The job becomes
claimable once its lease expires; a replacement worker *re-infers* at a newer
generation and only its commit can land. Re-inference is possible; double
**commit** is not. This is the Stage 1 limitation, unchanged.

**I8 — stale release racing a reclaim (CLOSED by F4).** A's stale record releases
while B owns G+1 → `False`, row byte-identical, B's commit still **200**. The
counterfactual (the pre-F4 statement) re-queued B's job and B's commit became
**409** with no result — the measured hazard. (INT §7.4 H3/H6/H7)

**I9 — SIGKILL at the commit boundary (MEASURED).** Killed before the statement →
nothing written; killed while the UPDATE waits inside PostgreSQL → rollback, row
byte-identical, same generation still commits; client killed while the server
commits → the commit lands and a retry replays one identical result. (INT §7.4 J1-J3)

---

## 5. Server generation fence — transactional evidence

* The fencing token is bumped **by the claim itself**: `lease_generation =
  j.lease_generation + 1` inside the claim's `UPDATE` (async_jobs.py:1171), in the
  same statement that selects the row `FOR UPDATE SKIP LOCKED` (async_jobs.py:1178).
  One statement, so two workers cannot both claim one job.
* The commit is **one conditional UPDATE** whose predicate includes ownership:
  `WHERE job_id=:j AND status='PROCESSING' AND lease_owner=:owner AND
  lease_generation=:gen` (async_jobs.py:1237-1242), accepted only when
  `rowcount == 1` (async_jobs.py:1244). PostgreSQL evaluates the predicate and
  applies the write atomically, so a stale writer's write affects 0 rows.
* `complete_job` **refuses** to write without a proof: `if not lease_owner or
  lease_generation is None: return False` (async_jobs.py:1215). There is no
  unfenced path.
* `renew_lease` and `fail_job` carry the same ownership predicate, so a fenced
  worker can neither extend a lease nor fail a job another worker now owns;
  `release_job` (F4) carries it too, so a stale release cannot re-queue a newer
  worker's lease. Only `complete_job` and `renew_lease` add the liveness
  predicate, because only they assert authority over a result (§6 O6).
* **Ownership fencing now covers every write that targets a leased job:**
  `complete_job`, `renew_lease`, `fail_job`, `release_job`. The only statement
  that touches a job without presenting a proof is the claim, which is the
  statement that *issues* ownership.
* The route's earlier `record.status`/owner/generation comparison
  (inference_api.py:786-791) is a fast-path rejection, **not** the guarantee; the
  guarantee is the SQL predicate, which is why the route still checks the commit
  result and maps a refusal from `complete_job` at inference_api.py:919-958.
* **Freshness is part of the predicate (F3).** `complete_job` also requires
  `lease_expires_at IS NOT NULL AND lease_expires_at > clock_timestamp()`
  (async_jobs.py:1270-1271), and `renew_lease` requires the same
  (async_jobs.py:1442-1443). So "expired" and "no longer owner" can no longer
  diverge: both produce rowcount 0.

### 5.1 Serialisation and clock reasoning (why this is atomic, and where it is not)

* **One statement, one predicate.** The commit is a single `UPDATE … WHERE
  <five conditions> RETURNING`-less statement whose `rowcount` decides the
  outcome. There is no read-then-write window: the route's earlier `get_job` read
  is advisory (it can only reject early, never accept).
* **Concurrent renewal / reclaim / expiry.** PostgreSQL's `UPDATE` takes a row
  lock. If the row was changed and committed by a concurrent writer while the
  commit statement waits, the executor re-evaluates the WHERE clause against the
  **newly committed row version** (EvalPlanQual, READ COMMITTED). Therefore:
  - a renewal that commits first is seen as a later `lease_expires_at` → the
    commit may proceed only inside the extended window;
  - an expiry or a reclaim that commits first is seen as a stale generation or a
    past deadline → 0 rows, no write.
  The last committed state wins; an in-memory copy never decides. This was
  measured, not assumed: §7.3 cases C1, D1 and E3 hold a real `SELECT … FOR
  UPDATE` in a control connection, wait until `pg_stat_activity` reports the
  competing statement **blocked on the lock**, then change the row and commit.
* **Clock source.** `clock_timestamp()` (statement-evaluation time), not `now()`
  (transaction-start time), so a statement that waited on a lock does not judge
  freshness with a timestamp taken before the wait. Liveness is never judged from
  a client-supplied timestamp and never from the API host's clock.
* **Renewal cadence headroom.** Renewal happens at `lease/3`
  (`LEASE_RENEW_FRACTION`) from both the device worker
  (`renew_every_seconds`) and the in-process document worker
  (`_LeaseRenewal`), so two consecutive renewal failures are absorbed before
  expiry. F3 does not shorten that headroom; it removes the ability to *extend
  an already-expired* lease.
* **Deadlock risk.** The commit and renewal each take exactly ONE row lock (the
  job row) and touch no other table, so no lock-ordering cycle exists. The claim
  takes one row lock via `FOR UPDATE SKIP LOCKED`, which cannot block behind the
  commit (it skips a locked row instead — measured in §7.3 case D1).
* **Not covered here:** an expiry that commits in the same instant as the commit
  statement is decided by whichever row version the executor sees; both outcomes
  are individually valid (`accepted` or `LEASE_EXPIRED`), and the test asserts
  only the invariant, never a particular winner (§7.3 case C1).

---

## 6. Observations (not defects; decisions worth recording)

* **O1 — RESOLVED, now defect F3.** Expiry was a claimability signal, not a
  revocation: `complete_job`/`renew_lease` had no `lease_expires_at` predicate, so
  a lapsed-but-unsuperseded lease could renew itself back to life and commit.
  F3 adds the liveness predicate to both statements (§3, §5.1). The accepted cost
  is that a worker which genuinely misses its window loses the job to a
  re-inference at the next generation instead of committing late.* **O6 — RESOLVED as F4 (release fence), and `fail_job` deliberately stays
  ownership-fenced only.** `_release_lease` had no proof at all and now goes
  through `async_jobs.release_job` (§3 F4, §7.4 H1-H7). `fail_job` keeps the
  pre-existing owner+generation+`PROCESSING` predicate and gains **no** expiry
  predicate, for four measured reasons rather than symmetry:

  | Question | Answer (§7.4) |
  |---|---|
  | Can it make a result authoritative? | No — it writes an error envelope and clears the lease; `result_json` is never set, and a `COMPLETED` row is not `PROCESSING`. G1/G2: `result_json IS NULL` every time. |
  | Can it touch a newer generation? | No — `lease_generation = :gen` is in the predicate. G3: `False`, every column byte-identical, B's commit still 200. |
  | Can it reduce recovery? | No — the branches are exhaustive. `retryable AND attempt_count < max_attempts` → `RETRY_WAIT`, which is exactly what a reclaim produces; otherwise `FAILED`, where a reclaim is impossible anyway (`claim_next_job` requires `attempt_count < max_attempts`) or the failure was deterministic. G2 shows the `RETRY_WAIT` job is still claimable at G+1. |
  | Can it race a reclaim into a bad state? | No — one row lock. G5 (blocked failure overtaken by a committed reclaim) → `False`, nothing written; G6 (real concurrent race) → exactly one coherent serialization per iteration, never a double-processed job. |

  So an expiry predicate here would change **no** outcome and would only add a
  path where a genuinely failed job sits `PROCESSING` until someone reclaims it.
  The rule is: expiry is required where authority over a result is asserted
  (`complete_job`, `renew_lease`); it is not required for a write that asserts
  none. `release_job` follows the same reasoning (H2) while still refusing a
  superseded owner (H3) and a `COMPLETED` job (H5).

* **O2 — RESOLVED in the worker, with no server change.** The result route's
  contract is unchanged: it sets `Idempotent-Replayed: true` on exactly the two
  duplicate/late paths (inference_api.py:757-763, 900-912) and sets **no** marker
  on a fresh fenced commit, so "absent" is the route's own statement that the
  response was not a replay. `submit_result` now returns a `SubmitOutcome` — a
  `dict` subclass carrying the server's envelope unchanged plus `replayed: bool`
  — and the marker is parsed strictly: `true` → replay, `false`/absent → fresh,
  **anything else is refused** (`MalformedResponseError`) rather than guessed at.
  `run_forever` counts `accepted` and `replayed` separately (`last_run_stats`),
  logs `ACCEPTED` vs `REPLAYED`, and never reports a replay as a completed job.
  Two further gaps in the same area were closed because the O2 tests found them:
  a transport failure during submission escaped `run_forever` and killed the
  worker (now: outcome UNKNOWN, not counted, not resubmitted, loop continues),
  and an unparseable 200 did the same (now: not evidence of acceptance).

* **O7 — `fail_job` always uses the FIRST-attempt backoff.** It passes
  `retry_backoff_seconds(1)` regardless of `attempt_count` (async_jobs.py), so a
  job on its 4th attempt still waits the base delay. This is a tuning wart, not a
  correctness one (the attempt budget is enforced separately by the claim), and
  it is recorded rather than changed here.

* **O8 — the result route blocks the event loop.** `submit_inference_result` is an
  `async def` handler that runs `kernel.process_from_source` synchronously, so one
  submission occupies its uvicorn worker for the whole validation. This was found
  while building §7.4 case I (the parked request made every other request to the
  same server wait) and is the reason case I uses a second app instance. It is a
  throughput property, not a lease-safety one, and it is out of scope here.
* **O3 — the tolerated-failure window uses the local monotonic clock.** The
  deadline is extended only by confirmed renewals, so clock skew changes *how
  long* a worker keeps trying, never whether a stale commit lands (the server
  decides that). Skew costs an extra 409, never a wrong number.
* **O4 — a lost lease does not abort an in-flight model run.** `call_bridge` is
  not cancellable; the candidate is discarded at submission (worker:1002). This
  is the correct trade-off for a device (aborting mid-generation wastes the same
  work) but should be documented as intended behaviour.
* **O5 — a bogus/foreign proof on an already-COMPLETED job returns 200 + the
  stored envelope** (replay path precedes proof validation). It is a read of a
  result the same tenant can fetch via `GET /v1/results/{id}`, so there is no
  confidentiality or integrity change.

---

## 7. Integration evidence summary (INT, isolated)

`scripts/platrixa_stage2_lease_safety_integration_test.py` — **106/106 checks,
exit 0** (was 101/101 before F3; the delta is the rewritten S2 2b and S4 c blocks,
which now assert the O1 rule instead of the defect), `threads still alive after
teardown: []`.

Real layers: `uvicorn`-hosted `api.main:create_app()` on `127.0.0.1` (ephemeral
port), real `POST /v1/inference-jobs`, real claim/renew/result routes, real
PostgreSQL (embedded, disposable) and the real `async_jobs` SQL, with the worker
under audit making genuine `requests` calls. Only the device-side pieces are
substituted: a controllable mock bridge on loopback and the Stage 1 kernel stub.

| Scenario | Result |
|---|---|
| S1 happy path (claim → bridge → complete body → commit → read back) | PASS; `api_status=VERIFIED` from the kernel, stored envelope == returned envelope == `GET /v1/results/{id}` |
| S2 lease expiry (+ reclaim / no reclaim) | PASS; superseded → 409 `LEASE_LOST` on both renew and submit; expired-unsuperseded → 409 `LEASE_EXPIRED` on both, nothing stored, then a reclaim at G+1 commits (**F3**) |
| S3 competing workers, both orders | PASS; late A → 200 replayed, row unchanged; B's envelope preserved |
| S4 interruption (before inference / blocked inference / after inference / superseding race / in-flight 409) | PASS; candidate never committed on any loss path. The "expiry committed before the body is returned" block now expects `LeaseLost(LEASE_EXPIRED)` + a reclaim that commits, not a late commit (**F3**) |
| S5 renewal failures (transient, repeated past deadline, real 409, malformed, foreign proof) | PASS; transient tolerated then confirmed, real 409 → `LeaseLost` |
| S6 bridge matrix (malformed JSON, non-object, oversized, 500, closed connection, timeout, refused, 404→`/`, 405→`/`, connection-error short-circuit, TRUNCATED) | PASS; nothing malformed was ever submitted; the tenant API key never reached the bridge; TRUNCATED travelled verbatim and the engine's terminal state was stored |
| S7 idempotency (same Idempotency-Key, duplicate result, bogus generation, final read) | PASS; one job, one reservation, one commit, replays exactly |
| S8 poll-loop liveness (422 rejection then the next job) | PASS after F2 (FAIL before) |

Isolation guardrails asserted at startup: the API/mock-bridge origins are
loopback, and the suite aborts if `PLATRIXA_API_BASE_URL`,
`PLATRIXA_MODEL_ENDPOINT_URL`, `PLATRIXA_LOCAL_BRIDGE_URL`, `PLATRIXA_API_KEY` or
`PLATRIXA_MODEL_API_KEY` is set to a non-loopback/real value.

---

### 7.1 Deterministic LIVE two-worker race (`scripts/platrixa_stage2_two_worker_race_test.py`)

**23/23 checks, exit 0**, `worker heartbeat threads still alive after teardown: []`,
and the disposable embedded cluster is now removed by the suite's own teardown
(`shutil.rmtree` of the cluster directory after `srv.cleanup()`, so no temporary
database files survive the run — the same teardown was added to the §7 suite).
Real layers: the same `uvicorn`-hosted app, the same real PostgreSQL store, the
same claim/renew/result routes and their fenced SQL, and **two real
`OutboundInferenceWorker` instances with different worker identities** (`race-worker-a`,
`race-worker-b`) sharing ONE authenticated tenant. Every request is a genuine HTTP
request over loopback; the only substituted piece is the mock bridge. Ordering is
carried by `threading.Event` barriers with bounded timeouts, and
`RecordingSession` re-checks the `b_claimed` barrier **inside A's own result
request**, so "A submitted after B held the newer generation" is an in-request
assertion rather than a timing observation. The measured generation transitions:

| Phase | Actor | Observed |
|---|---|---|
| claim | worker A (`race-worker-a`) | `CLAIMED`, owner `race-worker-a`, generation **G** (=1) |
| inference | worker A | bridge request held open on a barrier (`/interpret`) |
| lease loss | store variant: server-side expiry event; real variant: the server's own 2 s clock | — |
| claim | worker B (`race-worker-b`) | `CLAIMED`, owner `race-worker-b`, generation **G+1** (strictly newer, every iteration) |
| submit A (order 1) | worker A, stale proof | **HTTP 409** `{"error":{"code":"LEASE_LOST"}}` from the real API; job stays `PROCESSING`, `result_json IS NULL`, `lease_owner=race-worker-b` |
| submit B (order 1) | worker B | **HTTP 200**, `api_status=VERIFIED`; stored envelope == B's == `GET /v1/results/{id}` |
| submit B (order 2) | worker B first | **HTTP 200** `VERIFIED`; stored |
| submit A (order 2) | worker A, stale proof | **HTTP 200** with `Idempotent-Replayed: true` carrying **B's stored envelope**; the row is unchanged (recorded separately from the 409, per O2) |

Repeatability: **20 iterations per arrival order (40 races) plus 10 real-clock
iterations**, each starting from fresh isolated state, with every invariant
re-asserted each time. Invariants asserted per race: separate worker identities;
`gen_b == gen_a + 1`; A's request after B's claim (in-request barrier); the 409/200
came from the real socket (not a mocked route); B's envelope is what the store and
`GET /v1/results/{id}` both return; final `lease_generation == gen_b` with the lease
released; and **A's stale marker never appears in the authoritative result** (A's
candidate deliberately carries `marker-stale-worker-a`, so an overwrite would be
visible, not just inferred from equality). Tenant scoping is asserted too: a worker
authenticated as a *different* tenant gets `NO_WORK` and cannot touch A's job —
which is exactly why two contending workers must share one tenant.

**Precision about "two workers".** The two workers are two independent
`OutboundInferenceWorker` objects on two threads of the test host, each with its own
HTTP session, credentials and lease; they contend through the real server, and the
server is what decides. This is a genuine live concurrency race at the server, but
it is **not** two OS processes and not two physical devices. What it proves is that
the server fence holds against two simultaneously live lease holders; it does not
prove anything about multi-process scheduling or device-side behaviour.

### 7.2 Shutdown-assertion review (what that diff actually changed)

Review item: prove that replacing the assertion is sound, i.e. that the new
assertions check the intended lifecycle invariant and not merely an event's current
boolean. The complete change to the assertion (the one and only line removed from
the test file):

```diff
-    assert job.state.is_lost and worker.stop_event.is_set(), "the heartbeat was stopped and the lease lost"
+    assert job.state.is_lost, "the lease must be marked lost"
     assert not any(t.name == "platrixa-lease-heartbeat" and t.is_alive() for t in threading.enumerate())
+    # Per-job teardown must NOT set the worker-wide shutdown signal — otherwise a
+    # worker stops after its first job (audit finding).
+    assert worker.stop_event.is_set() is False
```

**Why the old assertion was incorrect.** It recorded the defect as the contract:
`stop_heartbeat()` used to set the worker-wide `self._stop_event`, which is the event
`run_forever()` loops on (worker:1057, 1071), so "the heartbeat was stopped" was true
only because a job ending had also ended the worker. `worker.stop_event` means
"the operator wants this worker to stop" (worker:1126-1133 is its only writer), and
`job.state.is_lost` means "this one lease is gone". Asserting them together encoded a
conflation of two different lifecycle events.

**What the new tests prove (not just a flag value).**

| Requirement | Where proved | How |
|---|---|---|
| A normally finished job cleans up its own heartbeat | UNIT `test_job_lifecycle_is_distinct_from_worker_shutdown` (boundary snapshots), INT race-suite S3 | no `platrixa-lease-heartbeat` thread alive at each job boundary; `stop_heartbeat` joins boundedly (worker:905-918) |
| The worker-wide signal stays unset after a job | UNIT (the two poll-loop tests + the lifecycle test), INT S3 | asserted at the real job boundaries and after the loop |
| The SAME worker serves more than one job | UNIT `test_poll_loop_serves_more_than_one_job` (2 jobs, 3 claims), INT S3 (two real jobs COMPLETED, both `VERIFIED`) | through the real claim/submit interface |
| Lease LOSS abandons only the current job | UNIT lifecycle test (job-2 is fenced with 409; job-1 and job-3 still complete, `completed == 2`), INT race S1/S2 | a fenced job that ended the worker would leave job-3 unserved |
| The real shutdown mechanism ends the loop | UNIT lifecycle test, INT S3 | `stop()` → `stop_event.is_set() is True` (negative control: the flag is not trivially false), `run_forever` then returns 0 **without issuing one further claim**, and the job left behind stays `QUEUED` with `attempt_count == 0` |

So the replacement assertion is a *necessary but not sufficient* check, and the
paired regression tests are what make it meaningful: they demonstrate the invariant
through the real interfaces (second job claimed and committed; `stop()` halting the
loop; a fenced job not halting it) rather than by reading an event's value.

---

### 7.3 O1 lease-expiry regression suite (`scripts/platrixa_stage2_o1_lease_expiry_test.py`)

**22/22 checks, exit 0**, 10 scenarios × **20 iterations = 200 deterministic
iterations**, fresh isolated job/store state per iteration (`S2.reset`), no sleeps
that decide an outcome, `worker heartbeat threads still alive after teardown: []`,
`stray non-infrastructure threads after teardown: []`, and the disposable cluster
directory removed by the suite's own teardown.

Real layers: the same `uvicorn`-hosted `api.main:create_app()` on loopback, the
real claim/renew/result routes and their fenced SQL, the real PostgreSQL store.
The only substitution is the model bridge (and the kernel is the Stage 1 harness
client, as in the other suites).

| Case | Coordination | Observed |
|---|---|---|
| **A** expired, unsuperseded | expiry moved into the past in the store (a crashed worker's signature) | submit → **409 `LEASE_EXPIRED`**, `nothing was stored`; row stays `PROCESSING`, owner A, gen G, `result_json IS NULL`, `http_status IS NULL`, `attempt_count 1`; `GET /v1/results/{id}` → **404 `RESULT_NOT_READY`**; renew → **409 `LEASE_EXPIRED`** with the stored expiry **unchanged**; a repeat submit stays refused |
| **B** expired → reclaim at G+1 | sequential, real routes | B `CLAIMED` at **gen G+1**; A (superseded) → **409 `LEASE_LOST`** (a *different* code from A's own expiry); row still B's, no result; B → **200 `VERIFIED`**; stored envelope == B's, == `GET /v1/results/{id}`; A's marker absent |
| **C1** commit racing an expiry that commits first | control `SELECT … FOR UPDATE`; wait until `pg_stat_activity` shows the commit **blocked on `Lock`/`transactionid`**; commit the expiry; release | **409 `LEASE_EXPIRED`**; nothing stored; nothing was stored *while* the statement was blocked either |
| **C2** commit before expiry is not undone | commit first, then move the expiry into the past | commit **200 `VERIFIED`**; status stays `COMPLETED`, stored bytes unchanged; a later submit → **200 + `Idempotent-Replayed: true`** with the committed envelope |
| **D1** concurrent claim vs in-flight commit | commit blocked by a control lock; the REAL `async_jobs.claim_next_job(kind='inference', tenant_id=…)` runs concurrently | claim → `None` (the `SKIP LOCKED` branch **skips** the in-flight row), generation unchanged; the earlier-ordered commit then → **200**; final `COMPLETED` at A's generation; a later claim → `None` |
| **D2** reclaim ordered first | sequential | A (old gen) → **409 `LEASE_LOST`**; B → **200 `VERIFIED`**; A's late arrival → **200 + `Idempotent-Replayed: true`** carrying **B's** envelope; stored result unchanged |
| **E1** renewal before expiry | real renew route | **200**; persisted `lease_expires_at` moves forward and stays in the future; the commit then → **200** |
| **E2** renewal after expiry | real renew route | **409 `LEASE_EXPIRED`**; persisted expiry **byte-identical** to the expired value (no resurrection); the commit stays refused |
| **E3** renewal blocked, overtaken by a committed expiry | control lock + `pg_stat_activity` block detection | **409 `LEASE_EXPIRED`**; the lease remains expired in the store |
| **F** duplicate submission | sequential | first **200 `VERIFIED`**; second **200 + `Idempotent-Replayed: true`**, identical envelope, stored bytes and generation unchanged |

Why the ordering cases are legitimate despite a single-process server: the result
route is an `async def` handler doing blocking DB work, so one uvicorn process
serialises its own requests on the event loop — two *API* requests cannot overlap
in-process. C1 and E3 therefore drive the **real route** in a background thread
(the request is genuinely in flight and blocked inside PostgreSQL) while the test
only orchestrated the row from a control connection; D1 invokes the **exact store
statement the claim route calls** (`claim_next_job`), because that is the only way
to have a claim and a commit live at the same instant against one process. The
fencing logic under test lives in those statements, and every status/body in cases
A, B, D2, E2 and F comes from a real HTTP response.

**Two assertions that had to change** (corrected, not weakened — both asserted the
defect):

```diff
- check("OBSERVED: an expired-but-unsuperseded lease can still RENEW", r_expired_renew.status_code == 200)
- check("OBSERVED: an expired-but-unsuperseded lease can still COMMIT", r_expired_submit.status_code == 200)
+ check("O1: an expired-but-unsuperseded lease CANNOT be renewed (no silent resurrection)",
+       r_expired_renew.status_code == 409 and code == "LEASE_EXPIRED")
+ check("O1: an expired-but-unsuperseded lease CANNOT commit",
+       r_expired_submit.status_code == 409 and code == "LEASE_EXPIRED")
```

```diff
- check("interruption after inference ... still committed, because no generation superseded it",
-       envelope.get("api_status") == "VERIFIED" and status == "COMPLETED")
+ check("O1: expiry committed before the submission is REFUSED (no unsuperseded commit)",
+       isinstance(raised_c, LeaseLost) and "LEASE_EXPIRED" in str(raised_c))
+ check("O1: the refused late candidate stored nothing ...", status == "PROCESSING" ...)
```

Both replacements assert *more* than the old line (status **and** error code **and**
persisted state). The S4 replacement was observed failing against the pre-F3
server (the suite aborted with an uncaught `LeaseLost` from the late submission)
before the fix made it pass. The S2 2b replacement was **not** re-run against the
pre-fix server: its pre-fix behaviour is the `200`/`200` observation recorded in
§3 F3, which is exactly what those two old lines asserted.

### 7.4 O2 / O6 / reclaim-interleaving / SIGKILL suite

`scripts/platrixa_stage2_o6_state_and_crash_test.py` — **36/36 checks, exit 0**,
18 section checks × **8 iterations each = 144 case runs** with fresh isolated
state per run, `worker heartbeat threads still alive after teardown: []`,
`stray non-infrastructure threads after teardown: []`, disposable cluster removed,
driver scratch directory removed.

Real layers: the real `create_app()` served by **two** uvicorn instances on loopback
(see case I), the real claim / renew / result routes, the real `async_jobs` SQL
executed by `async_jobs.release_job` / `fail_job` / `complete_job`, and the real
PostgreSQL store. Substituted: the Stage 1 kernel stub and, in §J, small driver
processes that call the real store function over the real database.

**O6 — `fail_job` transition table (SRC + INT)**

| Case | Preconditions | Predicate outcome | State written | Can it reach a newer gen? |
|---|---|---|---|---|
| G1 | owner A, gen G, unexpired, retryable, budget left | matches | `RETRY_WAIT`, lease cleared, error envelope, `next_attempt_at` set, `result_json NULL`; the immediate claim → `NO_WORK` | n/a |
| G2 | owner A, gen G, **expired**, nobody reclaimed | matches | `RETRY_WAIT` (same outcome a reclaim reaches); generation unchanged; reclaim at G+1 then succeeds | no |
| G3 | A superseded by B at G+1 | **0 rows → `False`** | every column byte-identical; B's `PROCESSING` lease intact; B's commit → **200** | no |
| G4 | second call by the same owner | 0 rows (not `PROCESSING`) | no-op | no |
| G5 | A's failure **blocked on the row lock**; a reclaim commits while it waits | 0 rows after re-check | reclaim's state survives, `error_json NULL` | no |
| G6 | real concurrent race: A's failure vs B's `claim` | one of two coherent orders per iteration, both asserted | failure-first → `RETRY_WAIT` + no claim; claim-first → `PROCESSING` + no failure envelope | no |

**O6 — `release_job` transition table (the fix)**

| Case | Call | Result | Row afterwards |
|---|---|---|---|
| H1 | current owner releases an unexpired job | `True` | `QUEUED`, lease NULL, generation unchanged, immediately claimable at G+1 |
| H2 | owning-but-**expired** worker releases its own job | `True` | `QUEUED` (the reclaim a moment later would do the same) |
| H3 | **superseded** owner releases B's live lease | **`False`** | every column byte-identical; B's commit still **200** |
| H4 | repeated release | `True` then `False` | second call is a no-op |
| H5 | release a **COMPLETED** job | `False` | `COMPLETED`, result bytes unchanged |
| H6 | release blocked on the row, reclaim commits first | `False` | reclaim's `PROCESSING`/owner B survives |
| H7 | **counterfactual**: the pre-O6 statement runs against B's live lease | (unconditional UPDATE) | row → `QUEUED`; B's commit → **409**, `result_json NULL` — the hazard was real |

**Concurrency case A — reclaim between kernel validation and the UPDATE**

A real result request is paused *inside* the real kernel call, after
`process_from_source` has returned (validation, grounding, accounting and rules all
ran) and before the route reaches `complete_job`. The pause is a test-only wrapper
around the real `Kernel` object; no production code has a test seam, and no route
or transaction is mocked.

| Case | Exact ordering | Observed |
|---|---|---|
| I1 | A claims G → A validates → **pause** → expiry → B claims **G+1** → resume A | A's candidate → **409 `LEASE_LOST`**; row still `PROCESSING`/owner B/gen G+1; `result_json NULL`; A's `renew` → **409**; A's `release_job` → `False`; the row is byte-identical after both refusals; B's commit → **200 `VERIFIED`**, A's marker absent |
| I2 | same, but B claims **and commits** before A resumes | A → **200** with `Idempotent-Replayed: true` carrying **B's** envelope; the row is byte-identical to its pre-resume state; A's marker absent |

Why two app instances: the route runs the kernel synchronously inside an `async
def`, so while A is parked that server's event loop is occupied (observation O8).
Worker B is served by a second uvicorn instance in the same process — same app, same
store, same kernel stub — so B's claim and commit are genuine HTTP requests through
the real routes, not an in-process substitute.

**Concurrency case B — SIGKILL, three distinct outcomes**

| Case | What is killed, where | Evidence | Result |
|---|---|---|---|
| **J1** commit **not yet issued** | driver process, at the GO barrier (before `complete_job` is called) | exit status **-9 (`SIGKILL`)**; row byte-identical to its pre-kill snapshot | nothing committed; lease still valid; `renew` → 200 and the recovery commit → 200 |
| **J2** commit **in flight, the transaction's owner dies** | driver process, while its `UPDATE … SET status='COMPLETED'` is provably **blocked inside PostgreSQL** (`pg_stat_activity` shows `state='active'`, `wait_event_type='Lock'` on that statement) | exit **-9**; the waiting backend is then released by dropping the control lock | PostgreSQL **rolls back**: row byte-identical, `result_json NULL`, generation intact; the **same generation** then commits → 200 |
| **J3** commit **already sent; client dies** | the HTTP client process, while the *server's* UPDATE is blocked | exit **-9**; the control lock is then released | the server still **commits**: `COMPLETED`, one result, generation unchanged; a retry at the same generation → **200 + `Idempotent-Replayed: true`** with byte-identical result — one authoritative result, never two |

**A real PostgreSQL nuance measured in J2, not assumed.** A backend that is waiting
for a row lock is not reading its socket, so killing its client does **not** abort
the transaction immediately; PostgreSQL notices only after the lock is released and
the backend resumes. The suite therefore kills first, then releases the control
lock, and only then judges the row — which is what a real dead client's transaction
experiences. All three outcomes are covered; none is inferred from another.

**O2 — worker-level evidence (UNIT, in-process transport)**

`scripts/platrixa_outbound_inference_worker_test.py` — **27/27** under both the
documented script runner and `pytest` (5 new checks, 23-27 in its header):

| Check | Asserts |
|---|---|
| 23 fresh acceptance | 200 with no marker → `replayed False`, `accepted True`, envelope unchanged and still JSON-encodable; loop stats `{accepted: 1, replayed: 0}` |
| 24 replay | `Idempotent-Replayed: true` → `replayed True`, `accepted False`; `run_forever` returns **0** completed, stats `{accepted: 0, replayed: 2}`, one submission per job, the loop keeps serving |
| 25 strict parsing | `true`/`TRUE`/` true ` → replay; `false`/absent → fresh; `yes`/`1`/`maybe`/`` → `MalformedResponseError`; through the loop: not counted, not resubmitted, loop survives |
| 26 ambiguous submission | transport failure on submit → not counted as accepted, not resubmitted, `stop_event` unset, direct caller gets `TransportError`, lease **not** marked lost |
| 27 logging | exactly one `ACCEPTED` and one `REPLAYED` line, distinct; neither the tenant key nor the bridge key appears in any record |

No server change was made for O2: the header, its `true` value and its absence on a
fresh commit were already the route's contract (verified at
inference_api.py:757-763, 900-912 and 977).

### 7.5 What changed in the working tree (base `570330b`, branch `platrixa/stage2-o1-lease-expiry`)

| File | Nature | Change |
|---|---|---|
| `backend/auth/async_jobs.py` | **implementation** | F3 commit/renew liveness predicates + `lease_commit_refusal_reason` (O1 batch); **new `release_job`** (F4) and the `fail_job` non-expiry rationale |
| `api/routes/inference_api.py` | **implementation** | `409 LEASE_EXPIRED` vs `LEASE_LOST` on submit and renew (O1 batch; no change for O2/O6) |
| `api/routes/async_api.py` | **implementation** | `_release_lease` delegates to the fenced `release_job` (F4) |
| `scripts/platrixa_outbound_inference_worker.py` | **implementation** | `SubmitOutcome` (dict-compatible) + strict `Idempotent-Replayed` parsing + `accepted`/`replayed` counting & logging + transport/malformed handling in the poll loop (O2) |
| `scripts/platrixa_outbound_inference_worker_test.py` | **tests** | 5 O2 checks (23-27); existing 22 untouched |
| `scripts/platrixa_stage2_o6_state_and_crash_test.py` | **new tests** | 18 sections: G1-G6, H1-H7, I1-I2, J1-J3 |
| `scripts/platrixa_stage2_o1_lease_expiry_test.py` | tests (O1 batch) | unchanged in this pass |
| `scripts/platrixa_stage2_lease_safety_integration_test.py`, `scripts/platrixa_stage2_two_worker_race_test.py`, `scripts/platrixa_stage2_device_benchmark.py` | tests/tools (earlier batches) | unchanged in this pass |
| `reports/STAGE2_LEASE_SAFETY_AUDIT.md` | **documentation** | this revision |

No schema change, no migration, no new table, no dependency change, and no change
to any API request/response *shape*. Nothing is committed, pushed, merged or
deployed; `main` and `platrixa/local-bridge-integration` are untouched.

Final working-tree status (`git status --short`, HEAD `570330b`):

```
 M api/routes/async_api.py
 M api/routes/inference_api.py
 M backend/auth/async_jobs.py
 M scripts/platrixa_outbound_inference_worker.py
 M scripts/platrixa_outbound_inference_worker_test.py
?? reports/STAGE2_LEASE_SAFETY_AUDIT.md
?? scripts/platrixa_stage2_device_benchmark.py
?? scripts/platrixa_stage2_lease_safety_integration_test.py
?? scripts/platrixa_stage2_o1_lease_expiry_test.py
?? scripts/platrixa_stage2_o6_state_and_crash_test.py
?? scripts/platrixa_stage2_two_worker_race_test.py
```

Exact commands used for the results in this report (all loopback, disposable store):

```
.venv/bin/python scripts/platrixa_outbound_inference_worker_test.py
.venv/bin/python -m pytest scripts/platrixa_outbound_inference_worker_test.py -q
.venv/bin/python scripts/platrixa_stage2_o6_state_and_crash_test.py 8
.venv/bin/python scripts/platrixa_stage2_o1_lease_expiry_test.py 5
.venv/bin/python scripts/platrixa_stage2_lease_safety_integration_test.py
.venv/bin/python scripts/platrixa_stage2_two_worker_race_test.py
.venv/bin/python scripts/local_bridge_contract_test.py
.venv/bin/python scripts/device_bridge_contract_check.py
.venv/bin/python scripts/fte_fyjc_52_kernel_boundary_test.py
.venv/bin/python scripts/fte_fyjc_89_outbound_inference_bridge_test.py
.venv/bin/python scripts/fte_fyjc_81_phase5e_async_documents_test.py
.venv/bin/python scripts/fte_fyjc_88_phase5k_queue_worker_test.py
.venv/bin/python -m py_compile <every modified .py>   &&   git diff --check
```

---

## 8. Not verified (and why)

* **Real-device behaviour → PENDING.** No Android/Termux host or `llama-server`
  in this environment. `scripts/platrixa_stage2_device_benchmark.py` is the
  runnable harness (guardrails and dry-run verified here); every Android field
  prints `NOT MEASURED` until it runs on a device.
* **Exact in-flight wire interleaving (I4) → VERIFIED for the ordering that
  matters.** §7.4 case I pauses a real request after the real kernel validation
  and before `complete_job`, lets B reclaim (and, in I2, commit) at G+1, then
  resumes it: **409 `LEASE_LOST`** / a labelled replay of B's envelope, with A
  unable to renew or release and the row byte-identical after the refusals. Two
  app instances are used because one `async def` handler running the kernel blocks
  its event loop (O8). Still not *separately* instrumented: a reclaim committing
  in the small window **between** the route's Python owner/generation pre-check
  and the fenced UPDATE. That window is decided by the UPDATE itself (it can only
  refuse), and I1 exercises the same refusal path, but the micro-ordering is not
  individually recorded.
* **Two live lease holders racing on one job → NOW VERIFIED at the server.** §7.1
  runs two live workers against the real server in both arrival orders, 40 races +
  10 real-clock races, all clean. Still **not** verified: two OS *processes* (they
  are threads of one host process) and two physical devices — the server-side
  concurrency is real, the process topology is not exercised.
* **SIGKILL → VERIFIED for three distinct outcomes, not for every possible one.**
  §7.4 J1 (killed before the statement), J2 (killed while this process's commit
  UPDATE was blocked inside PostgreSQL → the transaction was **rolled back**, row
  byte-identical, same generation still commits) and J3 (client killed while the
  **server** was mid-commit → the commit landed, and a retry replayed exactly one
  authoritative result). What is **not** covered: a crash during the COMMIT record
  write itself (the J2 kill lands while the UPDATE waits on a lock, so PostgreSQL's
  abort path — not its commit path — decides); an OS/network partition that hides a
  commit from every later observer (two-phase commit is not in use — recovery relies
  on the row, which either has the result or does not); and SIGKILL of the **API**
  process specifically (J2 kills the process that owns the transaction, which is the
  same failure shape but a different binary).
* **The claim's own `attempt_count` bump under a crash → not separately audited.**
  An attempt is consumed by the claim statement itself, so a worker killed right
  after claiming burns one unit of the budget. That is the pre-existing 5K design
  (bounded retries), and it is recorded here rather than changed.
* **The API's own request-log/observability persistence under load → not audited**
  (out of scope; untouched by this task).

---

## 9. Release gate and next action

State of the evidence after F3 (O1) + F4 (O6) + the O2 worker change:

| Gate | Status |
|---|---|
| O1 enforced atomically by the real server | **MET** — §3 F3, §5.1 (one conditional UPDATE, DB clock) |
| Expired-but-unsuperseded regression | **MET** — §7.3 case A, 20/20 |
| Old-generation commits rejected | **MET** — §7.1 (40 races + 10 real-clock), §7.3 B/D2 |
| Lifecycle assertion backed by a real second-job test | **MET** — §7.2 table, §7.1 S3 |
| Every documented runner includes the intended tests | **MET** — worker suite **27/27** under both the script runner and pytest (the 5 O2 checks are in its `TESTS` tuple); the O1 and O6 suites are their own documented runners (`python <suite> [iterations]`) |
| Temporary database + thread cleanup | **MET** — disposable cluster removed and no stray threads in all four Stage 2 suites; the O6 suite also removes its driver scratch directory |
| O6 release write fenced (no stale re-queue) | **MET** — §3 F4, §7.4 H1-H7, incl. the H7 counterfactual |
| `fail_job` semantics established, not assumed | **MET** — §6 O6 table, §7.4 G1-G6 |
| O2 replay distinguished from acceptance | **MET** — §7.4 O2 table (UNIT, worker suite 23-27); server contract unchanged |
| Expired lease cannot authorize a commit | **MET** — §3 F3, §7.3 A/C1/E2/E3 |
| Stage 1 + Stage 2 suites | **MET** — §7, §7.1, §7.3, §7.4 and the results table in the final report |
| Full diff + this report reviewed | **MET** |
| **Real Android/Termux benchmark** | **NOT MET — RELEASE BLOCKER under this task's policy** |

**What O2, O6, case I and case J changed about the release decision: nothing, and
that is the honest result.** F4 closes a real defect (a stale release could discard
a newer worker's finished work) and O2 closes a real reporting defect (a device
could log a discarded candidate as accepted). Neither could ever publish a wrong
result, and neither was on the O1 critical path. Case I turns the last
"adjacent-orderings" caveat into a measured ordering, and case J replaces "crash
tested only as simulated loss" with three measured SIGKILL outcomes — one of which
(rollback after the transaction's owner dies) is now evidence *for* the existing
design rather than a new risk. The correctness gate is therefore unchanged:
still met.

The only remaining blocker is the device measurement, and it is a *tuning* gate,
not a correctness gate: every property above is enforced server-side, while the
open question is device-hardware latency versus the lease window (how often a slow
device loses its lease and forces a re-inference — not whether a wrong result can be
committed). Run:

```
# on the Android device (Termux), with llama-server running locally
PLATRIXA_API_BASE_URL=http://<host>:<port> PLATRIXA_API_KEY=<test key> \\
  python scripts/platrixa_stage2_device_benchmark.py --smoke
```

and then without `--smoke` for the distribution (p50/p95/max, heartbeat cadence,
renewal latency, renewal failures, expired-lease frequency, RSS/CPU/thermal) once
the smoke run is clean. Until then the lease defaults remain **provisional**, and
this audit does not recommend merging or deploying.
