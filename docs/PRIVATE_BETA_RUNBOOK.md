# Platrixa — private beta runbook (1–3 invited developers, one Android phone)

This is the operator's page. It assumes the code in
`release/private-beta-rc1` is deployed and one phone runs one or more worker
instances.

**The one rule:** the deterministic kernel decides. The phone generates text;
it never decides a verdict. If a document cannot be verified, the answer is
`NEEDS_REVIEW` with reasons, never a guess.

---

## 1. Reality check before inviting anyone

| Capability | State | Evidence |
| --- | --- | --- |
| Deterministic verdicts | implemented, tested | `platrixa_phase1b_developer_workflow_test.py` |
| Per-tenant isolation | implemented, tested | `platrixa_s4_multitenant_authorization_test.py`, `platrixa_phase1b_tenant_isolation_test.py` |
| Queue admission + capacity accounting | implemented, tested | `platrixa_phase1b_queue_capacity_test.py` (28), `platrixa_phase1b_capacity_migration_test.py` (18) |
| Per-tenant rate limiting (durable) | implemented, tested | `platrixa_phase1b_rate_limit_and_ops_test.py` (19) |
| Tenant provisioning + revocation | implemented, tested | `platrixa_phase1b_tenant_provisioning_test.py` (43) |
| One worker / device, tenant-scoped | implemented, tested | `platrixa_outbound_inference_worker_test.py` (51) |
| Several instances on one phone | implemented, tested off-device | `platrixa_termux_multiinstance_test.py` (12), `platrixa_termux_service_test.py` (25) |
| Device-wide generation cap | implemented, tested off-device | `platrixa_slot_limiter_test.py` (17) |
| Encrypted backup + restore drill | implemented, drilled locally | `platrixa_backup_restore_drill_test.py` (20) |
| Retention / pruning | implemented, tested | `platrixa_retention_test.py` (20) |
| Key rotation | documented procedure | `docs/KEY_ROTATION.md` |
| **Real phone capacity** | **NOT MEASURED** | requires the device |
| **Scheduled production backups** | **NOT DONE** | requires a storage target |

Anything in the last two rows must not be described to a developer as working.

---

## 2. Daily (2 minutes)

```bash
curl -sS "$PLATRIXA_API/v1/owner/queue/status" \
  -H "X-Platrixa-Management-Token: $PLATRIXA_MANAGEMENT_TOKEN"
```

Look at `pending`, `in_progress`, `rejected_recent` and `consistent`.

* `consistent: false` → run the reconciler's report (same endpoint) and stop
  inviting work until it is explained. It means a capacity slot leaked or was
  double-released.
* `rejected_recent` climbing with `QUEUE_FULL` → the phone is behind, or a
  developer is looping. Check job volume per tenant before raising limits.

On the phone:

```bash
sv status platrixa-worker-<instance>
tail -n 50 ~/.platrixa/logs/worker-<instance>.log
cat ~/.platrixa/status/worker-<instance>.json
```

---

## 3. Weekly

1. **Backup** (see §4). Verify it, do not just create it.
2. **Retention** — dry run first:
   ```bash
   python3 scripts/platrixa_retention.py --database-url "$DATABASE_URL" --days 90
   python3 scripts/platrixa_retention.py --database-url "$DATABASE_URL" --days 90 --apply
   ```
   Non-terminal rows are never deleted; a terminal row still holding capacity is
   kept so the leak stays visible.
3. **Restore drill** — a backup you have not restored is a hypothesis. Use the
   disposable-PostgreSQL path from `platrixa_backup_restore_drill_test.py`
   against the newest real backup on a machine that is not production.

---

## 4. Backup and restore

```bash
# once, and keep the key OUTSIDE the backup's storage location
python3 scripts/platrixa_pg_backup.py keygen --out /secure/platrixa/backup.key

# create (AES-256-GCM, manifest with hashes and exact row counts)
python3 scripts/platrixa_pg_backup.py create \
  --database-url "$DATABASE_URL" --out /backups/platrixa-$(date +%F).pgbackup \
  --key-file /secure/platrixa/backup.key

# prove it is readable before trusting it
python3 scripts/platrixa_pg_backup.py verify \
  --backup /backups/platrixa-$(date +%F).pgbackup --key-file /secure/platrixa/backup.key

# restore into an EMPTY database (refuses a non-empty target)
python3 scripts/platrixa_pg_backup.py restore \
  --backup /backups/platrixa-$(date +%F).pgbackup --key-file /secure/platrixa/backup.key \
  --target-url "$CLEAN_DATABASE_URL"
```

Lost key = lost backups. Store the key somewhere that is not the bucket holding
the backups, and record *where* it is (not its value) in the team password
manager.

The tool refuses a cross-major restore (backup 18 → server 17). That is a
feature: a silent partial restore is worse than a refusal.

Scheduling is still the operator's job — a cron entry on a machine that can
reach the database, running `create` + `verify`, is enough for beta. **Nothing
in this repository schedules it for you today.**

---

## 5. Capacity: how to get a number you may repeat

Do **not** quote a developer count from the local benchmark. Its stub model
produces arithmetic, not an answer, and the report now says so
(`capacity_estimate.quotable: false`).

1. Run the on-device benchmark on the actual phone, with the actual model and
   the actual `PLATRIXA_SLOT_MAX`, until it reports a measured aggregate
   jobs/hour.
2. Feed that number back:
   ```bash
   python3 scripts/platrixa_worker_benchmark.py \
     --device-jobs-per-hour <measured> --device-label "Pixel 7 / Qwen2.5-3B-Q4 / 512 tokens"
   ```
   The report then applies a **30% safety margin** and marks the result
   `quotable: true`. That haircut covers thermal drift, battery saver, Wi-Fi
   jitter, and the fact that somebody is using the phone.
3. Re-measure after any model, quantization, context-length or Android change.
   A capacity number without its measurement date is a rumour.

With `PLATRIXA_SLOT_MAX=1` the phone generates one job at a time. Raising it is
a measurement decision, never a tuning guess.

---

## 6. Incidents

| Symptom | First check | Do |
| --- | --- | --- |
| Jobs stuck `QUEUED` | phone service status, model readiness | restart the instance; a late model start resolves itself (the worker gates every claim) |
| `consistent: false` | `terminal_still_held` / `pending_already_released` | stop admissions, inspect the affected job, then fix the row with a reviewed script — never by hand-editing capacity |
| `429 QUEUE_FULL` for one tenant | per-tenant pending count | the bound is protecting the queue; do not raise it to silence a looping client |
| `429 TENANT_RATE_LIMIT` | `Retry-After` | the durable counter is per tenant per window; a client ignoring `Retry-After` is a client bug |
| Worker exits 78 | log line names the missing setting | fix the env/key file; `finish` backs off so it will not hot-loop |
| Suspected key leak | `docs/KEY_ROTATION.md` | revoke first, then rotate |
| Database loss | newest backup manifest | restore into a clean database, then verify a job reaches `COMPLETED` |

---

## 7. Inviting a developer

1. Provision the tenant (one call, returns the raw key once):
   `POST /v1/developer/tenants`.
2. Send the key **out of band**; the developer writes it to
   `~/.platrixa/worker-<instance>.key` with mode `600`.
3. Install and start the instance on the phone:
   `sh device/termux/install-termux-service.sh --instance <name> --start`.
4. Confirm one real document from their tooling reaches a verdict they accept.
5. Record: tenant id, instance name, who holds the key, date.

## 8. Honest limits of this beta

* One phone, one model, one generation at a time by default.
* The Android model app has no boot receiver: after a reboot it is started by
  hand. Workers wait for it instead of failing jobs.
* No hosting SLA, no paging, no automated backup schedule.
* Capacity for these developers is unmeasured until §5 is done on the device.
