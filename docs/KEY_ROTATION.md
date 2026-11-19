# Platrixa — API key rotation (M13)

**When to rotate:** a key was pasted into a chat/ticket/log; a laptop was lost;
an invited developer leaves the beta; or on a fixed schedule (90 days).

Rotation is **create-new → deploy-new → revoke-old**. Never the reverse: revoking
first produces a window in which the developer's jobs are rejected.

## 0. Decide the blast radius

A key belongs to exactly one tenant, and one tenant may have several keys (one
per machine/instance). Rotate the **smallest** credential that was exposed.

| Exposed | Action |
| --- | --- |
| One device's key | rotate that key; other keys of the tenant keep working |
| The tenant's only key | rotate it; the device must be updated in the same window |
| The owner/management token | out of scope here — rotate it in the platform secret store, then re-deploy |

## 1. Create the replacement (hosted)

Token-authenticated developer endpoint:

```bash
curl -sS -X POST "$PLATRIXA_API/v1/developer/api-keys/$KEY_ID/rotate" \
  -H "Authorization: Bearer $PLATRIXA_DEVELOPER_TOKEN" \
  -H "Content-Type: application/json"
```

The response contains the **raw key once**. It is not retrievable later; the
store keeps only a hash and a prefix. If the response is lost, rotate again.

For an operator provisioning on a developer's behalf, the tenant-scoped route
requires the management token and the tenant id:

```bash
curl -sS -X POST "$PLATRIXA_API/v1/developer/tenants/$TENANT_ID/keys/$KEY_ID/revoke" \
  -H "X-Platrixa-Management-Token: $PLATRIXA_MANAGEMENT_TOKEN"
```

> The raw key is a secret. It goes into the developer's own key file. It does
> not go into this document, a chat message, a ticket, a commit, or an env file
> that is world-readable.

## 2. Install the replacement on the device

Per instance, on the phone (Termux):

```bash
umask 077
printf '%s' 'plx_...' > ~/.platrixa/worker-<instance>.key     # never echo it back
chmod 600 ~/.platrixa/worker-<instance>.key
sv restart platrixa-worker-<instance>
```

The run script refuses a key file whose mode is not `600`/`400` (exit 78), so a
mistake here fails loudly instead of leaking.

## 3. Verify before revoking

1. The new key is the one in use:
   `sv status platrixa-worker-<instance>` is `run`, and the log shows a
   successful claim with the instance's `--worker-id`.
2. Submit one known-good job from the developer's own tooling and watch it reach
   `COMPLETED` (or `FAILED` for a deliberately bad input — either proves the
   credential authenticates).
3. `GET /v1/developer/api-keys` lists the new key with a recent `last_used_at`
   and the old key with none.

Only then revoke the old one:

```bash
curl -sS -X POST "$PLATRIXA_API/v1/developer/api-keys/$OLD_KEY_ID/revoke" \
  -H "Authorization: Bearer $PLATRIXA_DEVELOPER_TOKEN"
```

## 4. Revoke-first exception (credential compromise)

If the key is believed to be in hostile hands, revoke immediately and accept the
outage. A stolen key can read the tenant's own results and burn its quota; a
short outage is cheaper. The device then fails closed with `401` (never a silent
retry storm), and capacity returns to the queue.

## 5. Offboarding a developer

1. `POST /v1/developer/api-keys/{key_id}/revoke` for every key of the tenant.
2. Do **not** delete the tenant or its history: accounting must remain
   reproducible. Retention (`scripts/platrixa_retention.py`) ages out terminal
   jobs on its normal schedule.
3. Remove the phone's key file and the service directory:
   `rm ~/.platrixa/worker-<instance>.key && rm -rf "$PREFIX/var/service/platrixa-worker-<instance>"`.

## 6. What is NOT covered

* There is no self-service rotation UI; rotation is a CLI/API procedure run by
  the owner or the developer.
* There is no automatic expiry. Rotation is a calendar task until a scheduler
  exists.
* Rate-limit and quota counters are keyed by **tenant**, so rotation does not
  reset a tenant's usage. That is intentional: rotation must not become a
  quota-reset trick.
