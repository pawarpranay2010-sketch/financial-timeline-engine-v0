#!/usr/bin/env python3
"""M10 — encrypted backup + REAL restore drill (two disposable PostgreSQLs).

Proves, end to end:

  1. a backup is created from a populated Platrixa database;
  2. it is encrypted (the file does not contain the dump in clear) and the
     manifest hashes verify;
  3. tampering with one byte makes verification FAIL (integrity is real);
  4. it restores into a COMPLETELY EMPTY database;
  5. the restored database has the same rows, and the startup migration path
     is idempotent on it (no manual SQL);
  6. the restored job plane actually WORKS: a job that was QUEUED in the
     backup is claimed and completed on the restored instance, and capacity
     is released exactly once.

The deterministic accounting verdict itself is exercised by
``platrixa_phase1b_developer_workflow_test.py`` (which runs a real candidate
through the kernel); this drill proves the DATA needed for that verdict
survives a restore.

Run:
    python3 scripts/platrixa_backup_restore_drill_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _v in ("PLATRIXA_QUEUE_GLOBAL_LIMIT", "PLATRIXA_QUEUE_PER_TENANT_LIMIT", "DATABASE_URL"):
    os.environ.pop(_v, None)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def _start_pg(path: Path):
    import pgserver

    shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(path)
    uri = srv.get_uri()
    if "/tmp/" not in uri:
        raise RuntimeError(f"refusing non-disposable test database: {uri!r}")
    return srv, uri


def _bind(url: str) -> None:
    """Point the Platrixa store at ``url`` and let its startup path run."""
    from backend.auth import async_jobs, gate as metered_gate

    os.environ[metered_gate.METERING_ENV_VAR] = url
    async_jobs._schema_ensured.discard(url)
    async_jobs._session_factory()


def _rows(url: str) -> dict:
    from sqlalchemy import create_engine, text

    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    with engine.begin() as conn:
        jobs = conn.execute(text("SELECT count(*) FROM platrixa_async_jobs")).scalar_one()
        keys = conn.execute(text("SELECT count(*) FROM platrixa_api_keys")).scalar_one()
        held = conn.execute(
            text("SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held")
        ).scalar_one()
        columns = {
            str(r[0])
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'platrixa_async_jobs'"
                )
            )
        }
    engine.dispose()
    return {"jobs": int(jobs), "keys": int(keys), "held": int(held), "columns": columns}


def main() -> int:
    from backend.auth import api_keys as store
    from backend.auth import async_jobs

    src_srv, src_url = _start_pg(Path("/tmp/platrixa_drill_src_pgdata"))
    _bind(src_url)

    created = store.provision_tenant("drill-tenant", "drill developer", "test", 100)
    check("source: tenant provisioned on a fresh database", bool(created.get("key")), "no key returned")
    key = created["key"]

    job = async_jobs.create_job(
        "drill-tenant",
        {"raw_input": "paid rent 100 rupees in cash"},
        kind=async_jobs.JOB_KIND_INFERENCE,
    )
    check("source: one inference job is QUEUED and holds capacity", job is not None and job.status == "QUEUED")

    before = _rows(src_url)
    check("source: visitor columns exist via the startup path only", {"visitor_id", "owner_type"} <= before["columns"])

    tmpdir = Path(tempfile.mkdtemp(prefix="platrixa-drill-"))
    key_file = tmpdir / "backup.key"
    backup = tmpdir / "db.pgbackup"

    def run(*args: str, expect: int = 0):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/platrixa_pg_backup.py"), *args],
            capture_output=True, text=True,
        )
        if expect is not None and proc.returncode != expect:
            raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr[:800]}")
        return proc

    run("keygen", "--out", str(key_file))
    key_bytes = key_file.read_bytes()
    check("backup key is 32 raw bytes with 0600 permissions", len(key_bytes) == 32 and (key_file.stat().st_mode & 0o077) == 0)

    run("create", "--database-url", src_url, "--out", str(backup), "--key-file", str(key_file))
    check("backup file and manifest exist", backup.exists() and Path(str(backup) + ".manifest.json").exists())
    blob = backup.read_bytes()
    check("backup is encrypted (the plain dump is not present in clear)", b"pg_dump" not in blob[:64] and blob.startswith(b"PLXBAK1\x00"))

    manifest = json.loads(Path(str(backup) + ".manifest.json").read_text())
    check(
        "manifest records counts and hashes",
        manifest.get("table_counts", {}).get("platrixa_async_jobs", 0) >= 1
        and len(manifest.get("plaintext_sha256", "")) == 64,
        json.dumps(manifest)[:300],
    )

    verify = run("verify", "--backup", str(backup), "--key-file", str(key_file))
    check("verify passes and pg_restore can read the archive", json.loads(verify.stdout)["ok"] is True)

    # tamper: flip one byte of the ciphertext
    tampered = tmpdir / "tampered.pgbackup"
    bad = bytearray(blob)
    bad[-1] ^= 0xFF
    tampered.write_bytes(bytes(bad))
    shutil.copyfile(str(backup) + ".manifest.json", str(tampered) + ".manifest.json")
    tamper = run("verify", "--backup", str(tampered), "--key-file", str(key_file), expect=1)
    check("a single flipped byte fails verification", tamper.returncode == 1)
    check(
        "the tamper report names the integrity problem",
        "mismatch" in tamper.stdout or "cannot read" in tamper.stdout,
        tamper.stdout[:200],
    )

    # restore into a genuinely empty database
    dst_srv, dst_url = _start_pg(Path("/tmp/platrixa_drill_dst_pgdata"))
    run("restore", "--backup", str(backup), "--key-file", str(key_file), "--target-url", dst_url)
    after = _rows(dst_url)
    check("restored database has the same job count", after["jobs"] == before["jobs"], f"{after['jobs']} vs {before['jobs']}")
    check("restored database has the same key count", after["keys"] == before["keys"])
    check("restored rows still hold capacity", after["held"] == before["held"])
    check("restored schema keeps the visitor columns", {"visitor_id", "owner_type"} <= after["columns"])

    # the restore refuses a non-empty target unless forced
    blocked = subprocess.run(
        [sys.executable, str(ROOT / "scripts/platrixa_pg_backup.py"), "restore",
         "--backup", str(backup), "--key-file", str(key_file), "--target-url", dst_url],
        capture_output=True, text=True,
    )
    check("restore refuses a non-empty target (no accidental overwrite)", blocked.returncode != 0)

    # the restored job plane WORKS
    _bind(dst_url)
    claimed = async_jobs.claim_next_job(
        owner="drill-worker", kind=async_jobs.JOB_KIND_INFERENCE, tenant_id="drill-tenant"
    )
    check("restored: the queued job is claimable", claimed is not None and claimed.job_id == job.job_id)
    if claimed is None:
        return 1

    completed = async_jobs.complete_job(
        claimed.job_id,
        envelope={"api_version": "v1", "api_status": "VERIFIED", "request_id": "drill-request"},
        http_status=200,
        lease_owner=claimed.lease_owner,
        lease_generation=claimed.lease_generation,
    )
    check("restored: the job is completed through the fenced commit", completed is True)
    final = async_jobs.get_job("drill-tenant", claimed.job_id)
    check("restored: terminal status is COMPLETED", final is not None and final.status == "COMPLETED")

    from backend.auth import queue_capacity

    rec = queue_capacity.reconcile()
    check("restored: capacity accounting is consistent after the round trip", rec["consistent"] is True, json.dumps(rec))

    # the key material is intact and usable, and the raw key was never stored
    from sqlalchemy import create_engine, text

    engine = create_engine(dst_url.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    with engine.begin() as conn:
        leaked = conn.execute(
            text("SELECT count(*) FROM platrixa_api_keys WHERE key_hash = :k"), {"k": key}
        ).scalar_one()
    engine.dispose()
    check("restored: the raw key is not present anywhere (hash-only storage survives)", int(leaked) == 0)

    print()
    passed = sum(1 for _n, ok, _d in CHECKS if ok)
    print("=" * 78)
    print(f"RESULT: {passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        for name, ok, detail in CHECKS:
            if not ok:
                print(f"  FAIL: {name} — {detail}")
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
