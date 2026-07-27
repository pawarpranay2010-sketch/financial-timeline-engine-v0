"""
Provider Orchestrator

Single point of contact for all external data fetches.

Source Priority (per spec — highest to lowest):
    1. PostgreSQL Cache          (DBCache — freshness-gated, no API cost)
    2. Local Vector Database     (not yet built — skipped gracefully)
    3. Official Filings          (NSE → BSE → SEBI — raise NotImplementedError
                                  until adapters are implemented)
    4. Financial APIs            (FMP — live, with key rotation + retry)
    5. News APIs                 (handled through same failover chain)

Resilience per API call:
    ┌──────────────────────────────────────────────────────────┐
    │  DBCache.get_fresh_*()          ← hit → return, done     │
    │                                 ← miss → continue        │
    │  for each provider in priority:                          │
    │      HealthMonitor.is_available()  → OFFLINE? skip       │
    │      KeyManager.get_active_key()   → inject into adapter │
    │      RetryPolicy.execute_with_retry(fn, ticker)          │
    │          success → record_success, return                │
    │          auth failure → rotate key, retry once           │
    │          transient failure → backoff, retry              │
    │          permanent failure → raise, next provider        │
    │  All providers exhausted → raise RuntimeError            │
    └──────────────────────────────────────────────────────────┘

Structured log per call:
    [Orchestrator] Provider=fmp | Type=market_price | Ticker=AAPL
                 | Status=failed | Latency=312ms | Key=FMP_***_KEY1
                 | Error=FMP HTTP Error 429 | FallbackFrom=nse
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from backend.module4.provider_manager import provider_manager
from backend.module4.key_manager import KeyManager
from backend.module4.provider_health import ProviderHealthMonitor, ProviderStatus
from backend.module4.retry_policy import execute_with_retry
from backend.module4.db_cache import DBCache
from backend.module4.database_manager import DatabaseManager
from backend.module4.logger import logger


# ---------------------------------------------------------------------------
# Default provider priority for API calls (spec: official sources first)
# ---------------------------------------------------------------------------

_DEFAULT_API_PRIORITY: List[str] = ["nse", "bse", "sebi", "fmp"]

# Substrings that identify an authentication failure → trigger key rotation
_AUTH_FRAGMENTS = [
    "invalid api key",
    "unauthorized",
    "authentication",
    "401",
    "403",
    "forbidden",
]


def _is_auth_error(error: Exception) -> bool:
    msg = str(error).lower()
    return any(fragment in msg for fragment in _AUTH_FRAGMENTS)


# ---------------------------------------------------------------------------
# Provider Orchestrator
# ---------------------------------------------------------------------------

class ProviderOrchestrator:
    """
    Coordinates PostgreSQL cache → provider failover for all data types.
    All external fetch calls in the ingestion pipeline go through this class.
    """

    def __init__(self):
        self.health = ProviderHealthMonitor()
        self.keys   = KeyManager()
        self.keys.load_from_env()

        # Dedicated read-only DB session for cache checks.
        # Separate from the DatabaseManager used by IngestionService.
        self._db    = DatabaseManager()
        self.cache  = DBCache(self._db)

        logger.info("[Orchestrator] Provider Orchestrator initialized")

    # ------------------------------------------------------------------
    # Internal: single-provider attempt (retry + key rotation)
    # ------------------------------------------------------------------

    def _try_provider(
        self,
        provider_name: str,
        method: str,
        ticker: str,
        data_type: str,
    ) -> Any:
        """
        Execute one provider call with:
            - OFFLINE circuit-breaker check
            - Active key selection and injection
            - RetryPolicy (exponential backoff for transient errors)
            - Auth failure → key rotation → one immediate retry
            - Health metric recording
            - Structured logging

        Raises on failure so _fetch_with_failover can advance to the
        next provider.
        """
        if not provider_manager.has_provider(provider_name):
            raise ValueError(f"Provider '{provider_name}' not registered")

        if not self.health.is_available(provider_name):
            raise RuntimeError(
                f"Provider '{provider_name}' is OFFLINE (circuit open)"
            )

        key_record = self.keys.get_active_key(provider_name)
        key_str    = key_record.key if key_record else None

        start = time.monotonic()
        try:
            provider = provider_manager.get_provider(provider_name)
            fn       = getattr(provider, method)

            # Inject the current active key into the adapter if supported
            if key_str and hasattr(provider, "api_key"):
                provider.api_key = key_str

            result     = execute_with_retry(fn, ticker)
            latency_ms = (time.monotonic() - start) * 1000

            self.health.record_success(provider_name, latency_ms)
            if key_record:
                self.keys.mark_success(provider_name, key_record.key)

            self._log_call(
                provider=provider_name, data_type=data_type, ticker=ticker,
                status="success", latency_ms=latency_ms, key_used=key_str,
            )
            return result

        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            error_msg  = str(exc)

            # Auth failure: deactivate current key, rotate, and retry once
            if _is_auth_error(exc) and key_record:
                self.keys.mark_failure(provider_name, key_record.key, error_msg)
                rotated = self.keys.rotate(provider_name)

                if rotated:
                    logger.warning(
                        f"[Orchestrator] Auth error on '{provider_name}' — "
                        f"retrying with rotated key {self.keys.mask(rotated.key)}"
                    )
                    try:
                        provider = provider_manager.get_provider(provider_name)
                        if hasattr(provider, "api_key"):
                            provider.api_key = rotated.key
                        fn     = getattr(provider, method)
                        result = fn(ticker)   # Single retry — no backoff needed

                        rotated_latency = (time.monotonic() - start) * 1000
                        self.health.record_success(provider_name, rotated_latency)
                        self.keys.mark_success(provider_name, rotated.key)
                        self._log_call(
                            provider=provider_name, data_type=data_type,
                            ticker=ticker, status="success (key-rotated)",
                            latency_ms=rotated_latency, key_used=rotated.key,
                        )
                        return result

                    except Exception as rotated_exc:
                        error_msg = str(rotated_exc)
                        self.keys.mark_failure(
                            provider_name, rotated.key, error_msg
                        )
                        # Fall through to failure recording below

            self.health.record_failure(provider_name, error_msg)
            if key_record:
                self.keys.mark_failure(provider_name, key_record.key, error_msg)

            self._log_call(
                provider=provider_name, data_type=data_type, ticker=ticker,
                status="failed", latency_ms=latency_ms,
                key_used=key_str, error=error_msg,
            )
            raise

    # ------------------------------------------------------------------
    # Internal: failover loop
    # ------------------------------------------------------------------

    def _fetch_with_failover(
        self,
        data_type: str,
        method: str,
        ticker: str,
        providers: List[str],
    ) -> Any:
        """
        Iterate providers in priority order. Return on first success.
        Log failover events. Raise RuntimeError if all providers fail.
        """
        last_error: Optional[Exception] = None

        for i, provider_name in enumerate(providers):
            try:
                result = self._try_provider(
                    provider_name, method, ticker, data_type
                )

                # If we succeeded after at least one prior failure, record it
                if i > 0:
                    self.health.record_fallback(providers[i - 1])
                    logger.info(
                        f"[Orchestrator] Fallback succeeded: "
                        f"{providers[i - 1]} → {provider_name}"
                    )

                return result

            except Exception as exc:
                last_error = exc
                if i < len(providers) - 1:
                    logger.warning(
                        f"[Orchestrator] '{provider_name}' failed — "
                        f"failing over to '{providers[i + 1]}' | Reason: {exc}"
                    )

        raise RuntimeError(
            f"All providers exhausted for {data_type}/{ticker}. "
            f"Last error: {last_error}"
        )

    # ------------------------------------------------------------------
    # Internal: structured logging
    # ------------------------------------------------------------------

    def _log_call(
        self,
        *,
        provider: str,
        data_type: str,
        ticker: str,
        status: str,
        latency_ms: float,
        key_used: Optional[str] = None,
        error: Optional[str] = None,
        fallback_from: Optional[str] = None,
    ) -> None:
        parts = [
            f"Provider={provider}",
            f"Type={data_type}",
            f"Ticker={ticker}",
            f"Status={status}",
            f"Latency={latency_ms:.0f}ms",
        ]
        if key_used:
            parts.append(f"Key={self.keys.mask(key_used)}")
        if error:
            parts.append(f"Error={error}")
        if fallback_from:
            parts.append(f"FallbackFrom={fallback_from}")

        msg = "[Orchestrator] " + " | ".join(parts)
        if status.startswith("success"):
            logger.info(msg)
        else:
            logger.warning(msg)

    # ------------------------------------------------------------------
    # Public fetch API
    # ------------------------------------------------------------------

    def fetch_company_profile(
        self,
        ticker: str,
        providers: Optional[List[str]] = None,
    ) -> Dict:
        """
        Return company profile.
        Cache window: 7 days. Falls back through providers on miss.
        """
        cached = self.cache.get_fresh_profile(ticker)
        if cached is not None:
            return cached
        return self._fetch_with_failover(
            "company_profile", "fetch_company_profile", ticker,
            providers or _DEFAULT_API_PRIORITY,
        )

    def fetch_financials(
        self,
        ticker: str,
        providers: Optional[List[str]] = None,
    ) -> Any:
        """
        Return financial statements.
        Cache window: 24 hours. Falls back through providers on miss.
        """
        cached = self.cache.get_fresh_financials(ticker)
        if cached is not None:
            return cached
        return self._fetch_with_failover(
            "financials", "fetch_financials", ticker,
            providers or _DEFAULT_API_PRIORITY,
        )

    def fetch_market_price(
        self,
        ticker: str,
        providers: Optional[List[str]] = None,
    ) -> Dict:
        """
        Return market price.
        Cache window: 5 minutes. Falls back through providers on miss.
        """
        cached = self.cache.get_fresh_price(ticker)
        if cached is not None:
            return cached
        return self._fetch_with_failover(
            "market_price", "fetch_market_price", ticker,
            providers or _DEFAULT_API_PRIORITY,
        )

    def fetch_news(
        self,
        ticker: str,
        providers: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Return news articles.
        Cache window: 15 minutes. Falls back through providers on miss.
        """
        cached = self.cache.get_fresh_news(ticker)
        if cached is not None:
            return cached
        return self._fetch_with_failover(
            "news", "fetch_news", ticker,
            providers or _DEFAULT_API_PRIORITY,
        )

    def fetch_filings(
        self,
        ticker: str,
        providers: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Return regulatory filings.
        No DB cache layer (filings are written once and don't expire).
        Falls back through providers in priority order.
        """
        return self._fetch_with_failover(
            "filings", "fetch_filings", ticker,
            providers or _DEFAULT_API_PRIORITY,
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(self) -> Dict:
        """Combined telemetry snapshot consumed by DiagnosticsService."""
        return {
            "provider_health": self.health.get_report(),
            "api_keys":        self.keys.get_report(),
            "cache_stats":     self.cache.get_stats(),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

provider_orchestrator = ProviderOrchestrator()
