# Tracked secret remediation — rotation plan

Status: **PREPARED ONLY. Nothing in this document has been executed.**
No credential was rotated, no file was untracked, and no Git history was
rewritten. Items marked **[OWNER AUTHORIZATION]** require an explicit decision
from the repository owner before they may be performed.

## 1. Exposure inventory

Four live secrets are committed in the tracked file `.env` at the repository
root. (`.gitignore` already lists `.env`, but the file was committed before that
rule existed, so ignoring it has no effect on already-tracked files.)

| # | Variable | What it authenticates | Exposure class | Where else it is read |
|---|---|---|---|---|
| 1 | `DATABASE_URL` | The PostgreSQL instance (`postgres` role, `railway` database) over the **public** Railway TCP proxy, `sslmode=require` | **Critical** — a public, directly reachable database credential | `backend/database/db.py` (`load_dotenv()`), all metering/queue code |
| 2 | `FMP_API_KEY` | Financial Modeling Prep | Medium — billable third-party quota | `backend/module4/provider/fmp_adapter.py`, `backend/module4/key_manager.py` |
| 3 | `ALPHA_VANTAGE_API_KEY` | Alpha Vantage | Medium — billable third-party quota | `backend/module4/provider/alpha_vantage_adapter.py`, `key_manager.py` |
| 4 | `FINNHUB_API_KEY` | Finnhub | Medium — billable third-party quota | `backend/module4/provider/finnhub_adapter.py`, `key_manager.py` |

Facts established by inspection (no secret value is reproduced here):

- `.env` is tracked; the values are real, not placeholders (lengths 16–106, no
  angle-bracket placeholders).
- The committed `DATABASE_URL` points at the production Railway Postgres
  instance through its public TCP proxy — i.e. anyone with the file has a
  working connection string today.
- The Railway `platrixa-api` service carries **no** `FMP_API_KEY`,
  `ALPHA_VANTAGE_API_KEY` or `FINNHUB_API_KEY` variable, so the deployed
  container currently obtains those three keys from this committed `.env`
  (`load_dotenv()` runs with `override=False`, so a Railway variable would take
  precedence once one exists).
- `.env.local` is also tracked but contains only PostHog public-frontend
  placeholders; it is not part of this remediation.
- These values are also in Git history (introduced/updated in commits
  `102784b`, `d627b67` and successors), so deleting the file now would not
  remove them from history.

## 2. Order of operations (non-negotiable)

1. **Rotate** every credential at its source of truth.
2. **Verify** the running service still works on the new values.
3. **Untrack** `.env` and commit that removal.
4. **Purge** the values from Git history (separate authorization + coordinated
   force-push).
5. **Confirm** the portal/secret-scanning backlog is clean.

Purging history before rotating achieves nothing: the old values remain valid
and the exposure window simply continues. Rotation before purge is what
actually ends the exposure.

## 3. Rotation steps

### 3.1 Provider API keys (`FMP_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`) — [OWNER AUTHORIZATION to rotate]

These must be rotated in each provider's own dashboard; the repository cannot
do it.

1. In each provider account, create a **new** key (do not delete the old one
   yet).
2. Set the new value as an encrypted Railway variable on the `platrixa-api`
   service (`FMP_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`) with
   `skipDeploys=true`, so production keeps running the pinned build.
3. Redeploy the pinned release commit (`30b97d8`) with
   `serviceInstanceDeploy(commitSha: "30b97d8")`. Railway variable writes
   otherwise queue their own redeploy of `main`; cancel any such deployment
   rather than letting it replace the release.
4. Verify with the masked provider status route (names and configured/not
   configured only):
   `curl -sS -H "X-Platrixa-API-Key: <operator key>" "$BASE/v1/providers/status"`.
5. Exercise one live market read (`GET /v1/market/<TICKER>/price`) to confirm
   the new key is accepted.
6. **Only then** delete the old key in the provider dashboard.

Rollback: keep the previous key active until step 5 passes; if a provider
rejects the new key, restore the old value in Railway and redeploy.

### 3.2 `DATABASE_URL` (PostgreSQL) — [OWNER AUTHORIZATION to rotate]

The `postgres` role has exactly one password, so the cutover is a single
coordinated change; plan a short window.

1. Generate a new high-entropy password (32+ characters) with a password
   manager — never with `openssl rand` output pasted into a ticket or chat.
2. If `Postgres.DATABASE_URL` / `PGPASSWORD` use Railway reference variables
   (`${{Postgres.POSTGRES_PASSWORD}}`), update `POSTGRES_PASSWORD` on the
   Postgres service with `skipDeploys=true`; dependent services resolve the new
   value on their next deploy. If they contain literal values, update each
   metadata service's `DATABASE_URL`/`DATABASE_PUBLIC_URL` explicitly.
3. Apply the role change inside the database:
   `ALTER ROLE postgres WITH PASSWORD '<new value>';`
   (run in the Railway service shell or over the private network — never paste
   the password into a shared terminal transcript).
4. Redeploy the pinned release (`30b97d8`) and verify:
   `/v1/health`, `/v1/ready`, and one metered read such as `/v1/usage` (which
   proves the metering store connection), plus `/v1/owner/usage` for the queue
   and quota tables.
5. Decide the fate of the public TCP proxy: it exists only so external clients
   can reach Postgres. If nothing outside Railway needs it, delete the TCP
   proxy (`tcpProxyDelete`) so the database is reachable only on the private
   network. If it must stay, treat the connection string as a standing secret.
6. Confirm no service is still serving requests on the old password (Railway
   logs: authentication failures would appear as connection errors).

Rollback: `ALTER ROLE ... PASSWORD` is reversible only by setting the old value
again, which reintroduces the exposed credential — therefore the correct
rollback is to fix forward with a third password, not to restore the old one.

### 3.3 Untrack `.env` — [OWNER AUTHORIZATION to commit]

Only after 3.1 and 3.2 verify green:

```bash
git rm --cached .env .env.local      # stop tracking; keep local copies
printf '\n# never commit runtime secrets\n.env\n.env.local\n' >> .gitignore
git commit -m "chore(secrets): untrack environment files"
```

Preconditions:

- Every value in `.env` that production needs must already exist as a Railway
  (or Pages) variable, because the file stops being the source of truth.
- `.env.example` must stay complete and placeholder-only (it currently lists
  all four variables).
- After the commit, redeploy the pinned release and re-run the verification in
  3.1/3.2 step 4. A container that silently loses a key fails at request time,
  not at boot.

### 3.4 Purge Git history — **[OWNER AUTHORIZATION: separate, explicit, force-push]**

Not performed, and must not be performed unilaterally: it rewrites every
commit, invalidates every existing clone/fork/PR ref, and requires a
force-push.

Recommended procedure when authorized:

1. Freeze pushes; announce the rewrite window.
2. Mirror-clone the repository.
3. Run `git filter-repo --path .env --path .env.local --invert-paths --sensitive-data-removal`
   (BFG equivalent: `bfg --delete-files .env`), then verify with
   `git log --all --oneline -- .env` returning nothing and
   `git rev-list --objects --all | grep -i '\.env$'` returning nothing.
4. Force-push **all** branches and tags (this is the only authorised
   force-push), then ask every collaborator to re-clone.
5. Ask GitHub Support to purge cached views/forks of the old objects; delete
   forks the project does not control.
6. Re-run step 3.1/3.2 verification against the rewritten branch (contents are
   unchanged apart from the removed file, so the pinned SHA will change —
   record the new release SHA).
7. Treat the exposed values as compromised regardless: if any provider issues
   an audit log (FMP, Alpha Vantage, Finnhub) or the database shows unknown
   clients, rotate again.

## 4. Verification checklist (after execution)

- [ ] `/v1/providers/status` reports FMP, Alpha Vantage and Finnhub configured.
- [ ] One market read succeeds with each provider path.
- [ ] `/v1/health`, `/v1/ready`, `/v1/usage`, `/v1/owner/usage` all answer 200.
- [ ] Railway deploy of the pinned release is `SUCCESS` with the expected SHA.
- [ ] `git ls-files | grep -E '(^|/)\.env'` lists only `.env.example`.
- [ ] `git log --all --oneline -- .env` is empty (post-purge only).
- [ ] No plaintext secret in tracked files (`gitleaks detect --no-git` clean).

## 5. What this document deliberately does not do

- It does not rotate anything: rotation happens in four external dashboards.
- It does not untrack or delete `.env`, and does not touch history.
- It does not print, copy or store any secret value.
- It does not create provider accounts or new database users.

## 6. Residual risk statement

Until steps 3.1–3.3 are completed, the four values in the tracked `.env` remain
valid and readable by anyone with repository access, including its history.
The database credential is the most urgent: it grants direct access to the
metering store that holds tenant API-key hashes, jobs and results.
