# Android four-worker and batch acceptance — device procedure

**Status: PENDING REAL-DEVICE EXECUTION.** Nothing in this document has been
measured on a phone yet. The offline simulation
(`scripts/platrixa_four_worker_queue_test.py`, 17 checks) proves the server-side
queue properties only. It does not measure the phone, the model, thermals, or
four-way concurrency — and it must never be reported as if it did.

Architecture under test:

```
3–4 developer tenants → Platrixa API (Railway) → durable queue
   → four Termux worker processes → ONE shared local llama.cpp server
   → candidate submission → deterministic kernel validation
```

The model server is bound to the phone's loopback only
(`127.0.0.1:11434`). No tunnel, no public model port, no developer access to the
device.

---

## 0. Record the device first

Run in Termux and paste the output into the test record:

```bash
getprop ro.product.manufacturer
getprop ro.product.model
getprop ro.build.version.release
getprop ro.product.cpu.abi
nproc
grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo
termux-battery-status
```

Also record: model identifier and quantisation, `llama-server` invocation
(context size, `-ngl`, threads, `--parallel`), and whether the server was
started with one slot or more.

**Honesty rule:** if `llama-server` is started with one generation slot, four
workers provide *pipelining* (one generates while others queue/hold leases),
**not** four-way inference concurrency. Do not raise the model's parallel slot
count just to finish this test faster; raise it only after a measured benchmark
shows memory and thermal headroom.

---

## 1. Prepare four workers (one shared slot limiter)

```bash
pkg install python git -y
termux-wake-lock

export PLATRIXA_ROOT="$HOME/platrixa"
mkdir -p "$PLATRIXA_ROOT"/{slots,keys,logs}
chmod 700 "$PLATRIXA_ROOT"

# one tenant key per developer tenant; keep each in a 0600 file, never in argv
cp /path/to/tenant-a.key "$PLATRIXA_ROOT/keys/tenant-a.key"
chmod 600 "$PLATRIXA_ROOT/keys/tenant-a.key"

export PLATRIXA_API_BASE_URL="https://<your-beta-host>"
export PLATRIXA_MODEL_URL="http://127.0.0.1:11434"
export PLATRIXA_MODEL_ID="<model-id>"
export PLATRIXA_SLOT_DIR="$PLATRIXA_ROOT/slots"
export PLATRIXA_SLOT_MAX=1        # keep 1 until a benchmark proves more is safe
```

Start the model server separately (its own Termux session) and confirm it is
loopback-only:

```bash
# example — use the exact llama.cpp build and model on the device
llama-server -m "$HOME/models/<model>.gguf" --host 127.0.0.1 --port 11434 \
  --ctx-size 4096 --threads 4 >> "$PLATRIXA_ROOT/logs/llama.log" 2>&1 &
curl -sS http://127.0.0.1:11434/health
```

From another device on the same Wi-Fi, confirm the port is **not** reachable
(the phone's IP must refuse `11434`). That check is part of acceptance.

---

## 2. Launch four workers

Four processes, four distinct worker ids, ONE shared slot directory:

```bash
cd "$PLATRIXA_ROOT"
for i in 1 2 3 4; do
  python scripts/platrixa_outbound_inference_worker.py \
    --api-base-url "$PLATRIXA_API_BASE_URL" \
    --api-key-file "$PLATRIXA_ROOT/keys/tenant-a.key" \
    --worker-id "phone1-w$i" \
    --model-url "$PLATRIXA_MODEL_URL" \
    --model-id "$PLATRIXA_MODEL_ID" \
    --model-dialect completion \
    --slot-dir "$PLATRIXA_SLOT_DIR" --slot-max 1 --slot-wait 5 \
    --status-file "$PLATRIXA_ROOT/logs/w$i.status.json" \
    --max-polls 500 --verbose \
    >> "$PLATRIXA_ROOT/logs/w$i.log" 2>&1 &
  echo "worker $i pid $!"
done
```

Watch the shared limiter and the workers:

```bash
ls "$PLATRIXA_ROOT/slots"            # slot-0/ while a generation is running
tail -f "$PLATRIXA_ROOT/logs/w1.log"
```

Assign tenants deliberately: either all four workers on one tenant (the queue
test), or one worker per developer tenant (the real beta shape). With one
tenant per developer, tenant isolation is enforced by the API, not by the
worker.

---

## 3. Test A — four workers on one tenant

1. Submit 12 single-transaction jobs for the test tenant (use
   `examples/developer_batch_client.py` or the quickstart `curl` loop).
2. Confirm the claim spread: `GET /v1/owner/queue/status` (management token)
   lists lease-derived workers; each claim should name a distinct
   `lease.owner` (`phone1-w1` … `phone1-w4`).
3. Confirm no duplicate processing: every job ends `COMPLETED` exactly once,
   and `GET /v1/jobs` lists 12 entries.
4. Interrupt one worker (`kill <pid>` while it holds a job). Record how long
   the lease takes to expire and whether another worker reclaims the job.
5. Confirm jobs submitted for a *different* tenant are never claimed by these
   workers.

Record:

| Measurement | Value |
| --- | --- |
| Phone model / RAM / Android version | |
| Model id + quantisation | |
| Worker processes | 4 |
| Model server slots (`--parallel`) | |
| `PLATRIXA_SLOT_MAX` | 1 |
| Queue wait per job (submit → claim) | |
| Inference duration per job (claim → result) | |
| End-to-end job duration | |
| CPU utilisation during the run | |
| Thermal / throttling observations | |
| Failures and retries | |
| Phone stable during the run? | |

`top -H -p $(pgrep -d, -f platrixa_outbound_inference_worker)` and
`termux-battery-status` before/after are the minimum evidence. Thermal zones
where readable: `cat /sys/class/thermal/thermal_zone*/temp`.

---

## 4. Test B — multi-transaction batches

Independent mode:

- submit 5 unrelated transactions in one job (`"mode": "independent"`);
- each transaction must receive its own status and result URL;
- the batch envelope must report `total_transactions: 5`,
  `verified_count + review_required_count + unsupported_count + failed_count`
  consistent with the per-transaction rows;
- make one transaction deliberately malformed/unverifiable and confirm the
  others are unaffected (`success` false, batch `PARTIAL`/aggregate not
  `VERIFIED`).

Ordered mode:

- submit a sequence where later transactions depend on earlier approved state;
- confirm only kernel-approved state is carried forward (state chain hashes
  present, `prior_state_hash` links consecutive links);
- make an early link `REVIEW_REQUIRED` or `FAILED` and confirm every later link
  is `BLOCKED` with `BLOCKED_BY_PRIOR_UNVERIFIED_STATE` and receives **no**
  model time;
- confirm no model response can set `VERIFIED` (a candidate that claims it is
  still validated by the kernel).

Record the same table as Test A per batch, plus: batch size, number of
transactions actually evaluated, blocked/deferred counts, and the batch budget
(`PLATRIXA_BATCH_MAX_SECONDS`) versus observed duration.

---

## 5. Test C — developer experience from a separate client

From a laptop (not the phone):

1. submit a batch with the tenant key → `202` + `job_id`;
2. `GET /v1/jobs/{job_id}` until terminal;
3. `GET /v1/jobs` and confirm the job appears exactly once;
4. `GET /v1/results/{result_id}` and inspect per-transaction results;
5. `GET /v1/usage` — confirm one job unit per job and N transaction units for
   an N-transaction batch (they are different numbers);
6. resubmit with the **same** `Idempotency-Key` and body → same `job_id`,
   `Idempotent-Replayed: true`, no extra job, no extra charge.

---

## 6. Acceptance record

Fill in and keep with the release evidence:

- Date / operator / commit under test:
- Phone + model:
- Test A result (pass/fail, measurements):
- Test B independent (pass/fail):
- Test B ordered (pass/fail):
- Test C result (pass/fail):
- Loopback-only model port verified from another device: yes/no
- Thermal throttling observed: yes/no (details)
- Failures not explained by the above:

Until this record is complete and attached, device acceptance is **PENDING**,
and the private beta must not be described as device-validated.
