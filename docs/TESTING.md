# Running the Test Suites

The repository has two kinds of test:

| Kind | Where | Runner |
|---|---|---|
| Deterministic engine / calculation tests | `tests/` | `pytest` |
| Evidence suites (phase-numbered, standalone) | `scripts/fte_fyjc_*_test.py` | plain `python3 script.py` — each prints `[PASS]`/`[FAIL]` lines and **exits non-zero on failure** |

The evidence suites are the ones that guard the HTTP surface, admission, readiness and the async
queue. They are ordinary scripts, not pytest files, so they can be run individually.

Because they define no `test_*` functions, `pytest scripts/fte_fyjc_9*_test.py` collects **zero
items and exits 5**. That is not a pass: run them with `.venv/bin/python <script>`, which prints
`[PASS]`/`[FAIL]` per check and exits non-zero on any failure. (`scripts/platrixa_worker_fatal_report_test.py`
is the exception — it defines real `test_*` functions and is pytest-collectable.)

## Environment

Several suites import `fastapi`, `httpx`, `psycopg2` and `pgserver`. The system interpreter is not
guaranteed to have them, and the suites fail at import time (`ModuleNotFoundError: No module named
'fastapi'`) rather than skipping — which reads as a failure. Run them inside the project virtual
environment instead:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`.venv/` is gitignored, so this is a local, disposable environment. `requirements.txt` is the only
manifest needed; the suite dependencies (`fastapi`, `httpx`, `psycopg2-binary`, `pgserver`,
`pytest`) are all declared there. Do not add test-only packages to a production manifest to make
local runs work.

## Suites relevant to readiness and admission

```bash
.venv/bin/python scripts/fte_fyjc_90_readiness_probe_test.py     # readiness probe (measured availability)
.venv/bin/python scripts/fte_fyjc_91_readiness_deadline_test.py  # pool/connect/statement bounds + deadline
.venv/bin/python scripts/fte_fyjc_94_admission_timeout_scope_test.py  # probe-only statement bound, locked row, pool saturation
.venv/bin/python scripts/fte_fyjc_95_deadline_semantics_test.py  # budget arithmetic, verdict before cleanup, overrun fails closed
.venv/bin/python scripts/fte_fyjc_85_phase5i_admission_edge_test.py   # admission boundary / readiness semantics
.venv/bin/python scripts/fte_fyjc_62_hosted_api_boundary_test.py      # hosted API boundary + readiness
```

Of the readiness suites, `fte_fyjc_90` is the only one that runs without a PostgreSQL driver. Its
section H performs real local socket checks (a refused connection, and a server that accepts TCP but
never answers the PostgreSQL startup packet) and reports `[SKIP]` — never a pass — when `psycopg2` is
absent. A skipped check is not a passing check.

### Worker suites

```bash
.venv/bin/python scripts/platrixa_outbound_inference_worker_test.py   # Stage-2 worker contract
.venv/bin/python scripts/platrixa_worker_claim_outcome_logging_test.py # claim diagnostics
.venv/bin/python scripts/platrixa_worker_cli_flags_test.py            # CLI flag wiring
.venv/bin/python -m pytest scripts/platrixa_worker_fatal_report_test.py -q  # exit 3 + run report
```

## P1–P4 remediation validation (one command)

The three suites added for metering-failure logging, readiness logging and fatal
claim reporting are scripts (all but the last are not pytest-collectable).
Run them chained — no installs, no new wrapper, using the project `.venv`:

```bash
.venv/bin/python scripts/fte_fyjc_92_metering_failure_logging_test.py && \
.venv/bin/python scripts/fte_fyjc_93_readiness_logging_test.py && \
.venv/bin/python -m pytest scripts/platrixa_worker_fatal_report_test.py -q
```

The chain exits `0` only when every suite passes; a single `[FAIL]` (or a pytest
failure) stops it with a non-zero status.

## Notes

- Suites that exercise the metered gate neutralize or stub `PLATRIXA_METERING_DATABASE_URL` in
  process; a developer `.env` at the repository root is loaded by `backend/database/db.py` and can
  otherwise change the admission mode under test.
- `backend/auth/admission.py` caches **failed** readiness verdicts for a short TTL
  (`PLATRIXA_READINESS_CACHE_TTL_SECONDS`, default 2 s; 0 disables). Successful verdicts are
  never cached. Tests that change the store between assertions must call
  `admission.reset_readiness_cache()`, otherwise a cached failure legitimately answers the next call.
- The 1500 ms `statement_timeout` is applied **per probe transaction**
  (`gate._apply_probe_statement_timeout`), not on the shared engine. Assertions about it must run on a
  probe session; a plain `engine.connect()` carries the server default (`0`).
- Nothing here requires a network connection beyond the local loopback.
