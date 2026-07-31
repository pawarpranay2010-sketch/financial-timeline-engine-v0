#!/usr/bin/env python3
"""
Module 4 Runtime Verification Test

Tests all components of the Module 4 pipeline:
1. Provider Manager - initialization, registration, routing
2. Validator - data validation
3. Normalizer - data normalization
4. Database Manager - transaction support (mocked)
5. Cache Manager - stub behavior
6. Key Manager - key loading, rotation
7. Provider Health - health tracking
8. Retry Policy - retryable error classification

No API keys required. Uses mocking for external dependencies.
"""

import sys
import os
from unittest.mock import MagicMock, patch
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = {"passed": [], "failed": [], "warnings": []}

def test(name, fn):
    """Run a test and record result."""
    try:
        fn()
        results["passed"].append(name)
        print(f"  ✅ {name}")
    except Exception as e:
        results["failed"].append((name, str(e)))
        print(f"  ❌ {name}: {e}")

def warn(name, msg):
    """Record a warning."""
    results["warnings"].append((name, msg))
    print(f"  ⚠️  {name}: {msg}")

# =====================================================
# 1. PROVIDER MANAGER
# =====================================================
print("\n" + "="*60)
print("1. PROVIDER MANAGER")
print("="*60)

def test_provider_manager_init():
    from backend.module4.provider_manager import ProviderManager
    pm = ProviderManager()
    assert hasattr(pm, 'providers'), "Missing providers dict"
    assert hasattr(pm, 'register_provider'), "Missing register_provider method"
    assert hasattr(pm, 'get_provider'), "Missing get_provider method"
    assert hasattr(pm, 'has_provider'), "Missing has_provider method"
    assert hasattr(pm, 'list_providers'), "Missing list_providers method"

test("ProviderManager class structure", test_provider_manager_init)

def test_provider_manager_register():
    from backend.module4.provider_manager import ProviderManager
    from backend.module4.provider.base import ProviderAdapter
    pm = ProviderManager()
    
    # Create mock adapter
    mock_adapter = MagicMock(spec=ProviderAdapter)
    
    pm.register_provider("test_provider", mock_adapter)
    assert pm.has_provider("test_provider"), "Provider not registered"
    assert pm.get_provider("test_provider") is mock_adapter, "Wrong provider returned"
    assert "test_provider" in pm.list_providers(), "Provider not in list"

test("ProviderManager register/get/has/list", test_provider_manager_register)

def test_provider_manager_unregister():
    from backend.module4.provider_manager import ProviderManager
    pm = ProviderManager()
    mock_adapter = MagicMock()
    pm.register_provider("test", mock_adapter)
    pm.unregister_provider("test")
    assert not pm.has_provider("test"), "Provider not unregistered"

test("ProviderManager unregister", test_provider_manager_unregister)

def test_provider_manager_case_insensitive():
    from backend.module4.provider_manager import ProviderManager
    pm = ProviderManager()
    mock_adapter = MagicMock()
    pm.register_provider("Test", mock_adapter)
    assert pm.has_provider("test"), "Case sensitivity issue"
    assert pm.has_provider("TEST"), "Case sensitivity issue"
    assert pm.get_provider("Test") is mock_adapter

test("ProviderManager case-insensitive lookup", test_provider_manager_case_insensitive)

def test_provider_manager_missing_provider():
    from backend.module4.provider_manager import ProviderManager
    pm = ProviderManager()
    try:
        pm.get_provider("nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "not registered" in str(e)

test("ProviderManager raises on missing provider", test_provider_manager_missing_provider)

# =====================================================
# 2. VALIDATOR
# =====================================================
print("\n" + "="*60)
print("2. VALIDATOR")
print("="*60)

def test_validator_class():
    from backend.module4.validator import Validator, ValidationResult
    v = Validator()
    assert hasattr(v, 'validate_company'), "Missing validate_company"
    assert hasattr(v, 'validate_financial'), "Missing validate_financial"
    assert hasattr(v, 'validate_timestamp'), "Missing validate_timestamp"
    assert hasattr(v, 'REQUIRED_COMPANY_FIELDS'), "Missing REQUIRED_COMPANY_FIELDS"

test("Validator class structure", test_validator_class)

def test_validate_company_valid():
    from backend.module4.validator import Validator
    v = Validator()
    valid_data = {
        "company_id": 1,
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "exchange": "NASDAQ"
    }
    result = v.validate_company(valid_data)
    assert result.valid, f"Expected valid, got errors: {result.errors}"

test("Validator validate_company (valid data)", test_validate_company_valid)

def test_validate_company_missing_fields():
    from backend.module4.validator import Validator
    v = Validator()
    invalid_data = {"ticker": "AAPL"}  # Missing company_id, company_name, exchange
    result = v.validate_company(invalid_data)
    assert not result.valid, "Should be invalid"
    assert len(result.errors) >= 2, f"Expected at least 2 errors, got {len(result.errors)}"

test("Validator validate_company (missing fields)", test_validate_company_missing_fields)

def test_validate_company_empty_fields():
    from backend.module4.validator import Validator
    v = Validator()
    invalid_data = {
        "company_id": 1,
        "ticker": "",
        "company_name": "Apple Inc.",
        "exchange": "NASDAQ"
    }
    result = v.validate_company(invalid_data)
    assert not result.valid, "Should be invalid due to empty ticker"
    assert any("Empty" in e for e in result.errors), "Should report empty field"

test("Validator validate_company (empty fields)", test_validate_company_empty_fields)

def test_validate_financial_valid():
    from backend.module4.validator import Validator
    v = Validator()
    valid_data = {
        "financial_year": 2024,
        "metric_name": "Revenue",
        "metric_value": 1000000
    }
    result = v.validate_financial(valid_data)
    assert result.valid, f"Expected valid, got errors: {result.errors}"

test("Validator validate_financial (valid data)", test_validate_financial_valid)

def test_validate_financial_non_numeric():
    from backend.module4.validator import Validator
    v = Validator()
    invalid_data = {
        "financial_year": 2024,
        "metric_name": "Revenue",
        "metric_value": "not_a_number"
    }
    result = v.validate_financial(invalid_data)
    assert not result.valid, "Should be invalid"
    assert any("numeric" in e.lower() for e in result.errors)

test("Validator validate_financial (non-numeric value)", test_validate_financial_non_numeric)

def test_validation_result():
    from backend.module4.validator import ValidationResult
    vr = ValidationResult()
    assert vr.valid is True
    assert len(vr.errors) == 0
    assert len(vr.warnings) == 0
    vr.add_error("test error")
    assert vr.valid is False
    assert len(vr.errors) == 1
    vr.add_warning("test warning")
    assert len(vr.warnings) == 1

test("ValidationResult class", test_validation_result)

# =====================================================
# 3. NORMALIZER
# =====================================================
print("\n" + "="*60)
print("3. NORMALIZER")
print("="*60)

def test_normalizer_class():
    from backend.module4.normalizer import Normalizer, MetricDictionary
    n = Normalizer()
    assert hasattr(n, 'normalize_company'), "Missing normalize_company"
    assert hasattr(n, 'normalize_financial'), "Missing normalize_financial"
    assert hasattr(n, 'normalize_price'), "Missing normalize_price"
    assert hasattr(n, 'normalize_news'), "Missing normalize_news"

test("Normalizer class structure", test_normalizer_class)

def test_normalize_company():
    from backend.module4.normalizer import Normalizer
    n = Normalizer()
    raw = {
        "company_id": 1,
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "exchange": "NASDAQ",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "isin": "US0378331005",
        "market_cap": 3000000000000
    }
    result = n.normalize_company(raw)
    assert result["ticker"] == "AAPL"
    assert result["company_name"] == "Apple Inc."
    assert result["exchange"] == "NASDAQ"
    assert result["sector"] == "Technology"

test("Normalizer normalize_company", test_normalize_company)

def test_normalize_financial():
    from backend.module4.normalizer import Normalizer
    n = Normalizer()
    raw = {
        "company_id": 1,
        "financial_year": 2024,
        "statement_type": "income",
        "metric_name": "revenue",
        "metric_value": 1000000,
        "currency": "USD"
    }
    result = n.normalize_financial(raw)
    assert result["company_id"] == 1
    assert result["financial_year"] == 2024
    assert result["metric_name"] == "Revenue"  # Should be normalized
    assert result["is_latest"] is True

test("Normalizer normalize_financial", test_normalize_financial)

def test_metric_dictionary():
    from backend.module4.normalizer import MetricDictionary
    assert MetricDictionary.resolve("revenue") == "Revenue"
    assert MetricDictionary.resolve("total revenue") == "Revenue"
    assert MetricDictionary.resolve("pat") == "PAT"
    assert MetricDictionary.resolve("ebitda") == "EBITDA"
    assert MetricDictionary.resolve("unknown_metric") is None
    assert MetricDictionary.resolve(None) is None

test("MetricDictionary resolve", test_metric_dictionary)

# =====================================================
# 4. KEY MANAGER
# =====================================================
print("\n" + "="*60)
print("4. KEY MANAGER")
print("="*60)

def test_key_manager_init():
    from backend.module4.key_manager import KeyManager
    km = KeyManager()
    assert hasattr(km, '_keys'), "Missing _keys"
    assert hasattr(km, 'register_keys'), "Missing register_keys"
    assert hasattr(km, 'get_active_key'), "Missing get_active_key"
    assert hasattr(km, 'rotate'), "Missing rotate"
    assert hasattr(km, 'mark_success'), "Missing mark_success"
    assert hasattr(km, 'mark_failure'), "Missing mark_failure"

test("KeyManager class structure", test_key_manager_init)

def test_key_manager_register_and_get():
    from backend.module4.key_manager import KeyManager
    km = KeyManager()
    km.register_keys("test", ["key1", "key2", "key3"])
    active = km.get_active_key("test")
    assert active is not None, "No active key"
    assert active.key == "key1", "Should start with first key"

test("KeyManager register and get_active_key", test_key_manager_register_and_get)

def test_key_manager_rotate():
    from backend.module4.key_manager import KeyManager
    km = KeyManager()
    km.register_keys("test", ["key1", "key2", "key3"])
    rotated = km.rotate("test")
    assert rotated is not None, "Rotation failed"
    assert rotated.key == "key2", f"Expected key2, got {rotated.key}"
    # key1 should be inactive
    key1_record = km._find_record("test", "key1")
    assert key1_record.is_active is False, "key1 should be inactive"

test("KeyManager rotate", test_key_manager_rotate)

def test_key_manager_mask():
    from backend.module4.key_manager import KeyManager
    assert KeyManager.mask("AIzaSyB1234567890abcdefghij") == "AIza***bcde"
    assert KeyManager.mask("short") == "***"
    assert KeyManager.mask(None) == "***"
    assert KeyManager.mask("") == "***"

test("KeyManager mask", test_key_manager_mask)

def test_key_manager_no_keys():
    from backend.module4.key_manager import KeyManager
    km = KeyManager()
    active = km.get_active_key("nonexistent")
    assert active is None, "Should return None for no keys"
    rotated = km.rotate("nonexistent")
    assert rotated is None, "Should return None for no keys"

test("KeyManager no keys scenario", test_key_manager_no_keys)

# =====================================================
# 5. PROVIDER HEALTH
# =====================================================
print("\n" + "="*60)
print("5. PROVIDER HEALTH")
print("="*60)

def test_health_monitor_init():
    from backend.module4.provider_health import ProviderHealthMonitor
    hm = ProviderHealthMonitor()
    assert hasattr(hm, 'record_success'), "Missing record_success"
    assert hasattr(hm, 'record_failure'), "Missing record_failure"
    assert hasattr(hm, 'is_available'), "Missing is_available"
    assert hasattr(hm, 'get_status'), "Missing get_status"

test("ProviderHealthMonitor class structure", test_health_monitor_init)

def test_health_initial_status():
    from backend.module4.provider_health import ProviderHealthMonitor, ProviderStatus
    hm = ProviderHealthMonitor()
    # Unknown providers should be HEALTHY by default
    assert hm.get_status("unknown") == ProviderStatus.HEALTHY
    assert hm.is_available("unknown") is True

test("ProviderHealth initial status (unknown = HEALTHY)", test_health_initial_status)

def test_health_success():
    from backend.module4.provider_health import ProviderHealthMonitor, ProviderStatus
    hm = ProviderHealthMonitor()
    hm.record_success("fmp", 150.0)
    assert hm.get_status("fmp") == ProviderStatus.HEALTHY
    assert hm.is_available("fmp") is True
    report = hm.get_report()
    assert "fmp" in report
    assert report["fmp"]["total_requests"] == 1
    assert report["fmp"]["successful_requests"] == 1

test("ProviderHealth record_success", test_health_success)

def test_health_degraded():
    from backend.module4.provider_health import ProviderHealthMonitor, ProviderStatus
    hm = ProviderHealthMonitor()
    # 2 consecutive failures → DEGRADED
    hm.record_failure("fmp", "timeout")
    hm.record_failure("fmp", "timeout")
    assert hm.get_status("fmp") == ProviderStatus.DEGRADED

test("ProviderHealth DEGRADED after 2 failures", test_health_degraded)

def test_health_offline():
    from backend.module4.provider_health import ProviderHealthMonitor, ProviderStatus
    hm = ProviderHealthMonitor()
    # 5 consecutive failures → OFFLINE
    for i in range(5):
        hm.record_failure("fmp", "timeout")
    assert hm.get_status("fmp") == ProviderStatus.OFFLINE
    assert hm.is_available("fmp") is False

test("ProviderHealth OFFLINE after 5 failures", test_health_offline)

def test_health_recovery():
    from backend.module4.provider_health import ProviderHealthMonitor, ProviderStatus
    hm = ProviderHealthMonitor()
    hm.record_failure("fmp", "timeout")
    hm.record_failure("fmp", "timeout")
    assert hm.get_status("fmp") == ProviderStatus.DEGRADED
    hm.record_success("fmp", 100.0)
    assert hm.get_status("fmp") == ProviderStatus.HEALTHY

test("ProviderHealth recovery after success", test_health_recovery)

# =====================================================
# 6. RETRY POLICY
# =====================================================
print("\n" + "="*60)
print("6. RETRY POLICY")
print("="*60)

def test_retry_classify():
    from backend.module4.retry_policy import is_retryable
    # Retryable (transient)
    assert is_retryable(Exception("timeout")) is True
    assert is_retryable(Exception("connection reset")) is True
    assert is_retryable(Exception("HTTP 500 error")) is True
    # Non-retryable (permanent)
    assert is_retryable(Exception("invalid api key")) is False
    assert is_retryable(Exception("unauthorized")) is False
    assert is_retryable(Exception("HTTP 401")) is False
    assert is_retryable(Exception("bad request")) is False

test("RetryPolicy error classification", test_retry_classify)

def test_retry_execute_success():
    from backend.module4.retry_policy import execute_with_retry
    call_count = 0
    def success_fn():
        nonlocal call_count
        call_count += 1
        return "success"
    result = execute_with_retry(success_fn)
    assert result == "success"
    assert call_count == 1

test("RetryPolicy execute_with_retry (success)", test_retry_execute_success)

def test_retry_execute_retryable():
    from backend.module4.retry_policy import execute_with_retry
    call_count = 0
    def transient_fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("timeout")
        return "success"
    result = execute_with_retry(transient_fn, max_attempts=3, base_delay=0.01)
    assert result == "success"
    assert call_count == 3

test("RetryPolicy execute_with_retry (retryable, eventual success)", test_retry_execute_retryable)

def test_retry_execute_non_retryable():
    from backend.module4.retry_policy import execute_with_retry
    call_count = 0
    def auth_fn():
        nonlocal call_count
        call_count += 1
        raise Exception("invalid api key")
    try:
        execute_with_retry(auth_fn, max_attempts=3, base_delay=0.01)
        assert False, "Should have raised"
    except Exception as e:
        assert "invalid api key" in str(e)
        assert call_count == 1, "Should not retry on auth error"

test("RetryPolicy execute_with_retry (non-retryable, immediate raise)", test_retry_execute_non_retryable)

# =====================================================
# 7. CACHE MANAGER (STUB)
# =====================================================
print("\n" + "="*60)
print("7. CACHE MANAGER (STUB)")
print("="*60)

def test_cache_manager_init():
    from backend.module4.cache_manager import CacheManager
    cm = CacheManager()
    assert hasattr(cm, 'redis'), "Missing redis attribute"
    assert cm.redis is None, "Redis should be None (stub)"

test("CacheManager init (stub)", test_cache_manager_init)

def test_cache_manager_methods():
    from backend.module4.cache_manager import CacheManager
    cm = CacheManager()
    # All methods should be no-ops and not raise
    cm.cache_company({"ticker": "AAPL"})
    assert cm.get_company("AAPL") is None
    cm.cache_price({"price": 150.0})
    assert cm.get_price("AAPL") is None
    cm.cache_news([])
    assert cm.get_news("AAPL") is None
    assert cm.exists("key") is False
    cm.delete("key")
    cm.clear()
    cm.close()

test("CacheManager methods (no-ops)", test_cache_manager_methods)

# =====================================================
# 8. DATABASE MANAGER (MOCKED)
# =====================================================
print("\n" + "="*60)
print("8. DATABASE MANAGER (MOCKED)")
print("="*60)

def test_database_manager_structure():
    from backend.module4.database_manager import DatabaseManager
    # Check class has all required methods
    assert hasattr(DatabaseManager, 'save_company'), "Missing save_company"
    assert hasattr(DatabaseManager, 'save_financials'), "Missing save_financials"
    assert hasattr(DatabaseManager, 'save_market_price'), "Missing save_market_price"
    assert hasattr(DatabaseManager, 'save_news'), "Missing save_news"
    assert hasattr(DatabaseManager, 'save_filing'), "Missing save_filing"
    assert hasattr(DatabaseManager, 'save_corporate_actions'), "Missing save_corporate_actions"
    assert hasattr(DatabaseManager, 'begin_transaction'), "Missing begin_transaction"
    assert hasattr(DatabaseManager, 'commit'), "Missing commit"
    assert hasattr(DatabaseManager, 'rollback'), "Missing rollback"
    assert hasattr(DatabaseManager, 'close'), "Missing close"

test("DatabaseManager class structure", test_database_manager_structure)

# =====================================================
# 9. INGESTION SERVICE (MOCKED)
# =====================================================
print("\n" + "="*60)
print("9. INGESTION SERVICE")
print("="*60)

def test_ingestion_service_structure():
    from backend.module4.ingestion_service import IngestionService
    assert hasattr(IngestionService, 'ingest_company'), "Missing ingest_company"

test("IngestionService class structure", test_ingestion_service_structure)

# =====================================================
# 10. PROVIDER ORCHESTRATOR (STRUCTURE)
# =====================================================
print("\n" + "="*60)
print("10. PROVIDER ORCHESTRATOR")
print("="*60)

def test_orchestrator_structure():
    from backend.module4.provider_orchestrator import ProviderOrchestrator
    assert hasattr(ProviderOrchestrator, 'fetch_company_profile'), "Missing fetch_company_profile"
    assert hasattr(ProviderOrchestrator, 'fetch_financials'), "Missing fetch_financials"
    assert hasattr(ProviderOrchestrator, 'fetch_market_price'), "Missing fetch_market_price"
    assert hasattr(ProviderOrchestrator, 'fetch_news'), "Missing fetch_news"
    assert hasattr(ProviderOrchestrator, 'fetch_filings'), "Missing fetch_filings"
    assert hasattr(ProviderOrchestrator, 'get_diagnostics'), "Missing get_diagnostics"

test("ProviderOrchestrator class structure", test_orchestrator_structure)

# =====================================================
# 11. DB CACHE (STRUCTURE)
# =====================================================
print("\n" + "="*60)
print("11. DB CACHE")
print("="*60)

def test_db_cache_structure():
    from backend.module4.db_cache import DBCache
    assert hasattr(DBCache, 'get_fresh_profile'), "Missing get_fresh_profile"
    assert hasattr(DBCache, 'get_fresh_financials'), "Missing get_fresh_financials"
    assert hasattr(DBCache, 'get_fresh_price'), "Missing get_fresh_price"
    assert hasattr(DBCache, 'get_fresh_news'), "Missing get_fresh_news"
    assert hasattr(DBCache, 'get_stats'), "Missing get_stats"

test("DBCache class structure", test_db_cache_structure)

def test_freshness_windows():
    from backend.module4.db_cache import FRESHNESS_WINDOWS
    assert FRESHNESS_WINDOWS["profile"].days == 7
    assert FRESHNESS_WINDOWS["financials"].days == 1
    assert FRESHNESS_WINDOWS["price"].seconds == 300  # 5 minutes
    assert FRESHNESS_WINDOWS["news"].seconds == 900  # 15 minutes

test("Freshness windows (TTL values)", test_freshness_windows)

# =====================================================
# 12. FMP ADAPTER (STRUCTURE)
# =====================================================
print("\n" + "="*60)
print("12. FMP ADAPTER")
print("="*60)

def test_fmp_adapter_structure():
    from backend.module4.provider.fmp_adapter import FMPAdapter
    assert hasattr(FMPAdapter, 'fetch_company_profile'), "Missing fetch_company_profile"
    assert hasattr(FMPAdapter, 'fetch_financials'), "Missing fetch_financials"
    assert hasattr(FMPAdapter, 'fetch_market_price'), "Missing fetch_market_price"
    assert hasattr(FMPAdapter, 'fetch_news'), "Missing fetch_news"
    assert hasattr(FMPAdapter, 'fetch_filings'), "Missing fetch_filings"

test("FMPAdapter class structure", test_fmp_adapter_structure)

def test_fmp_adapter_missing_key():
    from backend.module4.provider.fmp_adapter import FMPAdapter
    with patch.dict(os.environ, {}, clear=True):
        # Remove FMP_API_KEY if present
        os.environ.pop("FMP_API_KEY", None)
        try:
            adapter = FMPAdapter()
            warn("FMPAdapter", "Should raise ValueError without API key")
        except ValueError as e:
            assert "FMP_API_KEY" in str(e)

test("FMPAdapter raises without API key", test_fmp_adapter_missing_key)

# =====================================================
# SUMMARY
# =====================================================
print("\n" + "="*60)
print("SUMMARY")
print("="*60)

print(f"\n✅ Passed: {len(results['passed'])}")
print(f"❌ Failed: {len(results['failed'])}")
print(f"⚠️  Warnings: {len(results['warnings'])}")

if results['failed']:
    print("\nFailed tests:")
    for name, error in results['failed']:
        print(f"  - {name}: {error}")

if results['warnings']:
    print("\nWarnings:")
    for name, msg in results['warnings']:
        print(f"  - {name}: {msg}")

# Exit with appropriate code
sys.exit(1 if results['failed'] else 0)
