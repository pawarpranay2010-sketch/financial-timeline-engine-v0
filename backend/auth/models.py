"""Tenant quota model for the metered developer gate (Phase 16).

Registered on the SAME declarative ``Base`` as every other Platrixa
model (backend/database/db.py), following the repository's existing
model conventions (explicit Column types, comments, defaults).

Table: ``platrixa_tenant_quotas`` — one row per developer API key
(keyed by its SHA-256 hash; the raw key is NEVER stored).

Monthly semantics: ``usage_month`` holds a deterministic ``YYYY-MM``
(UTC) bucket and ``current_month_usage`` counts units within that
bucket. The atomic reservation in gate.py resets the bucket inside the
same UPDATE statement when the month rolls over — no background
scheduler, and August usage can never be mistaken for September usage.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from backend.database.db import Base


def current_usage_month() -> str:
    """The deterministic UTC month bucket, e.g. ``"2026-09"``."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


class TenantQuota(Base):
    """Admission-control record for one developer API key (one tenant)."""

    __tablename__ = "platrixa_tenant_quotas"

    # Deterministic SHA-256 hex digest of the developer API key.
    # The raw key never reaches this table (or any other).
    api_key_hash = Column(
        String(64),
        primary_key=True,
        index=True,
        comment="SHA-256 hex digest of the developer API key (never the raw key)",
    )

    # Stable tenant identity, resolved from the authenticated credential.
    # Clients cannot supply it — it always comes from the stored row.
    tenant_id = Column(
        String(64),
        nullable=False,
        index=True,
        comment="Tenant identifier resolved from the authenticated key",
    )

    monthly_limit = Column(
        Integer,
        nullable=False,
        default=100,
        comment="Maximum admitted requests per monthly bucket",
    )

    current_month_usage = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Units consumed within the current usage_month bucket",
    )

    usage_month = Column(
        String(7),
        nullable=False,
        default=current_usage_month,
        comment="UTC 'YYYY-MM' bucket that current_month_usage counts against",
    )

    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Deactivated keys authenticate-but-never-admit (401)",
    )

    last_request_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp of the most recent admitted request",
    )

    def to_safe_dict(self) -> dict:
        """Serialisable view WITHOUT any key material (raw or hashed)."""
        return {
            "tenant_id": self.tenant_id,
            "monthly_limit": self.monthly_limit,
            "current_month_usage": self.current_month_usage,
            "usage_month": self.usage_month,
            "is_active": self.is_active,
            "last_request_at": self.last_request_at.isoformat()
            if self.last_request_at
            else None,
        }
