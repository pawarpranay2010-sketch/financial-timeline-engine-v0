# Multi-Transaction Batch Jobs

One API job can carry a bounded batch of accounting transactions. This document
describes exactly what the implementation does, what it charges, and what the
operator must verify on the phone.

The architecture is unchanged:

```
developer app → Developer API → durable queue → Android Termux tenant worker
             → shared local llama.cpp → candidate → deterministic kernel
             → per-transaction result, tenant-isolated
```

A batch changes **how much work one job carries**, never who is authoritative.

---

## 1. Modes

### `independent`

Each transaction is processed on its own. No transaction's result is an input
to another, and no accounting state is assumed. This is the correct mode for a
day's unrelated vouchers.

The device generates each transaction sequentially (one job is one unit of
device work) and submits every candidate in **one fenced result call**. Each
transaction is then validated separately by the kernel and stored on its own
row, so partial outcomes are explicit.

### `ordered`

The transactions form one accounting sequence. The device submits them **one at
a time**:

1. the worker generates a candidate for link *k*;
2. the server validates it with the same kernel pipeline;
3. if the link is `VERIFIED`, the server returns the approved accounting and
   its `state_hash`; the worker includes that approved state in the prompt for
   link *k+1*;
4. if the link is **not** `VERIFIED`, the server halts the batch: every later
   transaction is marked `BLOCKED` with
   `BLOCKED_BY_PRIOR_UNVERIFIED_STATE` and never receives device inference.

Transactions in a sequence are never parallelised. A submission that skips
ahead is refused with `409 BATCH_ORDER_VIOLATION` (the expected
`transaction_id` is named in `details`).

---

## 2. The state chain

`state_hash` is a SHA-256 over canonical JSON containing:

- the prior link's `state_hash` (or `null` for the first link),
- the batch index and `transaction_id`,
- the engine's terminal state,
- a digest of the deterministic `accounting` result.

It is computed **only** for a `VERIFIED` transaction, from kernel output. Model
text never enters the hash, and a caller can never supply one.

`prior_state_hash` records the hash a link built on, so the chain is auditable
end to end. The approved state handed to the next prompt is bounded
(1200 characters); when the accounting body is too large the prompt receives a
digest plus the hash that commits to it, never a truncated JSON fragment.

`GET /v1/results/{result_id}` returns `state_chain` and `final_state_hash`.

---

## 3. Partial outcomes

| Situation | Result |
| --- | --- |
| one independent transaction's generation fails | that transaction is `FAILED` with the worker's error code; the others keep their real outcomes |
| one independent transaction is ambiguous | that transaction is `REVIEW_REQUIRED`; the others are unaffected |
| the device budget runs out mid-batch | untouched transactions are `DEFERRED` (`BATCH_BUDGET_EXHAUSTED`); `batch_status` is `PARTIAL`; nothing is invented |
| an ordered link is not verified | that link keeps its real state; later links are `BLOCKED`; `batch_status` is `HALTED` |
| the server cannot validate a submission at all | no per-transaction verdict is written; the job follows the existing bounded retry path (`RETRY_WAIT`, then `FAILED`) |

`BLOCKED` and `DEFERRED` transactions are **not** failures: they were never
evaluated. Their per-transaction result URL returns `RESULT_NOT_READY`.

The batch `api_status` obeys one rule: `VERIFIED` only when every processed
transaction is `VERIFIED`, otherwise the first non-verified transaction in batch
order, carried verbatim. `success` additionally requires `COMPLETE` and every
transaction processed.

---

## 4. Quotas and charging (deterministic, no refunds)

Two independent units are tracked per tenant per month:

| Unit | Store | Default |
| --- | --- | --- |
| jobs admitted | `platrixa_tenant_quotas` (existing) | 100 (per provisioning) |
| accounting transactions admitted | `platrixa_tenant_transaction_quotas` | 1000 (`PLATRIXA_TENANT_TX_MONTHLY_LIMIT`) |

Policy:

- charged **once, at admission**, one unit per admitted transaction (a single
  transaction job is 1 job + 1 transaction unit; a 3-transaction batch is 1 job
  + 3 transaction units);
- **not** charged when the request is rejected before admission (malformed,
  duplicate ids, over a limit, bad credential, either quota exhausted, queue
  full, store unavailable);
- **not** charged again for an idempotent replay or for a worker retry of an
  already-admitted job;
- an admitted transaction is charged even if it is later `FAILED`,
  `REVIEW_REQUIRED`, `UNSUPPORTED`, `BLOCKED` or `DEFERRED`: the unit bought
  admission to a bounded unit of device work;
- the only decrement is `transaction_quota.release()`, used exclusively when
  the job row could not be created after the charge was taken.

Owner visibility: `GET /v1/owner/usage` (management token) reports, per tenant:
active/revoked keys, job totals, transactions admitted, verified /
review-required / unsupported / failed / blocked / deferred / pending counts,
oldest pending instant, processing and retry counts, both quotas, and durable
throttling events.

---

## 5. Device fairness (one phone, several tenants)

The device-wide limiter (`scripts/platrixa_slot_limiter.py`) is a cross-process
counting semaphore with **FIFO fairness**:

- every worker registers a waiter ticket before trying a slot;
- a waiter may take a free slot only when no **live** older waiter exists, so a
  tenant with a large backlog cannot starve another by polling faster;
- a ticket whose process is dead (or has no usable PID and is older than the
  TTL) is pruned, so a crash cannot block the queue;
- `status` reports the holder's tenant label and the waiting tenants.

A worker acquires the slot **before generating** and holds it only for the job's
generation phase. If the slot stays busy for the short wait budget, the worker
calls `POST /v1/worker/jobs/{job_id}/release`: the job returns to `QUEUED`, its
lease is cleared and the attempt it consumed is refunded. A busy phone therefore
never fails a valid job, and a waiting worker never holds a lease.

Inference concurrency stays at **N = 1** until the phone is actually measured.
Additional worker processes do not add concurrency.

A batch cannot monopolise the phone: the transaction count, request bytes,
summed input and device wall-clock budget are all bounded, and the budget
produces `DEFERRED` (partial), not a failure.

---

## 6. Operator checks

Provision one tenant per developer, each with its own key and quotas:

```bash
curl -sS -X POST "$BASE/v1/developer/tenants" \
  -H "X-Platrixa-Management-Token: $MGMT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"dev-alice","developer_name":"Alice (invited)",
       "environment":"test","monthly_limit":100}'
```

Confirm the whole plane from the owner side:

```bash
curl -sS -H "X-Platrixa-Management-Token: $MGMT_TOKEN" "$BASE/v1/owner/queue/status"
curl -sS -H "X-Platrixa-Management-Token: $MGMT_TOKEN" "$BASE/v1/owner/usage"
```

On the phone:

```bash
python3 scripts/platrixa_slot_limiter.py status --slots-dir ~/.platrixa/slots
tail -f ~/.platrixa/logs/worker-dev-alice.log
```

What the logs should show for one batch job: one `claim`, one
`generating`/`PROCESSING` phase per transaction, one `result ACCEPTED` for an
independent batch (or one transaction-result submission per ordered link), and
`claim released` (instead of a failure) whenever the slot was busy.

---

## 7. Capacity honesty

No developer capacity is derived from this document. The device's sustained
throughput must be measured on the actual phone with
`scripts/platrixa_worker_benchmark.py`, using completed sustained throughput and
the configured safety margin. Until that measurement exists, no claim about
"how many developers this phone supports" is made.

Batch transactions are a strictly larger unit of device work than a single
transaction, so the measured jobs/hour figure must be interpreted per
transaction, not per API call.
