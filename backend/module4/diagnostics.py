"""
Diagnostics Service

Admin-only backend service that reports the runtime health of the
provider orchestration layer. Not exposed to end users.

Report sections:
    providers       — registration status + health per provider
    provider_health — status, success rate, avg latency, failure count, fallbacks
    api_key_usage   — masked per-key: daily calls, monthly calls, last success/failure
    cache_stats     — hit%, miss%, total queries since process start

Usage:
    from backend.module4.diagnostics import diagnostics_service
    report = diagnostics_service.get_full_report()
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

from backend.module4.provider_orchestrator import provider_orchestrator
from backend.module4.provider_manager import provider_manager
from backend.module4.logger import logger


class DiagnosticsService:
    """
    Aggregates runtime telemetry from the orchestrator layer into a
    structured admin report.
    """

    # ------------------------------------------------------------------
    # Individual sections
    # ------------------------------------------------------------------

    def get_provider_health(self) -> Dict:
        """
        Per-provider health snapshot:
            status, consecutive_failures, total_requests,
            successful_requests, failed_requests, fallback_count,
            success_rate_pct, avg_latency_ms,
            last_success, last_failure, last_error
        """
        return provider_orchestrator.health.get_report()

    def get_api_key_usage(self) -> Dict:
        """
        Per-provider API key usage (keys are masked):
            key (masked), is_active,
            daily_calls, monthly_calls,
            last_success, last_failure, last_error
        """
        return provider_orchestrator.keys.get_report()

    def get_cache_stats(self) -> Dict:
        """
        PostgreSQL cache hit / miss statistics (accumulated since process start):
            hits, misses, total, hit_rate_pct, miss_rate_pct
        """
        return provider_orchestrator.cache.get_stats()

    def get_registered_providers(self) -> Dict:
        """
        Cross-references ProviderManager (registration) with the health
        monitor (runtime status) for every registered provider.

        Fields per provider:
            registered      — always True (only registered providers appear)
            status          — healthy / degraded / offline
            avg_latency_ms  — rolling 20-sample average in milliseconds
            success_rate_pct
            total_requests
            failed_requests
            fallback_count
        """
        health_report = provider_orchestrator.health.get_report()
        registered    = provider_manager.list_providers()
        result: Dict  = {}

        for name in registered:
            health = health_report.get(name, {})
            result[name] = {
                "registered":       True,
                "status":           health.get("status", "healthy"),
                "avg_latency_ms":   health.get("avg_latency_ms"),
                "success_rate_pct": health.get("success_rate_pct", 0.0),
                "total_requests":   health.get("total_requests", 0),
                "failed_requests":  health.get("failed_requests", 0),
                "fallback_count":   health.get("fallback_count", 0),
            }

        return result

    # ------------------------------------------------------------------
    # Full report
    # ------------------------------------------------------------------

    def get_full_report(self) -> Dict:
        """
        Complete admin diagnostics snapshot.

        Returns a dict with keys:
            generated_at    — UTC ISO timestamp
            providers       — per-provider registration + health summary
            provider_health — detailed health records
            api_key_usage   — masked key usage records
            cache_stats     — hit / miss counters and rates
        """
        logger.info("[Diagnostics] Full admin report generated")
        return {
            "generated_at":    datetime.utcnow().isoformat(),
            "providers":       self.get_registered_providers(),
            "provider_health": self.get_provider_health(),
            "api_key_usage":   self.get_api_key_usage(),
            "cache_stats":     self.get_cache_stats(),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

diagnostics_service = DiagnosticsService()
