#!/usr/bin/env python3
"""Platrixa — encrypted PostgreSQL backup / verify / restore (M10).

WHY THIS EXISTS
---------------
The production database has no automated backup, no PITR and no drilled
restore. A private beta must not depend on "the volume is probably fine".

DESIGN
------
* **Logical dump** (`pg_dump -Fc`), because it is portable, inspectable, and
  restorable into any PostgreSQL 18 instance — including a disposable local
  one, which is the only way a restore can be *tested*.
* **AES-256-GCM** encryption with a key from a file the operator controls.
  The tool never prints, logs or derives the key from a passphrase that would
  end up in a shell history.
* **A manifest** recording the plaintext hash, the ciphertext hash, the
  PostgreSQL version, the database name, the creation time and the table
  counts. A backup is not a backup until it has been read back end to end;
  `verify` decrypts, hash-checks and runs `pg_restore --list`.
* **Restore refuses a non-empty target** unless `--force` is given, so a
  mistaken command cannot overwrite a live database.
* **pg_dump/pg_restore discovery**: `--pg-bin`, then `PATH`, then the binaries
  shipped with the `pgserver` test package. No silent fallback to a different
  major version: the version is recorded in the manifest and must match the
  server major version at restore time.

USAGE
-----
    python3 scripts/platrixa_pg_backup.py keygen  --out ~/.platrixa/backup.key
    python3 scripts/platrixa_pg_backup.py create  --database-url "$URL" --out db.pgbackup --key-file ~/.platrixa/backup.key
    python3 scripts/platrixa_pg_backup.py verify  --backup db.pgbackup --key-file ~/.platrixa/backup.key
    python3 scripts/platrixa_pg_backup.py restore --backup db.pgbackup --key-file ~/.platrixa/backup.key --target-url "$TARGET"

This tool never connects to a database it was not given explicitly, and never
writes to the source database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

MAGIC = b"PLXBAK1\x00"
NONCE_BYTES = 12
KEY_BYTES = 32
MANIFEST_SUFFIX = ".manifest.json"


# ---------------------------------------------------------------------------
# Key handling
# ---------------------------------------------------------------------------


def write_new_key(path: Path) -> None:
    """Create a 32-byte key file with 0600 permissions. Never prints it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SystemExit(f"refusing to overwrite an existing key file: {path}")
    path.write_bytes(os.urandom(KEY_BYTES))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_key(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) != KEY_BYTES:
        raise SystemExit(
            f"key file {path} must contain exactly {KEY_BYTES} raw bytes "
            f"(got {len(raw)}); create one with the `keygen` command"
        )
    return raw


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, MAGIC)


def decrypt(blob: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC):
        raise SystemExit("not a Platrixa backup (bad magic)")
    nonce = blob[len(MAGIC): len(MAGIC) + NONCE_BYTES]
    ciphertext = blob[len(MAGIC) + NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ciphertext, MAGIC)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Binaries
# ---------------------------------------------------------------------------


def find_binary(name: str, pg_bin: Optional[str] = None) -> str:
    if pg_bin:
        candidate = Path(pg_bin) / name
        if candidate.exists():
            return str(candidate)
        raise SystemExit(f"{name} not found under --pg-bin {pg_bin}")
    found = shutil.which(name)
    if found:
        return found
    try:  # the disposable test package ships a matching server + client
        import pgserver

        candidate = Path(pgserver.__file__).resolve().parent / "pginstall" / "bin" / name
        if candidate.exists():
            return str(candidate)
    except Exception:
        pass
    raise SystemExit(
        f"{name} not found. Install the PostgreSQL 18 client "
        f"(postgresql-client-18) or pass --pg-bin /path/to/pg/bin"
    )


def _psql_scalar(url: str, sql: str, pg_bin: Optional[str]) -> str:
    psql = find_binary("psql", pg_bin)
    result = subprocess.run(
        [psql, url, "-t", "-A", "-c", sql],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"psql failed: {result.stderr.strip()[:400]}")
    return result.stdout.strip()


def _server_version(url: str, pg_bin: Optional[str]) -> str:
    return _psql_scalar(url, "SHOW server_version", pg_bin).strip()


def _table_counts(url: str, pg_bin: Optional[str]) -> dict:
    """EXACT row counts per public table.

    ``pg_stat_user_tables.n_live_tup`` is a planner estimate refreshed by
    autovacuum/ANALYZE; immediately after a write it can read 0. A manifest
    that records an estimate cannot be used to prove a restore was complete,
    so every table is counted explicitly. The table set is small, so N+1
    queries cost nothing at backup time.
    """
    raw = _psql_scalar(
        url,
        "SELECT COALESCE(json_agg(tablename ORDER BY tablename), '[]'::json) "
        "FROM pg_tables WHERE schemaname = 'public'",
        pg_bin,
    ) or "[]"
    try:
        names = [str(n) for n in json.loads(raw)]
    except Exception:
        return {}
    counts: dict = {}
    for name in names:
        safe = name.replace('"', '""')
        try:
            counts[name] = int(
                _psql_scalar(url, f'SELECT count(*) FROM public."{safe}"', pg_bin) or 0
            )
        except Exception:
            counts[name] = -1  # could not be counted: stated, never guessed
    return counts


def _database_name(url: str) -> str:
    # postgresql://user:pass@host:port/dbname
    tail = url.rsplit("/", 1)[-1]
    return tail.split("?", 1)[0] or "unknown"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_create(args) -> int:
    key = load_key(Path(args.key_file))
    out = Path(args.out)
    pg_dump = find_binary("pg_dump", args.pg_bin)
    started = time.time()

    if out.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite {out} (use --force)")

    counts = _table_counts(args.database_url, args.pg_bin)
    version = _server_version(args.database_url, args.pg_bin)

    with tempfile.TemporaryDirectory(prefix="platrixa-backup-") as tmp:
        dump_path = Path(tmp) / "dump.pgc"
        proc = subprocess.run(
            [pg_dump, "--format=custom", "--no-owner", "--no-privileges",
             "--file", str(dump_path), args.database_url],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"pg_dump failed: {proc.stderr.strip()[:600]}")
        plaintext = dump_path.read_bytes()

    ciphertext = encrypt(plaintext, key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ciphertext)

    manifest = {
        "format": "platrixa-pgbackup-v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "database": _database_name(args.database_url),
        "server_version": version,
        "pg_dump_bytes": len(plaintext),
        "plaintext_sha256": sha256(plaintext),
        "ciphertext_sha256": sha256(ciphertext),
        "encryption": "AES-256-GCM",
        "table_counts": counts,
        "restore_requires_empty_target": True,
    }
    manifest_path = Path(str(out) + MANIFEST_SUFFIX)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    try:
        manifest_path.chmod(0o600)
        out.chmod(0o600)
    except OSError:
        pass

    # Read back immediately: a backup that has not been read is not a backup.
    if sha256(out.read_bytes()) != manifest["ciphertext_sha256"]:
        raise SystemExit("backup verification failed immediately after write")
    print(json.dumps({**manifest, "backup": str(out), "manifest": str(manifest_path)}, indent=2))
    return 0


def cmd_verify(args) -> int:
    key = load_key(Path(args.key_file))
    backup = Path(args.backup)
    manifest_path = Path(args.manifest) if args.manifest else Path(str(backup) + MANIFEST_SUFFIX)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    blob = backup.read_bytes()
    problems = []
    if sha256(blob) != manifest.get("ciphertext_sha256"):
        problems.append("ciphertext hash mismatch (file was modified or truncated)")

    plaintext: Optional[bytes]
    try:
        plaintext = decrypt(blob, key)
    except SystemExit as exc:  # bad magic
        plaintext = None
        problems.append(str(exc))
    except Exception as exc:
        # AES-GCM authentication failure: the bytes were altered. Report it as
        # an integrity finding instead of crashing with a traceback.
        plaintext = None
        problems.append(f"decryption failed ({type(exc).__name__}): ciphertext was altered")

    if plaintext is not None:
        if sha256(plaintext) != manifest.get("plaintext_sha256"):
            problems.append("plaintext hash mismatch after decryption")

        pg_restore = find_binary("pg_restore", args.pg_bin)
        with tempfile.TemporaryDirectory(prefix="platrixa-verify-") as tmp:
            dump_path = Path(tmp) / "dump.pgc"
            dump_path.write_bytes(plaintext)
            listing = subprocess.run(
                [pg_restore, "--list", str(dump_path)], capture_output=True, text=True
            )
            if listing.returncode != 0:
                problems.append(
                    f"pg_restore cannot read the archive: {listing.stderr.strip()[:300]}"
                )

    result = {
        "backup": str(backup),
        "ok": not problems,
        "problems": problems,
        "database": manifest.get("database"),
        "server_version": manifest.get("server_version"),
        "created_at": manifest.get("created_at"),
        "table_counts": manifest.get("table_counts"),
    }
    print(json.dumps(result, indent=2))
    return 0 if not problems else 1


def cmd_restore(args) -> int:
    key = load_key(Path(args.key_file))
    backup = Path(args.backup)
    manifest_path = Path(str(backup) + MANIFEST_SUFFIX)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_version = str(manifest.get("server_version", "")).split(".")[0]
        target_version = _server_version(args.target_url, args.pg_bin).split(".")[0]
        if expected_version and target_version and expected_version != target_version:
            raise SystemExit(
                f"refusing cross-major restore: backup is PostgreSQL {expected_version}, "
                f"target is {target_version}"
            )

    if not args.force:
        objects = _psql_scalar(
            args.target_url,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p')",
            args.pg_bin,
        )
        try:
            n_objects = int(objects)
        except ValueError:
            n_objects = 0
        if n_objects > 0:
            raise SystemExit(
                f"target database already has {n_objects} relation(s) in public; "
                "refusing to restore over it (use --force only for a disposable target)"
            )

    plaintext = decrypt(backup.read_bytes(), key)
    pg_restore = find_binary("pg_restore", args.pg_bin)
    with tempfile.TemporaryDirectory(prefix="platrixa-restore-") as tmp:
        dump_path = Path(tmp) / "dump.pgc"
        dump_path.write_bytes(plaintext)
        proc = subprocess.run(
            [pg_restore, "--no-owner", "--no-privileges", "--exit-on-error",
             "--dbname", args.target_url, str(dump_path)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"pg_restore failed: {proc.stderr.strip()[:600]}")

    print(json.dumps({
        "restored": True,
        "backup": str(backup),
        "target": _database_name(args.target_url),
        "tables": sorted(_table_counts(args.target_url, args.pg_bin).keys()),
    }, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa encrypted PostgreSQL backup/restore")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen")
    p.add_argument("--out", required=True)

    p = sub.add_parser("create")
    p.add_argument("--database-url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--key-file", required=True)
    p.add_argument("--pg-bin", default=None)
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("verify")
    p.add_argument("--backup", required=True)
    p.add_argument("--key-file", required=True)
    p.add_argument("--manifest", default=None)
    p.add_argument("--pg-bin", default=None)

    p = sub.add_parser("restore")
    p.add_argument("--backup", required=True)
    p.add_argument("--key-file", required=True)
    p.add_argument("--target-url", required=True)
    p.add_argument("--pg-bin", default=None)
    p.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "keygen":
        write_new_key(Path(args.out))
        print(json.dumps({"key_file": args.out, "bytes": KEY_BYTES, "note": "value never printed"}))
        return 0
    if args.command == "create":
        return cmd_create(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "restore":
        return cmd_restore(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
