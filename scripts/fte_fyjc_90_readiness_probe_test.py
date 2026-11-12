#!/usr/bin/env python3
"""Readiness probe fix (2026-10-20) — focused evidence suite (fte_fyjc_90).

Guards the defect reported by the production investigation: `/v1/ready` used to
infer "metering store available" from the fact that a SQLAlchemy Engine could be
CONSTRUCTED. `create_engine` is lazy and opens no socket, so a
configured-but-unreachable store was reported ready — production returned
`status: "ready"` / `production_ready: true` while the API's own `SELECT 1`
probe reported the database unreachable.

This suite needs no HTTP stack (deliberately: the suites that do — 62, 85 —
require `fastapi`, which is not installed everywhere). It exercises the REAL
gate and admission code, and it is anti-vacuous: every "reachable" claim is
backed by a side effect that only a real connection can produce.

Sections:
  A  Successful connection  — real SELECT 1, side effect proves it connected.
  B  Failed connection      — unopenable store → unavailable, no exception, no leak.
  C  Missing configuration  — nothing configured vs no fallback to DATABASE_URL.
  D  Timeout mechanism      — the measured bound, its clamp, and its placement.
  E  Probe caching          — bounded reuse, failure never cached as success.
  F  Admission semantics    — modes unchanged; outage still fails closed.
  G  Engine wiring          — the bound reaches create_engine (deterministic),
                              and the driver-scheme matrix incl. the psycopg3 gap.
  H  Real bounded connect   — refused + unresponsive server (needs psycopg2).
  I  Cache concurrency      — no deadlock, bounded herd, consistent verdicts.
  J  Probe cleanup          — session closed on both paths; no pool growth.
  K  Real PostgreSQL         — reachable, real outage, and the POST-CONNECT
                              hang the connect timeout cannot bound.

Run:  python3 scripts/fte_fyjc_90_readiness_probe_test.py
Exit: 0 = all checks passed, 1 = at least one failure.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.auth import admission as admission_boundary  # noqa: E402
from backend.auth import gate as metered_gate  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []

WELL_KNOWN_MISSING_DIR = "/nonexistent-platrixa-readiness-dir"
DEAD_STORE = f"sqlite:///{WELL_KNOWN_MISSING_DIR}/store.db"


SKIPS: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def skip(name: str, reason: str) -> None:
    """Record a check that could NOT run. Never counted as a pass."""
    SKIPS.append((name, reason))
    print(f"  [SKIP] {name} — {reason}")


def _clear(metering_url: str | None, *, dev_key: str | None = None, ttl: str | None = None):
    """Deterministic store/mode setup between cases (no inherited state)."""
    for var in (
        metered_gate.METERING_ENV_VAR,
        metered_gate.CONNECT_TIMEOUT_ENV_VAR,
        admission_boundary.PHASE15_ENV_VAR,
        admission_boundary.READINESS_CACHE_TTL_ENV_VAR,
    ):
        os.environ.pop(var, None)
    if metering_url is not None:
        os.environ[metered_gate.METERING_ENV_VAR] = metering_url
    if dev_key is not None:
        os.environ[admission_boundary.PHASE15_ENV_VAR] = dev_key
    if ttl is not None:
        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = ttl
    metered_gate._session_factory_cache.clear()
    admission_boundary.reset_readiness_cache()


def section_a() -> None:
    print("\nA — successful connection is MEASURED, not inferred")
    path = os.path.join(tempfile.mkdtemp(), "readiness_ok.db")
    _clear("sqlite:///" + path)

    block = admission_boundary.readiness_block()["admission"]
    check("A1 mode is metered-tenants", block["mode"] == "metered-tenants", str(block))
    check("A2 metering_store_available is True for a usable store",
          block["metering_store_available"] is True, str(block["metering_store_available"]))
    check("A3 production_ready is True for a usable store",
          block["production_ready"] is True, str(block["production_ready"]))
    # sqlite creates the database file on connect: this is the causal proof that
    # the probe opened a real connection rather than only building an Engine.
    check("A4 the probe really connected (the store file now exists)",
          os.path.exists(path), path)

    block2 = admission_boundary.readiness_block()["admission"]
    check("A5 repeat call stays ready", block2["production_ready"] is True, str(block2))


def section_b() -> None:
    print("\nB — failed connection is reported unavailable")
    _clear(DEAD_STORE)

    raised = None
    t0 = time.perf_counter()
    try:
        block = admission_boundary.readiness_block()["admission"]
    except Exception as exc:  # the report path must never raise
        raised = type(exc).__name__
        block = {}
    elapsed_ms = (time.perf_counter() - t0) * 1000

    check("B1 readiness_block does not raise on an unreachable store", raised is None, str(raised))
    check("B2 metering_store_available is False", block.get("metering_store_available") is False,
          str(block.get("metering_store_available")))
    check("B3 production_ready is False", block.get("production_ready") is False,
          str(block.get("production_ready")))
    check("B4 quota_enforced stays True (the boundary is still enforced)",
          block.get("quota_enforced") is True, str(block.get("quota_enforced")))
    check("B5 the failing probe is fast, not a hang", elapsed_ms < 10_000, f"{elapsed_ms:.1f} ms")

    body = repr(block)
    leaked = [tok for tok in ("/nonexistent-platrixa-readiness-dir", "sqlite:///") if tok in body]
    check("B6 no connection material in the readiness payload", not leaked, str(leaked))

    # The gate's own error must not carry the DSN either.
    try:
        metered_gate.probe_store()
        msg = ""
    except metered_gate.MeteredGateError as exc:
        msg = str(exc)
    check("B7 probe_store raises a fixed, DSN-free message",
          "unavailable" in msg and "sqlite" not in msg and "nonexistent" not in msg, msg)


def section_c() -> None:
    print("\nC — missing configuration, and no DATABASE_URL fallback")
    _clear(None)
    block = admission_boundary.readiness_block()["admission"]
    check("C1 mode is open-anonymous", block["mode"] == "open-anonymous", str(block["mode"]))
    check("C2 store availability is None (nothing configured, nothing claimed)",
          block["metering_store_available"] is None, str(block["metering_store_available"]))
    check("C3 production_ready is False", block["production_ready"] is False,
          str(block["production_ready"]))
    check("C4 metering_configured is False", block["metering_configured"] is False,
          str(block["metering_configured"]))

    # An ambient DATABASE_URL (the APPLICATION store) must not activate metering.
    os.environ["DATABASE_URL"] = DEAD_STORE
    try:
        metered_gate._session_factory_cache.clear()
        admission_boundary.reset_readiness_cache()
        block = admission_boundary.readiness_block()["admission"]
        check("C5 DATABASE_URL alone does not configure the metering store",
              block["metering_configured"] is False and block["metering_store_available"] is None,
              str(block))
        check("C6 DATABASE_URL alone does not make the deployment production_ready",
              block["production_ready"] is False, str(block["production_ready"]))
    finally:
        os.environ.pop("DATABASE_URL", None)


def section_d() -> None:
    print("\nD — the timeout mechanism and its actual bound")
    _clear(None)

    check("D1 default bounded connect timeout is 3s",
          metered_gate.connect_timeout_seconds() == 3, str(metered_gate.connect_timeout_seconds()))

    os.environ[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = "10"
    check("D2 operator override is honoured",
          metered_gate.connect_timeout_seconds() == 10, str(metered_gate.connect_timeout_seconds()))

    os.environ[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = "1"
    check("D3 clamped up to the libpq floor of 2s",
          metered_gate.connect_timeout_seconds() == 2, str(metered_gate.connect_timeout_seconds()))

    os.environ[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = "999"
    check("D4 clamped down to the 30s ceiling",
          metered_gate.connect_timeout_seconds() == 30, str(metered_gate.connect_timeout_seconds()))

    os.environ[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = "not-a-number"
    check("D5 an invalid value falls back to the default instead of failing",
          metered_gate.connect_timeout_seconds() == 3, str(metered_gate.connect_timeout_seconds()))
    os.environ.pop(metered_gate.CONNECT_TIMEOUT_ENV_VAR, None)

    pg_args = metered_gate._engine_connect_args("postgresql+psycopg2://u:p@h:5432/db")
    check("D6 the psycopg2 dialect receives connect_args.connect_timeout=3",
          pg_args.get("connect_args", {}).get("connect_timeout") == 3, str(pg_args))
    check("D7 non-psycopg2 URLs receive no connect_timeout (dialects reject it)",
          metered_gate._engine_connect_args("sqlite:///x.db") == {},
          str(metered_gate._engine_connect_args("sqlite:///x.db")))
    check("D8 the timeout is bounded on both ends of the range",
          metered_gate.MIN_CONNECT_TIMEOUT_SECONDS == 2
          and metered_gate.MAX_CONNECT_TIMEOUT_SECONDS == 30,
          f"{metered_gate.MIN_CONNECT_TIMEOUT_SECONDS}..{metered_gate.MAX_CONNECT_TIMEOUT_SECONDS}")

    # NOTE: a DROPPED-SYN network blackhole is still not exercised anywhere (the
    # real-socket checks in H cover refusal and an unresponsive server instead).
    # What this section verifies is the value, its clamp, and that it is attached
    # to the PostgreSQL dialects only.


def section_e() -> None:
    print("\nE — probe caching: failures cached, successes NEVER cached")
    path = os.path.join(tempfile.mkdtemp(), "cache_probe.db")
    _clear("sqlite:///" + path)  # default TTL

    calls: list[int] = []
    real_probe = metered_gate.probe_store

    def counting_probe():
        calls.append(1)
        return real_probe()

    metered_gate.probe_store = counting_probe  # type: ignore[assignment]
    try:
        first = admission_boundary.store_reachable()
        second = admission_boundary.store_reachable()
        check("E1 the first probe connects and reports reachable", first is True, str(first))
        # NEGATIVE-ONLY CACHE (readiness deadline follow-up): a ready verdict is
        # never served from the cache, so the second call must probe again.
        check("E2 every ready verdict is backed by a fresh probe (no cached success)",
              second is True and len(calls) == 2, f"probes={len(calls)}")

        admission_boundary.reset_readiness_cache()
        third = admission_boundary.store_reachable()
        check("E3 a ready verdict never comes from the cache (3 calls → 3 probes)",
              third is True and len(calls) == 3, f"probes={len(calls)}")

        # Failure caching: TTL 0 disables even that.
        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0"
        _clear(DEAD_STORE, ttl="0")
        metered_gate.probe_store = counting_probe  # _clear does not touch the stub
        calls.clear()
        for _ in range(3):
            admission_boundary.store_reachable()
        check("E4 TTL=0 disables failure caching (every call probes)",
              len(calls) == 3, f"probes={len(calls)}")

        # TTL 5: the FAILURE is reused, so a dead store cannot be re-probed by
        # every readiness poll.
        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "5"
        _clear(DEAD_STORE, ttl="5")
        metered_gate.probe_store = counting_probe
        calls.clear()
        down1 = admission_boundary.store_reachable()
        down2 = admission_boundary.store_reachable()
        check("E5 an unreachable store reports False (never retried into True)",
              down1 is False and down2 is False, f"{down1}/{down2}")
        check("E6 the failure verdict is reused within the TTL (one probe, not two)",
              len(calls) == 1, f"probes={len(calls)}")

        # A cached FAILURE must expire, otherwise a recovered store stays
        # not-ready forever. Short TTL and a real wait.
        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0.2"
        admission_boundary.reset_readiness_cache()
        calls.clear()
        first_fail = admission_boundary.store_reachable()
        time.sleep(0.3)
        after_expiry = admission_boundary.store_reachable()
        check("E7 an expired failure verdict is re-probed (never sticky)",
              first_fail is False and after_expiry is False and len(calls) == 2,
              f"probes={len(calls)} first={first_fail} after={after_expiry}")

        # THE contract that matters: a store that dies must not still be
        # advertised as ready. Same URL, same cache key, availability flipped —
        # under success-caching this returned the stale True.
        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "30"
        _clear("sqlite:///" + path, ttl="30")
        state = {"up": True}
        calls.clear()

        def flip_probe():
            calls.append(1)
            if not state["up"]:
                raise metered_gate.MeteredGateError("metering store unavailable")
            return real_probe()

        metered_gate.probe_store = flip_probe  # type: ignore[assignment]
        healthy = admission_boundary.store_reachable()
        state["up"] = False
        died = admission_boundary.store_reachable()
        check("E8 a store that dies is NOT reported ready from a cached success",
              healthy is True and died is False and len(calls) == 2,
              f"healthy={healthy} after_death={died} probes={len(calls)}")

        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "999"
        check("E9 an oversized TTL is capped at 30s",
              admission_boundary._readiness_ttl_seconds() == 30.0,
              str(admission_boundary._readiness_ttl_seconds()))
        os.environ[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "-5"
        check("E10 a non-positive TTL disables failure caching",
              admission_boundary._readiness_ttl_seconds() == 0.0,
              str(admission_boundary._readiness_ttl_seconds()))
    finally:
        metered_gate.probe_store = real_probe  # type: ignore[assignment]


def section_f() -> None:
    print("\nF — existing admission semantics are unchanged")
    _clear(None, dev_key="phase15-secret-value")
    check("F1 phase15 key takes precedence in the mode",
          admission_boundary.admission_mode() == "phase15-shared-key",
          admission_boundary.admission_mode())
    reason, ctx = admission_boundary.admit("phase15-secret-value")
    check("F2 the Phase 15 key admits with the shared identity",
          reason == admission_boundary.ADMIT_OK and ctx is not None
          and ctx.mode == "phase15-shared-key" and ctx.units_reserved == 0,
          f"{reason}/{getattr(ctx, 'mode', None)}")
    reason_bad, ctx_bad = admission_boundary.admit("wrong-key")
    check("F3 a wrong Phase 15 key is refused (no silent fallthrough)",
          reason_bad == admission_boundary.ADMIT_MISSING_KEY and ctx_bad is None, reason_bad)

    _clear(None)
    check("F4 zero-config mode is open-anonymous",
          admission_boundary.admission_mode() == "open-anonymous",
          admission_boundary.admission_mode())
    reason_anon, ctx_anon = admission_boundary.admit(None)
    check("F5 zero-config admits anonymously with zero units reserved",
          reason_anon == admission_boundary.ADMIT_OK and ctx_anon is not None
          and ctx_anon.units_reserved == 0, str(reason_anon))

    # Metered mode with the store DOWN must still fail CLOSED (503), not open.
    _clear(DEAD_STORE)
    check("F6 metered mode is selected when the store is configured",
          admission_boundary.admission_mode() == "metered-tenants",
          admission_boundary.admission_mode())
    reason_down, ctx_down = admission_boundary.admit("plx_test_deadbeefdeadbeef")
    check("F7 a store outage still fails closed (METERING_UNAVAILABLE, no context)",
          reason_down == admission_boundary.ADMIT_METERING_UNAVAILABLE and ctx_down is None,
          str(reason_down))
    reason_missing, _ = admission_boundary.admit(None)
    check("F8 a missing key still fails as MISSING_KEY",
          reason_missing == admission_boundary.ADMIT_MISSING_KEY, str(reason_missing))

    block_down = admission_boundary.readiness_block()["admission"]
    check("F9 readiness and admission agree that the store is down",
          block_down["production_ready"] is False
          and block_down["metering_store_available"] is False, str(block_down))

    _clear(None)


def section_g() -> None:
    """Deterministic proof that the bound reaches create_engine.

    No database and no driver needed: SQLAlchemy's create_engine is replaced with
    a recorder. This closes the gap that value/clamp tests leave open — that the
    kwarg is computed correctly but never actually handed to the engine builder.
    """
    print("\nG — the bound reaches create_engine (deterministic, no database)")
    import sqlalchemy

    recorded: list[tuple[str, dict]] = []
    real_create = sqlalchemy.create_engine

    class _FakeEngine:
        pass

    def fake_create(url, **kwargs):
        recorded.append((url, kwargs))
        return _FakeEngine()

    _clear(None)
    sqlalchemy.create_engine = fake_create  # type: ignore[assignment]
    try:
        os.environ[metered_gate.METERING_ENV_VAR] = "postgresql://u:p@127.0.0.1:5432/db"
        metered_gate._session_factory_cache.clear()
        metered_gate._session_factory()
        url_pg, kw_pg = recorded[-1]
        check("G1 a plain postgres URL is normalized to the psycopg2 dialect",
              url_pg.startswith("postgresql+psycopg2://"), url_pg)
        # Scope fix (2026-10-25): the shared engine bounds CONNECT only. A
        # connect-level statement_timeout also aborted tenant lookups and the
        # quota reservation UPDATE, so the probe applies its own bound for its
        # transaction instead (see K5b/K6).
        check("G2 the engine is built with connect_timeout=3 and NO global statement timeout",
              kw_pg.get("connect_args") == {"connect_timeout": 3}, str(kw_pg))
        check("G2b no statement_timeout leaks into the shared engine kwargs",
              "options" not in (kw_pg.get("connect_args") or {}), str(kw_pg))
        check("G3 pool_pre_ping and future survive the extra kwargs",
              kw_pg.get("pool_pre_ping") is True and kw_pg.get("future") is True, str(kw_pg))

        os.environ[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = "7"
        metered_gate._session_factory_cache.clear()
        metered_gate._session_factory()
        _url7, kw7 = recorded[-1]
        check("G4 the operator override reaches the engine (7s)",
              kw7.get("connect_args", {}).get("connect_timeout") == 7, str(kw7))

        os.environ.pop(metered_gate.CONNECT_TIMEOUT_ENV_VAR, None)
        os.environ[metered_gate.METERING_ENV_VAR] = "sqlite:///./probe_wiring.db"
        metered_gate._session_factory_cache.clear()
        metered_gate._session_factory()
        _url_sql, kw_sql = recorded[-1]
        check("G5 a sqlite store is built WITHOUT connect_args (its dialect rejects it)",
              "connect_args" not in kw_sql, str(kw_sql))

        # Driver-scheme matrix: every PostgreSQL URL scheme an operator can set.
        os.environ.pop(metered_gate.CONNECT_TIMEOUT_ENV_VAR, None)

        os.environ[metered_gate.METERING_ENV_VAR] = "postgresql+psycopg2://u:p@127.0.0.1:5432/db"
        metered_gate._session_factory_cache.clear()
        metered_gate._session_factory()
        _url_p2, kw_p2 = recorded[-1]
        args_p2 = kw_p2.get("connect_args", {})
        check("G6 an explicit psycopg2 URL is connect-bounded (no global statement timeout)",
              _url_p2.startswith("postgresql+psycopg2://")
              and args_p2.get("connect_timeout") == 3
              and "options" not in args_p2,
              str(kw_p2))

        # GAP CLOSED (readiness deadline fix, 2026-10-20). This check used to
        # pin the DEFECT: the bound was applied to the psycopg2 dialect only, so
        # a psycopg3 URL (a supported scheme — SQLAlchemy 2.1 resolves a bare
        # `postgresql://` to psycopg3 when installed) reached the driver with no
        # connect timeout at all. Both schemes now share one bounded path, so the
        # assertion is inverted: psycopg3 must be bounded too.
        os.environ[metered_gate.METERING_ENV_VAR] = "postgresql+psycopg://u:p@127.0.0.1:5432/db"
        metered_gate._session_factory_cache.clear()
        metered_gate._session_factory()
        _url_p3, kw_p3 = recorded[-1]
        args_p3 = kw_p3.get("connect_args", {})
        check("G7 a psycopg3 URL is connect-bounded too (the previous unbounded-scheme gap is closed)",
              _url_p3.startswith("postgresql+psycopg://")
              and args_p3.get("connect_timeout") == 3
              and "options" not in args_p3,
              str(kw_p3))
    finally:
        sqlalchemy.create_engine = real_create  # type: ignore[assignment]
        metered_gate._session_factory_cache.clear()
        _clear(None)


def section_h() -> None:
    """Real socket-level checks. Both are LOCAL; no external network is used.

    Honest scope: (1) is a genuine TCP refusal. (2) is a server that accepts the
    TCP connection and never answers the PostgreSQL startup packet — an
    UNRESPONSIVE server, not a dropped-SYN network blackhole. Whether libpq
    enforces connect_timeout during the startup phase is therefore what this
    measures; a true blackhole is still not tested.
    """
    print("\nH — real bounded connect: refused, then unresponsive (needs psycopg2)")
    try:
        import psycopg2  # noqa: F401
    except Exception as exc:  # pragma: no cover — environment dependent
        skip("H1-H5 real bounded-connect checks",
             f"psycopg2 unavailable here ({type(exc).__name__}); run under the project venv")
        return

    import socket
    import subprocess

    # --- H1/H2: a port that was bound and then closed → connection REFUSED ---
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 0))
    refused_port = probe.getsockname()[1]
    probe.close()

    _clear(f"postgresql://u:p@127.0.0.1:{refused_port}/db", ttl="0")
    t0 = time.perf_counter()
    refused_verdict = admission_boundary.store_reachable()
    refused_s = time.perf_counter() - t0
    check("H1 a refused connection reports the store unavailable",
          refused_verdict is False, str(refused_verdict))
    check("H2 refusal fails fast instead of waiting for the bound",
          refused_s < 5.0, f"{refused_s:.2f}s")

    # --- H3/H4: accept the TCP connection, never answer → must be cut off ---
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    hanging_port = srv.getsockname()[1]
    held: list = []
    srv.settimeout(0.25)

    import threading

    stop = threading.Event()

    def acceptor() -> None:
        while not stop.is_set():
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            held.append(conn)  # hold it open; never write a byte

    worker = threading.Thread(target=acceptor, daemon=True)
    worker.start()

    child = (
        "import json,sys,time\n"
        "from backend.auth import admission\n"
        "t0=time.perf_counter()\n"
        "ok=admission.store_reachable()\n"
        "print(json.dumps({'ok':ok,'seconds':time.perf_counter()-t0}))\n"
    )

    def measure(bound_seconds: int) -> dict | None:
        """Probe the hanging server in a child process with a hard wall clock.

        A subprocess timeout means an unenforced bound can never hang the suite;
        it becomes a recorded failure instead.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith("PLATRIXA_")}
        env.pop("DATABASE_URL", None)
        env[metered_gate.METERING_ENV_VAR] = f"postgresql://u:p@127.0.0.1:{hanging_port}/db"
        env[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = str(bound_seconds)
        env[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0"
        try:
            proc = subprocess.run(
                [sys.executable, "-c", child],
                cwd=str(Path(__file__).resolve().parent.parent),
                env=env,
                capture_output=True,
                text=True,
                timeout=25,
            )
        except subprocess.TimeoutExpired:
            return None
        if proc.returncode != 0:
            return None
        import json as _json

        return _json.loads(proc.stdout.strip().splitlines()[-1])

    try:
        short = measure(2)
        if short is None:
            check("H3 an unresponsive server reports the store unavailable", False,
                  "child did not return within 25s — the bound was NOT enforced")
        else:
            check("H3 an unresponsive server reports the store unavailable",
                  short["ok"] is False, str(short))
            check("H4 the connect attempt is cut off at the configured bound (~2s)",
                  1.0 <= short["seconds"] <= 6.0, f"{short['seconds']:.2f}s")

            # The same server probed with a LARGER configured bound must take
            # measurably longer. That distinguishes "the configured value drives
            # the timeout" from "a fixed constant happens to be ~2s".
            longer = measure(4)
            if longer is None:
                check("H5 a larger configured bound lengthens the attempt", False,
                      "second child did not return within 25s")
            else:
                delta = longer["seconds"] - short["seconds"]
                check("H5 a larger configured bound lengthens the attempt (2s vs 4s)",
                      delta >= 0.8,
                      f"2s→{short['seconds']:.2f}s, 4s→{longer['seconds']:.2f}s, delta={delta:.2f}s")
    finally:
        stop.set()
        srv.close()
        for conn in held:
            try:
                conn.close()
            except OSError:
                pass
        _clear(None)


def section_i() -> None:
    """Cache behavior under concurrent readiness polls.

    Design under test: reads of the cache take the lock, but the PROBE runs
    outside it (otherwise a 3s connect would serialize every readiness caller).
    The consequence is a bounded thundering herd on a cold cache, which is
    permissible; an unbounded one, a deadlock, or a corrupted verdict is not.
    """
    print("\nI — readiness cache under concurrency")
    import threading

    path = os.path.join(tempfile.mkdtemp(), "concurrent.db")
    _clear("sqlite:///" + path, ttl="30")

    calls: list[int] = []
    real_probe = metered_gate.probe_store

    def slow_probe():  # a probe slow enough to expose serialization
        calls.append(1)
        time.sleep(0.15)
        return real_probe()

    metered_gate.probe_store = slow_probe  # type: ignore[assignment]
    try:
        for label, url, expect in (
            ("I1-I5", "sqlite:///" + path, True),
            ("I6-I7", DEAD_STORE, False),
        ):
            _clear(url, ttl="30")
            metered_gate.probe_store = slow_probe  # type: ignore[assignment]
            results: list = []
            errors: list = []
            calls.clear()

            def worker() -> None:
                try:
                    results.append(admission_boundary.store_reachable())
                except Exception as exc:  # store_reachable must never raise
                    errors.append(type(exc).__name__)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            t0 = time.perf_counter()
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)
            elapsed = time.perf_counter() - t0
            alive = [t for t in threads if t.is_alive()]

            if label == "I1-I5":
                check("I1 concurrent callers all get the same ready verdict",
                      results == [True] * 8, str(results))
                check("I2 no concurrent caller raised", not errors, str(errors))
                check("I3 no deadlock: every caller returned", not alive, f"alive={len(alive)}")
                # If the probe ran while holding the cache lock, 8 callers at
                # 0.15s each would need >=1.2s. Measured, not assumed.
                check("I4 the probe runs outside the cache lock (no serialization)",
                      elapsed < 0.6, f"{elapsed:.2f}s for 8 callers")
                check("I5 the herd is bounded by the caller count (never more probes than callers)",
                      1 <= len(calls) <= 8, f"probes={len(calls)}")
            else:
                check("I6 a concurrent outage yields False for every caller",
                      results == [False] * 8, str(results))
                check("I7 a concurrent outage never raises from the readiness path",
                      not errors and not alive, f"errors={errors} alive={len(alive)}")
    finally:
        metered_gate.probe_store = real_probe  # type: ignore[assignment]
        _clear(None)


def section_j() -> None:
    """Session/connection lifecycle on BOTH paths, and no pool growth."""
    print("\nJ — probe cleanup and resource hygiene")
    path = os.path.join(tempfile.mkdtemp(), "hygiene.db")
    _clear("sqlite:///" + path)

    closes: list[int] = []
    executes: list[int] = []
    real_factory = metered_gate._session_factory

    class _GoodSession:
        def execute(self, *_a, **_k):
            executes.append(1)
            return None

        def close(self):
            closes.append(1)

    class _BadSession:
        def execute(self, *_a, **_k):
            executes.append(1)
            raise RuntimeError("driver exploded: postgresql://leak:pw@h/db")

        def close(self):
            closes.append(1)

    try:
        metered_gate._session_factory = lambda: (lambda: _GoodSession())  # type: ignore[assignment]
        metered_gate.probe_store()
        check("J1 the successful probe closes its session", closes == [1], f"closes={len(closes)}")
        check("J2 the successful probe executes exactly one statement",
              executes == [1], f"executes={len(executes)}")

        metered_gate._session_factory = lambda: (lambda: _BadSession())  # type: ignore[assignment]
        err: BaseException | None = None
        try:
            metered_gate.probe_store()
        except metered_gate.MeteredGateError as exc:
            err = exc
        check("J3 a failing statement still closes the session (finally path)",
              len(closes) == 2, f"closes={len(closes)}")
        check("J4 the failure surfaces as MeteredGateError with fixed text",
              err is not None and str(err) == "metering store unavailable", repr(str(err)))
        check("J5 the driver exception survives only as __cause__, never in the message",
              isinstance(getattr(err, "__cause__", None), RuntimeError)
              and "leak" not in str(err) and "postgresql://" not in str(err),
              repr(str(err)))
    finally:
        metered_gate._session_factory = real_factory  # type: ignore[assignment]

    # Real path: one engine per store, and every probe returns its connection.
    _clear("sqlite:///" + path, ttl="0")
    f1 = metered_gate._session_factory()
    f2 = metered_gate._session_factory()
    check("J6 the probe reuses one engine per store (no orphan engines)", f1 is f2, "different factories")
    for _ in range(5):
        admission_boundary.store_reachable()
    pool = f1.kw["bind"].pool
    check("J7 repeated probes return every connection to the pool (no leak)",
          pool.checkedout() == 0, f"checkedout={pool.checkedout()}")


# --- real-PostgreSQL helper: complete the handshake, then go silent ---------


def _freeze_after_handshake(upstream_socket_path: str, stop):
    """A relay that forwards the PostgreSQL STARTUP/HANDSHAKE, then stalls.

    Returns (proxy_dir, srv_socket, held_sockets). The client gets a *successful
    connection* and a *completed handshake*, and only afterwards does the server
    stop answering — the exact failure mode a connection timeout cannot bound.

    UNIX sockets are used because the embedded server under test listens on a
    UNIX socket only (``listen_addresses = ''``); the proxy path is a directory
    containing ``.s.PGSQL.5432`` so libpq resolves it as a normal server.
    """
    import socket
    import threading

    proxy_dir = tempfile.mkdtemp()
    proxy_path = os.path.join(proxy_dir, ".s.PGSQL.5432")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(proxy_path)
    srv.listen(4)
    held: list = []
    srv.settimeout(0.25)

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
                # Walk PostgreSQL protocol messages; 'Z' (ReadyForQuery) means
                # the startup handshake is complete. The length field counts
                # itself but NOT the type byte, so a message occupies 1 + len.
                while len(buf) >= 5:
                    mtype = buf[:1]
                    mlen = int.from_bytes(buf[1:5], "big")
                    if mlen < 4 or len(buf) < 1 + mlen:
                        break
                    buf = buf[1 + mlen:]
                    if mtype == b"Z":
                        handshake.set()
                if handshake.is_set():
                    # Handshake done: forward nothing further. Hold the sockets
                    # open so the client never sees EOF and waits forever.
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


def section_k() -> None:
    """Real PostgreSQL: reachable, real outage, and the POST-CONNECT hang.

    CORRECTED FRAMING (2026-10-20, readiness deadline review): the relay used
    below keeps forwarding client→server and stops forwarding server→client
    after the handshake, so the probe's statement DOES reach PostgreSQL and is
    executed; only the REPLY is dropped. This measures a MUTE RESPONSE PATH,
    not a server-side query stall. The distinction matters: a stalled query is
    bounded by statement_timeout (suite 91, C5), while a lost reply is not
    bounded by any client-side control in libpq. Both are "hangs after a
    successful connect", but only one is fixable with a statement timeout.
    """
    print("\nK — real PostgreSQL: reachable, outage, and post-connect hang")
    try:
        import pgserver  # noqa: F401
        import psycopg2  # noqa: F401
    except Exception as exc:  # pragma: no cover — environment dependent
        skip("K1-K10 real PostgreSQL checks",
             f"pgserver/psycopg2 unavailable ({type(exc).__name__}); run under the project venv")
        return

    import json
    import socket
    import subprocess
    import threading

    data_dir = tempfile.mkdtemp()
    srv = None
    try:
        srv = pgserver.get_server(data_dir)
        uri = srv.get_uri()

        # --- K1: a REAL server is reported ready (real driver, real socket) ---
        _clear(uri, ttl="0")
        check("K1 readiness is True against a real PostgreSQL server",
              admission_boundary.store_reachable() is True, uri.split("@")[0])
        check("K2 the real probe left no connection checked out",
              metered_gate._session_factory().kw["bind"].pool.checkedout() == 0,
              str(metered_gate._session_factory().kw["bind"].pool.checkedout()))

        # --- K3: a REAL outage (server stopped) is reported unavailable ---
        srv.cleanup()
        srv = None
        _clear(uri, ttl="0")
        t0 = time.perf_counter()
        down = admission_boundary.store_reachable()
        down_s = time.perf_counter() - t0
        check("K3 readiness is False when the real server is stopped", down is False, str(down))
        check("K4 the real outage fails fast (refused, not a hang)",
              down_s < 5.0, f"{down_s:.2f}s")

        # --- restart for the post-connect checks ---
        srv = pgserver.get_server(data_dir)
        uri = srv.get_uri()
        _clear(uri, ttl="0")
        engine = metered_gate._session_factory().kw["bind"]

        with engine.connect() as conn:
            st = conn.exec_driver_sql("SHOW statement_timeout").scalar()
            driver = engine.dialect.driver
        check("K5 the shared metering connection carries NO statement_timeout (admission not shortened)",
              st in ("0", "0ms"), f"statement_timeout={st!r} driver={driver}")
        check("K5a the metering engine uses the psycopg2 driver (SA 2.1 would pick psycopg3 for a bare URL)",
              driver == "psycopg2", f"driver={driver}")

        # Scope fix (2026-10-25): the readiness probe applies statement_timeout
        # for ITS transaction only, so the stalled-query bound still protects
        # readiness while a metered reservation may wait on a row lock.
        from sqlalchemy import text as _sql_text

        probe_s = metered_gate._session_factory()()
        try:
            metered_gate._apply_probe_statement_timeout(probe_s)
            in_probe = probe_s.execute(_sql_text("SHOW statement_timeout")).scalar()
        finally:
            probe_s.close()
        check("K5b the probe transaction carries statement_timeout=1500ms",
              in_probe in ("1500ms", "1500"), f"statement_timeout={in_probe!r}")

        stalled_err = None
        t0 = time.perf_counter()
        ps = metered_gate._session_factory()()
        try:
            metered_gate._apply_probe_statement_timeout(ps)
            ps.execute(_sql_text("SELECT pg_sleep(3.0)"))
        except Exception as exc:
            stalled_err = type(exc).__name__
        finally:
            ps.close()
        stalled = time.perf_counter() - t0
        check("K6 a stalled PROBE statement is aborted at the bound (~1.5s)",
              stalled_err is not None and 1.0 <= stalled <= 3.0,
              f"error={stalled_err} after {stalled:.2f}s")

        # The same stall on the ADMISSION path must run to completion: no
        # server-side abort may turn a lock wait into a spurious 503.
        plain_err = None
        t0 = time.perf_counter()
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT pg_sleep(2.0)")
        except Exception as exc:
            plain_err = type(exc).__name__
        plain = time.perf_counter() - t0
        check("K6b a 2s admission-side statement is NOT aborted (baseline restored)",
              plain_err is None and plain >= 1.9,
              f"error={plain_err} after {plain:.2f}s")

        # And the probe's transaction-local bound must not survive into the
        # next checkout: the pooled connection returns at the server default.
        with engine.connect() as conn:
            after = conn.exec_driver_sql("SHOW statement_timeout").scalar()
        check("K6c the transaction-local probe bound does not leak to the next checkout",
              after in ("0", "0ms"), f"statement_timeout={after!r}")

        pool_timeout = getattr(engine.pool, "_timeout", None)
        check("K7 the pool wait bound is the configured 1.0s, not SQLAlchemy's 30s default",
              pool_timeout == metered_gate.pool_timeout_seconds(),
              f"pool_timeout={pool_timeout}")

        # --- K8: the proposed remedy, measured (NOT applied to the source) ---
        from sqlalchemy import create_engine

        # Pin the driver explicitly: SQLAlchemy 2.1 resolves a bare
        # `postgresql://` to psycopg3 (the psycopg dialect), which is not
        # installed here. That resolution is exactly why the production code
        # normalizes to `postgresql+psycopg2://` before building the engine.
        remedy_url = uri.replace("postgresql://", "postgresql+psycopg2://", 1)
        remedy = create_engine(
            remedy_url,
            pool_pre_ping=True,
            future=True,
            connect_args={"options": "-c statement_timeout=1500"},
        )
        aborted = None
        t0 = time.perf_counter()
        try:
            with remedy.connect() as conn:
                conn.exec_driver_sql("SELECT pg_sleep(4.0)")
        except Exception as exc:
            aborted = type(exc).__name__
        remedy_s = time.perf_counter() - t0
        try:
            remedy.dispose()
        except Exception:
            pass
        check("K8 PROPOSED FIX WORKS: statement_timeout=1500 aborts a stalled statement (~1.5s)",
              aborted is not None and 1.0 <= remedy_s <= 3.5,
              f"aborted={aborted} after {remedy_s:.2f}s")

        # --- K9/K10: end-to-end, through a handshake that then goes silent ---
        socket_dir = uri.split("host=", 1)[1].strip()
        upstream_path = os.path.join(socket_dir, ".s.PGSQL.5432")
        check("K9a the embedded server's UNIX socket exists (relay target is real)",
              os.path.exists(upstream_path), upstream_path)

        stop = threading.Event()
        proxy_dir, proxy_srv, held = _freeze_after_handshake(upstream_path, stop)
        child = (
            "import json,sys,time\n"
            "from backend.auth import admission\n"
            "t0=time.perf_counter()\n"
            "ok=admission.store_reachable()\n"
            "print(json.dumps({'ok':ok,'seconds':time.perf_counter()-t0}))\n"
        )
        # Reuse the REAL role/database from the live URI: an invented role would
        # be rejected during authentication and the probe would fail fast,
        # which would look like a bound and hide the hang being measured.
        from urllib.parse import urlparse

        parsed = urlparse(uri)
        real_user = parsed.username or "postgres"
        real_db = (parsed.path or "/postgres").lstrip("/") or "postgres"

        env = {k: v for k, v in os.environ.items() if not k.startswith("PLATRIXA_")}
        env.pop("DATABASE_URL", None)
        env[metered_gate.METERING_ENV_VAR] = (
            f"postgresql://{real_user}@/{real_db}?host={proxy_dir}"
        )
        env[metered_gate.CONNECT_TIMEOUT_ENV_VAR] = "2"
        env[admission_boundary.READINESS_CACHE_TTL_ENV_VAR] = "0"
        timed_out = False
        result = None
        try:
            proc = subprocess.run(
                [sys.executable, "-c", child],
                cwd=str(Path(__file__).resolve().parent.parent),
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                result = json.loads(proc.stdout.strip().splitlines()[-1])
        except subprocess.TimeoutExpired:
            timed_out = True
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

        check("K10 the probe completed the handshake through the relay (statement forwarded, reply dropped)",
              timed_out or (result is not None),
              f"child never reached the probe; result={result}")
        check("K11 RESIDUAL LIMITATION: a mute response path after connect is not bounded by "
              "connect or statement timeouts (no return within 20s)",
              timed_out,
              f"the probe RETURNED in {result} — some other bound applies; re-measure"
              if not timed_out else "")
    finally:
        if srv is not None:
            try:
                srv.cleanup()
            except Exception:
                pass
        _clear(None)


def main() -> int:
    print("=" * 74)
    print("fte_fyjc_90 — readiness probe: measured availability")
    print("=" * 74)
    for section in (
        section_a,
        section_b,
        section_c,
        section_d,
        section_e,
        section_f,
        section_g,
        section_h,
        section_i,
        section_j,
        section_k,
    ):
        section()

    failed = [c for c in CHECKS if not c[1]]
    print("\n" + "=" * 74)
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if SKIPS:
        print(f"{len(SKIPS)} check(s) SKIPPED (not executed, not passed):")
        for name, reason in SKIPS:
            print(f"  SKIP: {name} — {reason}")
    if failed:
        for name, _ok, detail in failed:
            print(f"  FAILED: {name} — {detail}")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
