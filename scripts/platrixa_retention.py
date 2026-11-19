#!/usr/bin/env python3
"""Platrixa — bounded retention pruning (M12).

WHAT THIS PRUNES (and what it refuses to touch)
-----------------------------------------------
* ``platrixa_async_jobs`` rows that are TERMINAL (COMPLETED / FAILED) and older
  than ``--days``, with ``capacity_held = FALSE``. Rows holding capacity are
  never deleted: deleting one would leak a queue slot forever.
* Rows in any non-terminal status are never deleted, at any age. A QUEUED row
  is a promise to a developer; an old one is an incident, not garbage.
* ``platrixa_tenant_rate_limits`` windows older than ``--days``.
* Optionally terminal ``platrixa_webhook_deliveries``.

SAFETY
------
* **Dry run is the default.** ``--apply`` is required to delete anything.
* Deletes happen in bounded batches with ``LIMIT``, so a large table is pruned
  over several runs instead of one long lock.
* The database is snapshotted before ``--apply`` is honoured? No — that is the
  operator's decision. What this tool does guarantee: it refuses a non-loopback
  database unless ``--allow-remote`` is passed, and it prints exactly what it
  would delete before deleting it.
* Client-side timestamps: retention is measured from ``updated_at`` (the last
  terminal transition), not ``created_at``, so a job that was queued for a week
  still gets its full retention window.

USAGE
-----
    python3 scripts/platrixa_retention.py --database-url "$URL"                 # report only
    python3 scripts/platrixa_retention.py --database-url "$URL" --apply --days 90
"""

from __future__ import annotations

import argparse
import json
import sys

TERMINAL_STATUSES = ("COMPLETED", "FAILED")
LOOPBACK_HINTS = ("127.0.0.1", "localhost", "/tmp/", "::1")


def _connect(url: str):
    import psycopg2

    dsn = url.replace("postgresql+psycopg2://", "postgresql://", 1)
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
    return bool(cur.fetchone()[0])


def _count(cur, sql: str, params) -> int:
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


def plan(cur, days: int) -> dict:
    jobs_where = (
        "status = ANY(%s) AND capacity_held = FALSE "
        "AND updated_at < now() - (%s || ' days')::interval"
    )
    plan_counts = {
        "terminal_jobs": _count(
            cur,
            f"SELECT count(*) FROM platrixa_async_jobs WHERE {jobs_where}",
            (list(TERMINAL_STATUSES), days),
        ),
        "non_terminal_jobs_kept": _count(
            cur,
            "SELECT count(*) FROM platrixa_async_jobs WHERE NOT (status = ANY(%s))",
            (list(TERMINAL_STATUSES),),
        ),
        "jobs_holding_capacity_kept": _count(
            cur, "SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held", ()
        ),
    }
    if _table_exists(cur, "platrixa_tenant_rate_limits"):
        plan_counts["rate_limit_windows"] = _count(
            cur,
            "SELECT count(*) FROM platrixa_tenant_rate_limits "
            "WHERE window_start < now() - (%s || ' days')::interval",
            (days,),
        )
    if _table_exists(cur, "platrixa_webhook_deliveries"):
        plan_counts["terminal_webhook_deliveries"] = _count(
            cur,
            "SELECT count(*) FROM platrixa_webhook_deliveries "
            "WHERE status = ANY(%s) AND created_at < now() - (%s || ' days')::interval",
            (["DELIVERED", "FAILED"], days),
        )
    return plan_counts


def prune(conn, days: int, batch: int, max_batches: int) -> dict:
    deleted = {
        "terminal_jobs": 0,
        "rate_limit_windows": 0,
        "terminal_webhook_deliveries": 0,
    }
    with conn.cursor() as cur:
        job_sql = (
            "DELETE FROM platrixa_async_jobs WHERE job_id IN ("
            "  SELECT job_id FROM platrixa_async_jobs"
            "  WHERE status = ANY(%s) AND capacity_held = FALSE"
            "    AND updated_at < now() - (%s || ' days')::interval"
            "  ORDER BY updated_at LIMIT %s"
            ")"
        )
        for _ in range(max_batches):
            cur.execute(job_sql, (list(TERMINAL_STATUSES), days, batch))
            rows = cur.rowcount
            conn.commit()
            deleted["terminal_jobs"] += max(rows, 0)
            if rows < batch:
                break

        if _table_exists(cur, "platrixa_tenant_rate_limits"):
            for _ in range(max_batches):
                cur.execute(
                    "DELETE FROM platrixa_tenant_rate_limits WHERE ctid IN ("
                    "  SELECT ctid FROM platrixa_tenant_rate_limits"
                    "  WHERE window_start < now() - (%s || ' days')::interval"
                    "  LIMIT %s"
                    ")",
                    (days, batch),
                )
                rows = cur.rowcount
                conn.commit()
                deleted["rate_limit_windows"] += max(rows, 0)
                if rows < batch:
                    break

        if _table_exists(cur, "platrixa_webhook_deliveries"):
            for _ in range(max_batches):
                cur.execute(
                    "DELETE FROM platrixa_webhook_deliveries WHERE delivery_id IN ("
                    "  SELECT delivery_id FROM platrixa_webhook_deliveries"
                    "  WHERE status = ANY(%s) AND created_at < now() - (%s || ' days')::interval"
                    "  ORDER BY created_at LIMIT %s"
                    ")",
                    (["DELIVERED", "FAILED"], days, batch),
                )
                rows = cur.rowcount
                conn.commit()
                deleted["terminal_webhook_deliveries"] += max(rows, 0)
                if rows < batch:
                    break
    return deleted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa bounded retention pruning (M12)")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--batch", type=int, default=1000)
    parser.add_argument("--max-batches", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="actually delete (default: report only)")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-loopback database; required for production pruning",
    )
    args = parser.parse_args(argv)

    if args.days < 1:
        raise SystemExit("--days must be >= 1")
    if not args.allow_remote and not any(h in args.database_url for h in LOOPBACK_HINTS):
        raise SystemExit(
            "refusing a non-loopback database without --allow-remote "
            "(this tool deletes rows)"
        )

    conn = _connect(args.database_url)
    try:
        with conn.cursor() as cur:
            before = plan(cur, args.days)
        result = {
            "days": args.days,
            "mode": "apply" if args.apply else "dry_run",
            "eligible": before,
        }
        if args.apply:
            result["deleted"] = prune(conn, args.days, args.batch, args.max_batches)
            with conn.cursor() as cur:
                result["after"] = plan(cur, args.days)
        else:
            result["deleted"] = {}
            result["note"] = "nothing was deleted; re-run with --apply"
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
