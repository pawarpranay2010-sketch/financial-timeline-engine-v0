#!/usr/bin/env python3
"""Platrixa — private, READ-ONLY owner operational report.

Prints what the durable stores already know about the developer API:
tenants, developer labels, masked key prefixes, key lifecycle status,
request/job counts, job ids with timestamps and status, safe error
categories, monthly quota usage, and pending queue workload.

It NEVER prints or reads: raw API keys, key hashes, management credentials,
transaction text, model prompts, or candidate payloads. The connection is put
in a READ ONLY transaction, so the report cannot modify database state.

Usage:
    python3 scripts/platrixa_owner_report.py [--tenant TENANT_ID] [--limit N]

Environment:
    PLATRIXA_METERING_DATABASE_URL   the metering store (same as the API)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _session():
    from backend.auth import gate as metered_gate

    if not metered_gate._metering_configured():
        raise SystemExit(
            "PLATRIXA_METERING_DATABASE_URL is not configured; no metering store to report on"
        )
    return metered_gate._session_factory()()


def _safe_error_code(raw: Optional[str]) -> Optional[str]:
    """Only the machine-readable error code leaves the store — never a message."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    err = parsed.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        return str(code)[:64] if code else None
    code = parsed.get("code")
    return str(code)[:64] if code else None


def build_report(tenant_id: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    from sqlalchemy import text

    params: Dict[str, Any] = {"limit": max(1, min(limit, 200))}
    where = ""
    if tenant_id:
        where = " WHERE tenant_id = :t"
        params["t"] = tenant_id

    with _session() as session:
        # READ ONLY: any accidental write below raises instead of mutating.
        session.execute(text("SET TRANSACTION READ ONLY"))

        quotas = session.execute(
            text(
                "SELECT tenant_id, monthly_limit, current_month_usage, usage_month, "
                "is_active, last_request_at FROM platrixa_tenant_quotas"
                + (" WHERE tenant_id = :t" if tenant_id else "")
                + " ORDER BY tenant_id"
            ),
            {"t": tenant_id} if tenant_id else {},
        ).mappings().all()

        keys = session.execute(
            text(
                "SELECT tenant_id, name, key_prefix, environment, status, created_at "
                "FROM platrixa_api_keys"
                + (" WHERE tenant_id = :t" if tenant_id else "")
                + " ORDER BY tenant_id, created_at"
            ),
            {"t": tenant_id} if tenant_id else {},
        ).mappings().all()

        status_counts = session.execute(
            text(
                "SELECT status, count(*) AS n FROM platrixa_async_jobs"
                + where
                + " GROUP BY status ORDER BY status"
            ),
            {"t": tenant_id} if tenant_id else {},
        ).mappings().all()

        pending = session.execute(
            text(
                "SELECT status, count(*) AS n FROM platrixa_async_jobs "
                "WHERE capacity_held = TRUE" + (" AND tenant_id = :t" if tenant_id else "")
                + " GROUP BY status"
            ),
            {"t": tenant_id} if tenant_id else {},
        ).mappings().all()

        jobs = session.execute(
            text(
                "SELECT job_id, tenant_id, status, kind, created_at, updated_at, error_json "
                "FROM platrixa_async_jobs"
                + where
                + " ORDER BY created_at DESC LIMIT :limit"
            ),
            params,
        ).mappings().all()

        success = int(
            session.execute(
                text(
                    "SELECT count(*) FROM platrixa_async_jobs WHERE status = 'COMPLETED'"
                    + (" AND tenant_id = :t" if tenant_id else "")
                ),
                {"t": tenant_id} if tenant_id else {},
            ).scalar_one()
        )
        failure = int(
            session.execute(
                text(
                    "SELECT count(*) FROM platrixa_async_jobs WHERE status = 'FAILED'"
                    + (" AND tenant_id = :t" if tenant_id else "")
                ),
                {"t": tenant_id} if tenant_id else {},
            ).scalar_one()
        )

    safe_jobs: List[Dict[str, Any]] = [
        {
            "job_id": r["job_id"],
            "tenant_id": r["tenant_id"],
            "status": r["status"],
            "kind": r["kind"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "error_category": _safe_error_code(r["error_json"]),
        }
        for r in jobs
    ]

    return {
        "tenant_filter": tenant_id,
        "quota": [
            {
                "tenant_id": r["tenant_id"],
                "monthly_limit": r["monthly_limit"],
                "used": r["current_month_usage"],
                "remaining": max(int(r["monthly_limit"]) - int(r["current_month_usage"]), 0),
                "usage_month": r["usage_month"],
                "key_active": bool(r["is_active"]),
                "last_request_at": r["last_request_at"].isoformat()
                if r["last_request_at"]
                else None,
            }
            for r in quotas
        ],
        "keys": [
            {
                "tenant_id": r["tenant_id"],
                "developer_label": r["name"],
                "key_prefix": r["key_prefix"],  # masked identification only
                "environment": r["environment"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in keys
        ],
        "jobs": {
            "by_status": {str(r["status"]): int(r["n"]) for r in status_counts},
            "pending_by_status": {str(r["status"]): int(r["n"]) for r in pending},
            "pending_total": sum(int(r["n"]) for r in pending),
            "completed_total": success,
            "failed_total": failure,
            "recent": safe_jobs,
        },
        "note": (
            "read-only report: no key material, hashes, management credentials, "
            "transaction text, prompts or candidate payloads are exposed"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Private read-only owner report.")
    parser.add_argument("--tenant", default=None, help="restrict to one tenant id")
    parser.add_argument("--limit", type=int, default=20, help="recent jobs to list")
    args = parser.parse_args()
    report = build_report(args.tenant, args.limit)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
