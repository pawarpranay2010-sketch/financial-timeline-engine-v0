"""
Provider Health Monitor

Tracks per-provider runtime health: availability, consecutive failures,
rolling latency, and fallback events.

Status levels:
    HEALTHY   → 0–1 consecutive failures
    DEGRADED  → 2–4 consecutive failures (still responding, circuit half-open)
    OFFLINE   → 5+ consecutive failures  (circuit open — calls blocked)

A successful call resets consecutive_failures to 0 and restores HEALTHY.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Deque, Dict, Optional

from backend.module4.logger import logger


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------

class ProviderStatus(str, Enum):
    HEALTHY  = "healthy"
    DEGRADED = "degraded"
    OFFLINE  = "offline"


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_DEGRADED_THRESHOLD = 2   # consecutive failures → DEGRADED
_OFFLINE_THRESHOLD  = 5   # consecutive failures → OFFLINE (circuit open)
_LATENCY_WINDOW     = 20  # rolling sample size for average latency


# ---------------------------------------------------------------------------
# Health Record (per provider)
# ---------------------------------------------------------------------------

@dataclass
class HealthRecord:
    provider: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    consecutive_failures: int = 0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    fallback_count: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    last_error: Optional[str] = None
    response_times_ms: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_LATENCY_WINDOW)
    )

    def avg_latency_ms(self) -> Optional[float]:
        if not self.response_times_ms:
            return None
        return round(sum(self.response_times_ms) / len(self.response_times_ms), 1)

    def success_rate_pct(self) -> float:
        if not self.total_requests:
            return 0.0
        return round(self.successful_requests / self.total_requests * 100, 1)

    def to_report(self) -> dict:
        return {
            "status":               self.status.value,
            "consecutive_failures": self.consecutive_failures,
            "total_requests":       self.total_requests,
            "successful_requests":  self.successful_requests,
            "failed_requests":      self.failed_requests,
            "fallback_count":       self.fallback_count,
            "success_rate_pct":     self.success_rate_pct(),
            "avg_latency_ms":       self.avg_latency_ms(),
            "last_success": (
                self.last_success.isoformat() if self.last_success else None
            ),
            "last_failure": (
                self.last_failure.isoformat() if self.last_failure else None
            ),
            "last_error":           self.last_error,
        }


# ---------------------------------------------------------------------------
# Health Monitor
# ---------------------------------------------------------------------------

class ProviderHealthMonitor:
    """
    Records success / failure events per provider and maintains a rolling
    health status and latency average for each.

    Thread-safety: not required for single-threaded Streamlit deployments.
    For multi-threaded use, wrap record_* methods with a threading.Lock.
    """

    def __init__(self):
        self._records: Dict[str, HealthRecord] = {}

    def _get_or_create(self, provider: str) -> HealthRecord:
        if provider not in self._records:
            self._records[provider] = HealthRecord(provider=provider)
        return self._records[provider]

    # ------------------------------------------------------------------
    # Event Recording
    # ------------------------------------------------------------------

    def record_success(self, provider: str, latency_ms: float) -> None:
        """
        Record a successful call. Resets consecutive failure counter and
        restores the provider to HEALTHY status.
        """
        rec = self._get_or_create(provider)
        rec.total_requests += 1
        rec.successful_requests += 1
        rec.consecutive_failures = 0
        rec.last_success = datetime.utcnow()
        rec.response_times_ms.append(latency_ms)
        rec.status = ProviderStatus.HEALTHY

    def record_failure(self, provider: str, error: str) -> None:
        """
        Record a failed call. Advances status toward DEGRADED → OFFLINE as
        consecutive failures accumulate.
        """
        rec = self._get_or_create(provider)
        rec.total_requests += 1
        rec.failed_requests += 1
        rec.consecutive_failures += 1
        rec.last_failure = datetime.utcnow()
        rec.last_error = error

        if rec.consecutive_failures >= _OFFLINE_THRESHOLD:
            if rec.status != ProviderStatus.OFFLINE:
                rec.status = ProviderStatus.OFFLINE
                logger.error(
                    f"[Health] Provider '{provider}' is now OFFLINE "
                    f"({rec.consecutive_failures} consecutive failures). "
                    "Circuit open — calls will be skipped."
                )
        elif rec.consecutive_failures >= _DEGRADED_THRESHOLD:
            if rec.status == ProviderStatus.HEALTHY:
                rec.status = ProviderStatus.DEGRADED
                logger.warning(
                    f"[Health] Provider '{provider}' is DEGRADED "
                    f"({rec.consecutive_failures} consecutive failures)"
                )

    def record_fallback(self, provider: str) -> None:
        """
        Record that this provider triggered a failover to another provider.
        Called for the provider that *caused* the fallback, not the one that
        handled it.
        """
        rec = self._get_or_create(provider)
        rec.fallback_count += 1

    # ------------------------------------------------------------------
    # Status Queries
    # ------------------------------------------------------------------

    def get_status(self, provider: str) -> ProviderStatus:
        rec = self._records.get(provider)
        return rec.status if rec else ProviderStatus.HEALTHY

    def is_available(self, provider: str) -> bool:
        """
        Returns False only when a provider's circuit is open (OFFLINE).
        DEGRADED providers are still attempted — they may recover.
        """
        return self.get_status(provider) != ProviderStatus.OFFLINE

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_report(self) -> Dict:
        """Return a full per-provider health snapshot for diagnostics."""
        return {
            provider: rec.to_report()
            for provider, rec in self._records.items()
        }
