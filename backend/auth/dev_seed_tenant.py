"""Development-only tenant seeding for the metered gate (Phase 16).

Creates a tenant quota record against the metering store and prints the
raw API key EXACTLY ONCE, to stdout. The database receives only the
SHA-256 hash.

This is a developer/test utility — it must never be used to provision
production tenants silently. It refuses to run against a store that
already contains the generated hash (idempotent rejection, no overwrite)
and never reads, prints, or logs any existing key material.

Run:
    PLATRIXA_METERING_DATABASE_URL=postgresql://... \
        python -m backend.auth.dev_seed_tenant --tenant dev-local --limit 5
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys


def seed_tenant(tenant_id: str, monthly_limit: int) -> int:
    from sqlalchemy import create_engine, text

    from backend.auth.models import current_usage_month
    from backend.auth.tokens import hash_token

    url = (os.getenv("PLATRIXA_METERING_DATABASE_URL", "") or "").strip()
    if not url:
        print("❌ PLATRIXA_METERING_DATABASE_URL is not set.")
        return 1
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    # High-entropy random token — the honest precondition for hash-only
    # storage (see tokens.py). 32 bytes of URL-safe entropy.
    raw_key = "plx_" + secrets.token_urlsafe(32)
    key_hash = hash_token(raw_key)
    month = current_usage_month()

    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT tenant_id FROM platrixa_tenant_quotas WHERE api_key_hash = :h"),
            {"h": key_hash},
        ).first()
        if existing is not None:
            # Astronomically unlikely (2^-256); never overwrite.
            print("❌ generated key already exists; refusing to overwrite.")
            return 1
        conn.execute(
            text(
                "INSERT INTO platrixa_tenant_quotas "
                "(api_key_hash, tenant_id, monthly_limit, current_month_usage, usage_month, is_active) "
                "VALUES (:h, :t, :l, 0, :m, TRUE)"
            ),
            {"h": key_hash, "t": tenant_id, "l": monthly_limit, "m": month},
        )

    print("✅ tenant created (dev/test only — not a production provisioning tool)")
    print(f"   tenant_id:    {tenant_id}")
    print(f"   monthly_limit:{monthly_limit}")
    print(f"   usage_month:  {month}")
    print("   API key (shown ONCE, stored only as a hash):")
    print(f"   {raw_key}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Dev-only tenant quota seeding")
    parser.add_argument("--tenant", required=True, help="tenant identifier")
    parser.add_argument("--limit", type=int, default=100, help="monthly request limit")
    args = parser.parse_args()
    if args.limit < 1:
        print("❌ --limit must be >= 1")
        return 1
    return seed_tenant(args.tenant, args.limit)


if __name__ == "__main__":
    sys.exit(main())
