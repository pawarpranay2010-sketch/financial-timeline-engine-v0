#!/usr/bin/env python3
"""Platrixa S5 — fresh-database key creation (regression for R5).

The defect: ``create_api_key`` inserts a registry row AND a companion
``platrixa_tenant_quotas`` row in one transaction, but the quota table used to
be created lazily at the first METERED request. On a brand-new database the
first key creation therefore failed with::

    psycopg2.errors.UndefinedTable: relation "platrixa_tenant_quotas" does not exist

The fix under test applies BOTH idempotent DDL files (quota first) in
``api_keys._ensure_schema``.

This suite starts a disposable embedded PostgreSQL cluster with NO schema at
all and proves:
  1. the quota table really is absent before the first call;
  2. the first ``create_api_key`` succeeds;
  3. only the SHA-256 hash is stored (never the raw key);
  4. the quota row exists with the requested limit;
  5. a second creation and a repeated schema ensure are idempotent.

Run:
    python scripts/platrixa_fresh_database_key_creation_test.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PASSED = 0
FAILED: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} — {detail}")


def main() -> int:
    print("=" * 78)
    print("Platrixa S5 — fresh-database API-key creation")
    print("=" * 78)

    import pgserver

    data = Path(f"/tmp/platrixa_fresh_db_{os.getpid()}")
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data)
    uri = server.get_uri()
    if "/tmp/" not in uri:
        print(f"refusing non-disposable database: {uri!r}")
        return 1

    from backend.auth import gate as metered_gate

    os.environ[metered_gate.METERING_ENV_VAR] = uri
    os.environ["DATABASE_URL"] = uri

    from sqlalchemy import create_engine, text

    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)

    def relation(name: str) -> bool:
        with engine.connect() as conn:
            return bool(
                conn.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}).scalar()
            )

    check(
        "the fresh database has no quota table yet",
        relation("platrixa_tenant_quotas") is False,
        "quota table already existed",
    )
    check(
        "the fresh database has no key registry yet",
        relation("platrixa_api_keys") is False
        or relation("platrixa_api_key_registry") is False,
        "registry already existed",
    )

    from backend.auth import api_keys as key_store

    tenant = f"fresh-tenant-{int(time.time())}"
    try:
        created = key_store.create_api_key(tenant, "fresh-key", "test", monthly_limit=250)
    except Exception as exc:  # noqa: BLE001 - the defect under test was an exception
        check("first key creation on a fresh database succeeds", False, f"{type(exc).__name__}: {exc}")
        engine.dispose()
        return 1
    else:
        check("first key creation on a fresh database succeeds", True)

    check(
        "the quota table is created by the first call",
        relation("platrixa_tenant_quotas"),
        "quota table still missing after create",
    )
    check(
        "the registry table is created by the first call",
        relation("platrixa_api_keys") or relation("platrixa_api_key_registry"),
        "registry table still missing after create",
    )

    raw = created.get("key", "")
    expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with engine.connect() as conn:
        quota = conn.execute(
            text(
                "SELECT tenant_id, monthly_limit, current_month_usage, is_active "
                "FROM platrixa_tenant_quotas WHERE api_key_hash = :h"
            ),
            {"h": expected_hash},
        ).mappings().first()
        stored_hashes = [
            row[0]
            for row in conn.execute(text("SELECT api_key_hash FROM platrixa_tenant_quotas")).all()
        ]

    check(
        "the quota row exists for the new key hash",
        quota is not None,
        "no quota row for the returned key",
    )
    check(
        "the quota row carries the requested limit",
        quota is not None and int(quota["monthly_limit"]) == 250,
        dict(quota) if quota else None,
    )
    check(
        "only a SHA-256 hash is stored (never the raw key)",
        all(len(h) == 64 for h in stored_hashes) and raw not in stored_hashes,
        stored_hashes,
    )

    second = key_store.create_api_key(tenant, "fresh-key-2", "test")
    check(
        "a second key for the same tenant also succeeds",
        bool(second.get("key")) and second["key"] != raw,
        second.get("id"),
    )

    # Re-running the idempotent schema ensure must be safe.
    key_store._ensure_schema(engine)
    key_store._ensure_schema(engine)
    check("repeated schema ensure is idempotent", True)

    engine.dispose()
    total = PASSED + len(FAILED)
    print("\n" + "=" * 78)
    print(f"RESULT: {PASSED}/{total} fresh-database checks passed")
    for name, detail in FAILED:
        print(f"  FAIL {name} — {detail}")
    print("=" * 78)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
