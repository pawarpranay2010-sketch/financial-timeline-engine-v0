"""
Key Manager

Centralized API key management for all external providers.

Responsibilities:
    - Load keys from environment variables (and Streamlit secrets)
    - Track per-key usage: daily calls, monthly calls
    - Track last success / last failure / last error per key
    - Rotate to the next available key on authentication failure
    - Mask keys in logs  →  first 4 chars + *** + last 4 chars

Environment variable conventions:
    FMP_API_KEY       → primary FMP key
    FMP_API_KEY_2     → secondary FMP key (optional)
    FMP_API_KEY_3     → tertiary  FMP key (optional)

    ALPHA_VANTAGE_API_KEY   / ALPHA_VANTAGE_API_KEY_2
    POLYGON_API_KEY         / POLYGON_API_KEY_2
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from backend.module4.logger import logger


# ---------------------------------------------------------------------------
# Environment variable name patterns per provider
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERNS: Dict[str, List[str]] = {
    "fmp": ["FMP_API_KEY", "FMP_API_KEY_2", "FMP_API_KEY_3"],
    "alpha": ["ALPHA_VANTAGE_API_KEY", "ALPHA_VANTAGE_API_KEY_2"],
    "polygon": ["POLYGON_API_KEY", "POLYGON_API_KEY_2"],
}


# ---------------------------------------------------------------------------
# Key Record
# ---------------------------------------------------------------------------


@dataclass
class APIKeyRecord:
    """Tracks runtime state and usage for a single API key."""

    provider: str
    key: str
    is_active: bool = True
    daily_calls: int = 0
    monthly_calls: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    last_error: Optional[str] = None

    def to_report(self, mask_fn) -> dict:
        return {
            "key": mask_fn(self.key),
            "is_active": self.is_active,
            "daily_calls": self.daily_calls,
            "monthly_calls": self.monthly_calls,
            "last_success": self.last_success.isoformat()
            if self.last_success
            else None,
            "last_failure": self.last_failure.isoformat()
            if self.last_failure
            else None,
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------------------
# Key Manager
# ---------------------------------------------------------------------------


class KeyManager:
    """
    Manages API keys for all registered providers.
    Supports multiple keys per provider with automatic round-robin rotation.
    """

    def __init__(self):
        # provider (lowercase) → ordered list of APIKeyRecord
        self._keys: Dict[str, List[APIKeyRecord]] = {}
        # provider → index of the currently active key
        self._cursor: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_keys(self, provider: str, keys: List[str]) -> None:
        """Register one or more API keys for a provider. Ignores duplicates."""
        name = provider.lower()
        if name not in self._keys:
            self._keys[name] = []
            self._cursor[name] = 0

        added = 0
        for k in keys:
            if k and not any(r.key == k for r in self._keys[name]):
                self._keys[name].append(APIKeyRecord(provider=name, key=k))
                added += 1

        if added:
            logger.info(
                f"[KeyManager] Registered {added} key(s) for provider '{name}' "
                f"(total: {len(self._keys[name])})"
            )

    def load_from_env(self) -> None:
        """
        Scan known environment variable names and Streamlit secrets for
        each provider's keys. Called once at orchestrator startup.
        """
        # Optional Streamlit secrets
        _secrets: dict = {}
        try:
            import streamlit as st

            if hasattr(st, "secrets"):
                _secrets = dict(st.secrets)
        except Exception:
            pass

        for provider, var_names in _ENV_VAR_PATTERNS.items():
            found: List[str] = []
            for var in var_names:
                value = os.getenv(var) or _secrets.get(var)
                if value:
                    found.append(value)
            if found:
                self.register_keys(provider, found)
            else:
                logger.debug(f"[KeyManager] No keys found for provider '{provider}'")

    # ------------------------------------------------------------------
    # Key Access
    # ------------------------------------------------------------------

    def get_active_key(self, provider: str) -> Optional[APIKeyRecord]:
        """
        Return the currently active key record for a provider.
        Scans from the current cursor position; returns None if no active keys.
        """
        name = provider.lower()
        records = self._keys.get(name, [])
        if not records:
            return None

        idx = self._cursor.get(name, 0)
        for offset in range(len(records)):
            candidate = records[(idx + offset) % len(records)]
            if candidate.is_active:
                return candidate

        return None  # All keys inactive

    def rotate(self, provider: str) -> Optional[APIKeyRecord]:
        """
        Deactivate the current key and advance to the next available one.

        Returns the new active key record, or None if all keys are exhausted.
        The caller should fail over to another provider if None is returned.
        """
        name = provider.lower()
        records = self._keys.get(name, [])
        if not records:
            return None

        current_idx = self._cursor.get(name, 0)
        current_key = records[current_idx]
        current_key.is_active = False
        logger.warning(
            f"[KeyManager] Deactivated key {self.mask(current_key.key)} "
            f"for provider '{name}'"
        )

        # Find the next active key after the current one
        for offset in range(1, len(records)):
            next_idx = (current_idx + offset) % len(records)
            if records[next_idx].is_active:
                self._cursor[name] = next_idx
                logger.info(
                    f"[KeyManager] Rotated to key {self.mask(records[next_idx].key)} "
                    f"for provider '{name}'"
                )
                return records[next_idx]

        logger.error(f"[KeyManager] No active keys remaining for provider '{name}'")
        return None

    # ------------------------------------------------------------------
    # Usage Tracking
    # ------------------------------------------------------------------

    def mark_success(self, provider: str, key: str) -> None:
        record = self._find_record(provider, key)
        if record:
            record.last_success = datetime.utcnow()
            record.daily_calls += 1
            record.monthly_calls += 1

    def mark_failure(self, provider: str, key: str, error: str) -> None:
        record = self._find_record(provider, key)
        if record:
            record.last_failure = datetime.utcnow()
            record.last_error = error

    def _find_record(self, provider: str, key: str) -> Optional[APIKeyRecord]:
        for record in self._keys.get(provider.lower(), []):
            if record.key == key:
                return record
        return None

    # ------------------------------------------------------------------
    # Masking
    # ------------------------------------------------------------------

    @staticmethod
    def mask(key: str) -> str:
        """Return a log-safe masked version of the API key."""
        if not key:
            return "***"
        if len(key) < 8:
            return "***"
        return f"{key[:4]}***{key[-4:]}"

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_report(self) -> Dict:
        """Return masked per-provider key usage for the diagnostics endpoint."""
        return {
            provider: [r.to_report(self.mask) for r in records]
            for provider, records in self._keys.items()
        }
