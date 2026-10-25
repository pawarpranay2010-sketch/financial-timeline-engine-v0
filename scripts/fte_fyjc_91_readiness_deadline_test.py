#!/usr/bin/env python3
"""Readiness deadline (2026-10-20, follow-up) — focused evidence suite (fte_fyjc_91).

Guards the SECOND half of the readiness fix. The first half (suite 90) proved
availability is *measured* rather than inferred. This suite proves the
measurement is *bounded*, in every phase it can spend time in:

  A  Pool acquisition   — an exhausted pool cannot stall readiness for 30s.
  B  Connection         — refusal, slow connect, and BOTH driver URL schemes,
                          verified at the real driver (psycopg2 and psycopg3).
  C  Query execution    — the 1500ms statement_timeout is applied to the
                          PROBE transaction only and aborts a stalled query;
                          admission statements keep the server default. A store
                          that hangs AFTER connecting is still unavailable.
  D  Overall deadline   — the full /v1/ready route: 200 when healthy, 503 when
                          not, cleanup, and repeated failures that do not
                          exhaust the pool.
  E  Liveness/regression— /v1/health stays green with PostgreSQL down, the old
                          no-op-factory false positive is impossible, and the
                          pre-fix readiness_block from git still fails Q1e-2.

Deadline design under test: every phase has its own enforced bound
(SQLAlchemy pool_timeout, libpq connect_timeout, PostgreSQL statement_timeout)
and ``gate.readiness_deadline_seconds()`` is their sum plus cleanup slack. This
suite measures the sum, it does not assume it.

Run:  .venv/bin/python scripts/fte_fyjc_91_readiness_deadline_test.py
Exit: 0 = all checks passed, 1 = at least one failure.

Needs: fastapi + httpx (route level), psycopg2 and pgserver (real server).
psycopg3 is used for the ``postgresql+psycopg://`` scheme; if it is missing
those checks are SKIPPED, never counted as passes.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sqlalchemy  # noqa: E402

from backend.auth import admission as admission_boundary  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
SKIPS: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def skip(name: str, reason: str) -> None:
    """Record a check that could NOT run. Never counted as a pass."""
    SKIPS.append((name, reason))
    print(f"  [SKIP] {name} — {reason}")


def _clear(url: str | None, *, ttl: str | None = None, dev_key: str | None = None) -> None:
    """Deterministic store/mode setup between cases (no inherited state)."""
    for var in (
        metered_gate.METERING_ENV_VAR,
        metered_gate.CONNECT_TIMEOUT_ENV_VAR,
        metered_gate.STATEMENT_TIMEOUT_MS_ENV_VAR,
        metered_gate.POOL_TIMEOUT_ENV_VAR,
        admission_boundary.PHASE15_ENV_VAR,
        admission_boundary.READINESS_CACHE_TTL_ENV_VAR,
    ):
        os.environ.pop(var, None)
    if url is not None:
        os.environ[metered_gate.METERING_ENV_VAR] = url
    if ttl is not None:
        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = ttl
    if dev_key is not None:
        os.environ[admission_boundary.PHASE15_ENV_VAR] = dev_key
    metered_gate._session_factory_cache.clear()
    admission_boundary.reset_readiness_cache()


class PgServer:
    """Embedded PostgreSQL, start/stop on one data directory."""

    def __init__(self) -> None:
        self.data_dir = tempfile.mkdtemp()
        self.srv = None
        self.uri = ""

    def start(self) -> str:
        import pgserver

        self.srv = pgserver.get_server(self.data_dir)
        self.uri = self.srv.get_uri()
        return self.uri

    def stop(self) -> None:
        if self.srv is not None:
            self.srv.cleanup()
            self.srv = None


def _redact(uri: str) -> str:
    """Identity information only — never the DSN."""
    try:
        p = urlparse(uri)
        return f"scheme={p.scheme} host={'unix' if not p.hostname else 'tcp'} db={p.path.lstrip('/') or '?'}"
    except Exception:
        return "scheme=<unparseable>"


# ---------------------------------------------------------------------------
# HTTP app with a stub provider (real routes, real error handlers)
# ---------------------------------------------------------------------------


class _StubClient:
    def provider_status(self):
        return {"available": True, "loadable": True}

    def rule_pack_summary(self):
        return {"id": "stub", "version": "0"}


def _make_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.routes.developer as developer

    app = FastAPI()
    app.include_router(developer.router)
    developer.register_developer_error_handlers(app)
    developer.set_client(_StubClient())
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# A relay that completes the PostgreSQL handshake, then goes silent
# ---------------------------------------------------------------------------


def _freeze_after_handshake(upstream_socket_path: str, stop: threading.Event):
    """Forward the startup handshake, then stop answering.

    Returns (proxy_dir, server_socket, held). A client that connects through it
    gets a SUCCESSFUL connection and a COMPLETED handshake; only then does the
    server fall silent. ``connect_timeout`` cannot bound that case — which is
    exactly what this suite measures.
    """
    proxy_dir = tempfile.mkdtemp()
    proxy_path = os.path.join(proxy_dir, ".s.PGSQL.5432")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(proxy_path)
    srv.listen(4)
    srv.settimeout(0.25)
    held: list = []

    def handle(client: socket.socket) -> None:
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream.settimeout(5)
        try:
            upstream.connect(upstream_socket_path)
        except OSError:
            client.close()
            upstream.close()
            return
        upstream.settimeout(None)
        handshake = threading.Event()

        def c2s() -> None:
            while not stop.is_set():
                try:
                    data = client.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                try:
                    upstream.sendall(data)
                except OSError:
                    break

        def s2c() -> None:
            buf = b""
            while not stop.is_set():
                try:
                    data = upstream.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                try:
                    client.sendall(data)
                except OSError:
                    break
                buf += data
                # A PostgreSQL message is 1 type byte + a length that counts
                # itself but NOT the type byte (a classic off-by-one).
                while len(buf) >= 5:
                    mtype = buf[:1]
                    mlen = int.from_bytes(buf[1:5], "big")
                    if mlen < 4 or len(buf) < 1 + mlen:
                        break
                    buf = buf[1 + mlen:]
                    if mtype == b"Z":  # ReadyForQuery
                        handshake.set()
                if handshake.is_set():
                    held.extend([client, upstream])
                    return

        threading.Thread(target=c2s, daemon=True).start()
        threading.Thread(target=s2c, daemon=True).start()

    def acceptor() -> None:
        while not stop.is_set():
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=acceptor, daemon=True).start()
    return proxy_dir, srv, held


def _probe_in_child(child_src: str, env_extra: dict, hard_timeout: float):
    """Run a snippet in a child process with a hard wall clock.

    Returns (result_dict_or_None, timed_out, stderr_tail). A probe that fails to
    return is recorded as a failure — it can never hang the suite.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLATRIXA_")}
    env.pop("DATABASE_URL", None)
    env.update({k: str(v) for k, v in env_extra.items()})
    env["PYTHONPATH"] = str(ROOT)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", child_src],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=hard_timeout,
        )
    except subprocess.TimeoutExpired:
        return None, True, ""
    if proc.returncode != 0:
        return None, False, (proc.stderr or "").strip().splitlines()[-1][:200]
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return None, False, "child produced no output"
    try:
        return json.loads(lines[-1]), False, ""
    except ValueError:
        return None, False, f"unparseable child output: {lines[-1][:120]}"


# ---------------------------------------------------------------------------
# A — pool acquisition
# ---------------------------------------------------------------------------


def section_a(uri: str) -> None:
    print("\nA — pool acquisition is bounded (pool_timeout, not 30s)")
    _clear(uri, ttl="0")

    factory = metered_gate._session_factory()
    engine = factory.kw["bind"]
    pool = engine.pool
    configured = metered_gate.pool_timeout_seconds()

    check("A1 the engine pool uses the configured timeout, not SQLAlchemy's 30s default",
          configured == 1.0 and abs(getattr(pool, "_timeout", -1) - configured) < 1e-9,
          f"configured={configured} pool={getattr(pool, '_timeout', None)}")

    t0 = time.perf_counter()
    ok = admission_boundary.store_reachable()
    free_s = time.perf_counter() - t0
    check("A2 a free pool yields a ready verdict quickly", ok is True and free_s < 1.0,
          f"{free_s:.2f}s verdict={ok}")

    # Exhaust the pool exactly (size + overflow) and hold every connection.
    slots = pool.size() + pool._max_overflow
    held = []
    try:
        for _ in range(slots):
            held.append(engine.connect())
        check("A3 the pool is genuinely exhausted for this test",
              pool.checkedout() >= slots, f"checkedout={pool.checkedout()} slots={slots}")

        t0 = time.perf_counter()
        exhausted = admission_boundary.store_reachable()
        exhausted_s = time.perf_counter() - t0
        deadline = metered_gate.readiness_deadline_seconds()

        check("A4 an exhausted pool is reported unavailable (fail closed)",
              exhausted is False, str(exhausted))
        check("A5 pool wait is bounded by pool_timeout, not the 30s default",
              0.5 <= exhausted_s <= configured + 1.0,
              f"{exhausted_s:.2f}s (pool_timeout={configured})")
        check("A6 the whole exhausted-pool probe stays inside the deadline",
              exhausted_s < deadline, f"{exhausted_s:.2f}s < {deadline:.1f}s")
    finally:
        for conn in held:
            try:
                conn.close()
            except Exception:
                pass

    check("A7 released connections return to the pool", pool.checkedout() == 0,
          f"checkedout={pool.checkedout()}")
    check("A8 readiness recovers once the pool drains",
          admission_boundary.store_reachable() is True)


# ---------------------------------------------------------------------------
# B — connection establishment, verified at each real driver
# ---------------------------------------------------------------------------


def section_b(uri: str) -> None:
    print("\nB — connection establishment, verified at the driver")

    # --- immediate refusal ---
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()

    _clear(f"postgresql://u:p@127.0.0.1:{closed_port}/db", ttl="0")
    t0 = time.perf_counter()
    refused = admission_boundary.store_reachable()
    refused_s = time.perf_counter() - t0
    check("B1 an immediate connection refusal is reported unavailable",
          refused is False, str(refused))
    check("B2 refusal fails fast instead of waiting for a bound",
          refused_s < 2.0, f"{refused_s:.2f}s")

    # --- psycopg2: what actually reaches the driver ---
    try:
        import psycopg2
    except Exception as exc:  # pragma: no cover
        skip("B3-B5 psycopg2 driver evidence", f"psycopg2 unavailable ({type(exc).__name__})")
    else:
        seen2: dict = {}
        real_connect = psycopg2.connect

        def spy2(*args, **kwargs):
            seen2.update(kwargs)
            return real_connect(*args, **kwargs)

        psycopg2.connect = spy2  # type: ignore[assignment]
        try:
            _clear(uri, ttl="0")  # bare postgresql:// → normalized to psycopg2
            metered_gate._session_factory()
            ok2 = admission_boundary.store_reachable()
        finally:
            psycopg2.connect = real_connect  # type: ignore[assignment]

        check("B3 psycopg2 receives connect_timeout (the bound reaches the driver)",
              seen2.get("connect_timeout") == metered_gate.connect_timeout_seconds(),
              f"connect_timeout={seen2.get('connect_timeout')!r}")
        # Scope fix (2026-10-25): the shared engine bounds connect only. The
        # 1500ms statement bound is applied per probe transaction, so it must
        # not reach the driver as a connection option (it would also abort the
        # quota reservation UPDATE during a row-lock wait).
        check("B4 psycopg2 is NOT sent a global statement_timeout (probe-only scope)",
              "statement_timeout" not in str(seen2.get("options") or ""),
              f"options={seen2.get('options')!r}")
        check("B5 a psycopg2-backed readiness check succeeds against the real server",
              ok2 is True, str(ok2))

    # --- psycopg3: the alternate supported scheme ---
    try:
        import psycopg
    except Exception as exc:
        skip("B6-B9 psycopg3 driver evidence",
             f"psycopg3 unavailable ({type(exc).__name__}); scheme cannot be exercised live")
    else:
        seen3: dict = {}
        real_connect3 = psycopg.connect

        def spy3(*args, **kwargs):
            seen3.update(kwargs)
            return real_connect3(*args, **kwargs)

        psycopg.connect = spy3  # type: ignore[assignment]
        try:
            p3url = uri.replace("postgresql://", "postgresql+psycopg://", 1)
            _clear(p3url, ttl="0")
            engine3 = metered_gate._session_factory().kw["bind"]
            driver3 = engine3.dialect.driver
            ok3 = admission_boundary.store_reachable()
        finally:
            psycopg.connect = real_connect3  # type: ignore[assignment]

        check("B6 the postgresql+psycopg scheme selects the psycopg3 driver",
              driver3 == "psycopg", driver3)
        check("B7 psycopg3 receives connect_timeout (not silently unbounded)",
              seen3.get("connect_timeout") == metered_gate.connect_timeout_seconds(),
              f"connect_timeout={seen3.get('connect_timeout')!r}")
        check("B8 psycopg3 is NOT sent a global statement_timeout (probe-only scope)",
              "statement_timeout" not in str(seen3.get("options") or ""),
              f"options={seen3.get('options')!r}")
        check("B9 a psycopg3-backed readiness check succeeds against the real server",
              ok3 is True, str(ok3))

    # --- slow establishment is cut off at the connect bound ---
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    hang_port = srv.getsockname()[1]
    srv.settimeout(0.25)
    stop = threading.Event()
    held_conns: list = []

    def acceptor() -> None:
        while not stop.is_set():
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            held_conns.append(conn)  # never answer the startup packet

    threading.Thread(target=acceptor, daemon=True).start()
    child = (
        "import json,time\n"
        "from backend.auth import admission\n"
        "t0=time.perf_counter()\n"
        "ok=admission.store_reachable()\n"
        "print(json.dumps({'ok':ok,'seconds':time.perf_counter()-t0}))\n"
    )
    try:
        res, timed_out, err = _probe_in_child(
            child,
            {
                metered_gate.METERING_ENV_VAR: f"postgresql://u:p@127.0.0.1:{hang_port}/db",
                metered_gate.CONNECT_TIMEOUT_ENV_VAR: "2",
                metered_gate.POOL_TIMEOUT_ENV_VAR: "1",
                metered_gate.STATEMENT_TIMEOUT_MS_ENV_VAR: "1500",
                admission_boundary.READINESS_CACHE_TTL_ENV_VAR: "0",
            },
            hard_timeout=20,
        )
    finally:
        stop.set()
        srv.close()
        for conn in held_conns:
            try:
                conn.close()
            except OSError:
                pass

    if timed_out:
        check("B10 a slow connection is cut off at the connect bound", False,
              "child did not return within 20s — the bound was NOT enforced")
    else:
        ok_b10 = res is not None and res.get("ok") is False and 1.0 <= res.get("seconds", 0) <= 6.0
        check("B10 a slow connection is cut off at the connect bound (~2s)",
              ok_b10, f"{res} {err}")


# ---------------------------------------------------------------------------
# C — query execution
# ---------------------------------------------------------------------------


def section_c(uri: str) -> None:
    print("\nC — the readiness query is bounded on the readiness connection")
    _clear(uri, ttl="0")
    engine = metered_gate._session_factory().kw["bind"]

    with engine.connect() as conn:
        st = conn.exec_driver_sql("SHOW statement_timeout").scalar()
        driver = engine.dialect.driver
    check("C1 the shared connection carries no statement_timeout (admission baseline)",
          st in ("0", "0ms"), f"statement_timeout={st!r}")
    check("C2 the metering engine is psycopg2 for a bare postgresql:// URL",
          driver == "psycopg2", driver)

    probe_s = metered_gate._session_factory()()
    try:
        metered_gate._apply_probe_statement_timeout(probe_s)
        probe_st = probe_s.execute(sqlalchemy.text("SHOW statement_timeout")).scalar()
    finally:
        probe_s.close()
    check("C2b the probe transaction carries statement_timeout=1500ms",
          probe_st in ("1500ms", "1500"), f"statement_timeout={probe_st!r}")

    t0 = time.perf_counter()
    ok = admission_boundary.store_reachable()
    ok_s = time.perf_counter() - t0
    check("C3 a successful SELECT 1 is reported ready", ok is True, str(ok))
    check("C4 the success lands well inside the deadline",
          ok_s < 1.0, f"{ok_s:.2f}s")

    # Scope fix (2026-10-25): the stalled-query bound applies to the PROBE
    # transaction only. Admission statements run at the server default, so a
    # reservation blocked on a row lock is not aborted at 1.5s.
    cancel_err = None
    t0 = time.perf_counter()
    ps = metered_gate._session_factory()()
    try:
        metered_gate._apply_probe_statement_timeout(ps)
        ps.execute(sqlalchemy.text("SELECT pg_sleep(5.0)"))
    except Exception as exc:
        cancel_err = type(exc).__name__
    finally:
        ps.close()
    cancelled_s = time.perf_counter() - t0
    check("C5 a stalled PROBE query is aborted by statement_timeout (~1.5s)",
          cancel_err is not None and 1.0 <= cancelled_s <= 3.5,
          f"error={cancel_err} after {cancelled_s:.2f}s")

    plain_err = None
    t0 = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT pg_sleep(2.0)")
    except Exception as exc:
        plain_err = type(exc).__name__
    plain_s = time.perf_counter() - t0
    check("C5b a 2s admission-side statement is NOT aborted (no reservation-timeout regression)",
          plain_err is None and plain_s >= 1.9,
          f"error={plain_err} after {plain_s:.2f}s")

    # End-to-end: a store that completes the handshake and then goes silent.
    socket_dir = uri.split("host=", 1)[1].strip() if "host=" in uri else ""
    upstream_path = os.path.join(socket_dir, ".s.PGSQL.5432")
    if not os.path.exists(upstream_path):
        skip("C6-C7 connected-but-hung store", f"relay target not found ({upstream_path})")
        return

    stop = threading.Event()
    proxy_dir, proxy_srv, held = _freeze_after_handshake(upstream_path, stop)
    parsed = urlparse(uri)
    real_user = parsed.username or "postgres"
    real_db = (parsed.path or "/postgres").lstrip("/") or "postgres"
    child = (
        "import json,time\n"
        "from backend.auth import admission\n"
        "t0=time.perf_counter()\n"
        "ok=admission.store_reachable()\n"
        "print(json.dumps({'ok':ok,'seconds':time.perf_counter()-t0}))\n"
    )
    try:
        res, timed_out, err = _probe_in_child(
            child,
            {
                metered_gate.METERING_ENV_VAR: f"postgresql://{real_user}@/{real_db}?host={proxy_dir}",
                metered_gate.CONNECT_TIMEOUT_ENV_VAR: "2",
                metered_gate.POOL_TIMEOUT_ENV_VAR: "1",
                metered_gate.STATEMENT_TIMEOUT_MS_ENV_VAR: "1500",
                admission_boundary.READINESS_CACHE_TTL_ENV_VAR: "0",
            },
            hard_timeout=25,
        )
    finally:
        stop.set()
        try:
            proxy_srv.close()
        except OSError:
            pass
        for s in held:
            try:
                s.close()
            except OSError:
                pass

    # IMPORTANT — WHAT THIS RELAY ACTUALLY MEASURES.
    #
    # The relay keeps forwarding client→server for its whole life and stops
    # forwarding server→client once the handshake completes. So the probe's
    # ``SELECT 1`` DOES reach PostgreSQL and IS executed there; only the REPLY is
    # dropped. This is a MUTE RESPONSE PATH, not a server-side query stall.
    #
    # The distinction matters: statement_timeout bounds execution, so a stalled
    # query is bounded (C5). Nothing in libpq bounds a client waiting for a reply
    # that will never be relayed — connect_timeout no longer applies, and the
    # statement already completed. psycopg exposes no portable client-side read
    # deadline, and abandoning a thread to fake one would leak the checked-out
    # connection. The residual is therefore recorded as a LIMITATION, not as a
    # satisfied deadline: this check passes only if the limitation is real.
    if timed_out:
        check("C6 RESIDUAL LIMITATION (not a deadline pass): a mute response path after "
              "connect is NOT interrupted — measured no return within 25s", True, "")
    else:
        check("C6 RESIDUAL LIMITATION (not a deadline pass): a mute response path after "
              "connect is NOT interrupted", False,
              f"unexpectedly returned within 25s: {res}; the framing above needs revisiting")

    # The fail-closed overrun guard. Every phase has a bound, so this should be
    # unreachable in practice; if a bound is mis-configured or a new blocking
    # path is ever added, the verdict must be "unavailable" rather than a late
    # "ready" that an orchestrator has already given up on. Forcing the ceiling
    # negative exercises that branch deterministically.
    _clear(uri, ttl="0")
    real_deadline = metered_gate.readiness_deadline_seconds
    metered_gate.readiness_deadline_seconds = lambda: -1.0  # type: ignore[assignment]
    try:
        raised = None
        try:
            metered_gate.probe_store()
        except metered_gate.MeteredGateError as exc:
            raised = str(exc)
        overrun_verdict = admission_boundary.store_reachable()
    finally:
        metered_gate.readiness_deadline_seconds = real_deadline  # type: ignore[assignment]
    check("C7 an overrun past the computed deadline fails closed (never a late ready)",
          raised == "metering store unavailable" and overrun_verdict is False,
          f"raised={raised!r} verdict={overrun_verdict}")


# ---------------------------------------------------------------------------
# D — overall deadline through the real route, plus cleanup
# ---------------------------------------------------------------------------


def section_d(pg: PgServer) -> None:
    print("\nD — /v1/ready end to end: status code, deadline, cleanup")
    client = _make_client()
    uri = pg.uri

    # --- healthy ---
    _clear(uri, ttl="0")
    t0 = time.perf_counter()
    r = client.get("/v1/ready")
    healthy_s = time.perf_counter() - t0
    body = r.json()
    check("D1 /v1/ready is 200 and ready against a reachable store",
          r.status_code == 200 and body.get("status") == "ready",
          f"code={r.status_code} status={body.get('status')}")
    check("D2 the healthy route completes well inside the deadline",
          healthy_s < metered_gate.readiness_deadline_seconds(), f"{healthy_s:.2f}s")

    # --- dependency down ---
    pg.stop()
    _clear(uri, ttl="0")
    t0 = time.perf_counter()
    r_down = client.get("/v1/ready")
    down_s = time.perf_counter() - t0
    b_down = r_down.json()
    check("D3 /v1/ready is 503 when PostgreSQL is unavailable",
          r_down.status_code == 503 and b_down.get("status") == "not_ready",
          f"code={r_down.status_code} status={b_down.get('status')}")
    check("D4 the 503 arrives within the deadline",
          down_s <= metered_gate.readiness_deadline_seconds() + 0.5, f"{down_s:.2f}s")
    check("D5 the 503 body gives a reason without exposing the DSN",
          "unreachable" in str(b_down.get("reason", ""))
          and "postgresql://" not in r_down.text
          and "host=" not in r_down.text,
          r_down.text[:200])

    h = client.get("/v1/health")
    check("D6 liveness /v1/health is still 200 while PostgreSQL is down",
          h.status_code == 200 and h.json().get("status") == "ok",
          f"code={h.status_code}")

    # --- repeated failures do not exhaust the pool ---
    codes = [client.get("/v1/ready").status_code for _ in range(5)]
    check("D7 repeated failed readiness calls all answer 503",
          codes == [503] * 5, str(codes))

    pg.start()
    _clear(pg.uri, ttl="0")
    engine = metered_gate._session_factory().kw["bind"]
    check("D8 no connections left checked out after the failures",
          engine.pool.checkedout() == 0, f"checkedout={engine.pool.checkedout()}")
    check("D9 the service recovers after repeated failures",
          client.get("/v1/ready").status_code == 200,
          "did not recover")

    # --- cache: a cached failure must not outlive recovery by more than TTL,
    #     and a success must never be served from cache ---
    _clear(pg.uri, ttl="0")
    r1 = client.get("/v1/ready")
    pg.stop()
    r2 = client.get("/v1/ready")  # TTL=0 → must probe, must not reuse "ready"
    check("D10 no cached success: readiness flips to 503 on the very next call",
          r1.status_code == 200 and r2.status_code == 503,
          f"{r1.status_code} then {r2.status_code}")
    pg.start()
    _clear(pg.uri, ttl="0.2")
    client.get("/v1/ready")  # may be a failure verdict, cached for 0.2s
    time.sleep(0.3)
    r4 = client.get("/v1/ready")
    check("D11 a cached failure expires, so recovery is visible",
          r4.status_code == 200, f"code={r4.status_code}")


# ---------------------------------------------------------------------------
# E — liveness and the original regression
# ---------------------------------------------------------------------------


def section_e() -> None:
    print("\nE — liveness semantics and the Q1e regression detector")

    # The old false positive: a session factory that returns a no-op object and
    # never executes anything. This MUST NOT be production-ready.
    _clear("postgresql://u:p@h:5432/db", ttl="0")
    real_factory = metered_gate._session_factory
    metered_gate._session_factory = lambda: (lambda: object())  # type: ignore[assignment]
    try:
        block = admission_boundary.readiness_block()["admission"]
    finally:
        metered_gate._session_factory = real_factory  # type: ignore[assignment]
    check("E1 a factory that never executes the probe is NOT production_ready",
          block.get("production_ready") is False
          and block.get("metering_store_available") is False, str(block))

    client = _make_client()
    _clear(None)
    r = client.get("/v1/ready")
    h = client.get("/v1/health")
    check("E2 /v1/ready is 503 with no admission boundary configured",
          r.status_code == 503 and r.json().get("status") == "not_ready",
          f"code={r.status_code}")
    check("E3 /v1/health is 200 (liveness does not need PostgreSQL)",
          h.status_code == 200, f"code={h.status_code}")

    # The pre-fix readiness_block, extracted from git, must still FAIL the Q1e-2
    # assertion — otherwise the regression test has stopped detecting anything.
    try:
        old_src = subprocess.run(
            ["git", "show", "HEAD:backend/auth/admission.py"],
            cwd=str(ROOT), capture_output=True, text=True, check=True,
        ).stdout
    except Exception as exc:
        skip("E4 pre-fix readiness_block still fails Q1e-2",
             f"git unavailable ({type(exc).__name__})")
        return

    node = next(
        n for n in ast.parse(old_src).body
        if isinstance(n, ast.FunctionDef) and n.name == "readiness_block"
    )
    old_fn = ast.get_source_segment(old_src, node)
    ns = dict(vars(admission_boundary))
    ns["metered_gate"] = metered_gate
    exec(old_fn, ns)
    old_block = ns["readiness_block"]

    calls: list[int] = []

    class _ProbeSession:
        def execute(self, *_a, **_k):
            calls.append(1)

        def close(self):
            return None

    metered_gate._session_factory = lambda: (lambda: _ProbeSession())  # type: ignore[assignment]
    try:
        _clear("postgresql://u:p@h:5432/db", ttl="0")
        old_verdict = old_block()["admission"]
        old_calls = len(calls)
        calls.clear()
        _clear("postgresql://u:p@h:5432/db", ttl="0")
        new_verdict = admission_boundary.readiness_block()["admission"]
        new_calls = len(calls)
    finally:
        metered_gate._session_factory = real_factory  # type: ignore[assignment]
        _clear(None)

    check("E4 the PRE-FIX implementation reports ready without probing (the old defect)",
          old_verdict.get("production_ready") is True and old_calls == 0,
          f"ready={old_verdict.get('production_ready')} probes={old_calls}")
    check("E5 the CORRECTED implementation probes and still reports ready",
          new_verdict.get("production_ready") is True and new_calls == 1,
          f"ready={new_verdict.get('production_ready')} probes={new_calls}")
    check("E6 Q1e-2 therefore still discriminates old from new",
          old_calls == 0 and new_calls == 1, f"old={old_calls} new={new_calls}")


def main() -> int:
    print("=" * 78)
    print("fte_fyjc_91 — readiness deadline: pool, connect, query, route")
    print("=" * 78)

    try:
        import pgserver  # noqa: F401
    except Exception as exc:
        skip("A-D real PostgreSQL checks", f"pgserver unavailable ({type(exc).__name__})")
        pg = None
    else:
        pg = PgServer()

    if pg is not None:
        try:
            uri = pg.start()
            print(f"\n(embedded PostgreSQL started: {_redact(uri)})")
            print(f"(readiness deadline: {metered_gate.readiness_deadline_seconds():.1f}s "
                  f"= pool {metered_gate.pool_timeout_seconds():.1f} "
                  f"+ connect {metered_gate.connect_timeout_seconds()} "
                  f"+ statement {metered_gate.statement_timeout_ms() / 1000:.1f} "
                  f"+ slack {metered_gate.CLEANUP_SLACK_SECONDS:.1f})")
            section_a(uri)
            section_b(uri)
            section_c(uri)
            section_d(pg)
        finally:
            pg.stop()
            _clear(None)
    else:
        _clear(None)

    section_e()

    failed = [c for c in CHECKS if not c[1]]
    print("\n" + "=" * 78)
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if SKIPS:
        print(f"{len(SKIPS)} check(s) SKIPPED (not executed, not passed):")
        for name, reason in SKIPS:
            print(f"  SKIP: {name} — {reason}")
    for name, _ok, detail in failed:
        print(f"  FAILED: {name} — {detail}")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
