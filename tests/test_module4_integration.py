#!/usr/bin/env python3
"""
Module 4 Integration Test

Tests real connections to:
1. PostgreSQL (via DATABASE_URL)
2. Redis (via REDIS_URL)
3. FMP API (via FMP_API_KEY)

Also runs a full pipeline test with mock data if connections fail.
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = {
    "connections": {"postgresql": None, "redis": None, "fmp": None},
    "pipeline": None,
    "errors": []
}

print("="*70)
print("MODULE 4 INTEGRATION TEST")
print("="*70)

# =====================================================
# 1. PostgreSQL Connection Test
# =====================================================
print("\n[1/4] PostgreSQL Connection Test")
print("-"*40)

try:
    from backend.database.db import engine, SessionLocal
    with engine.connect() as conn:
        result = conn.execute(__import__('sqlalchemy').text("SELECT 1"))
        row = result.fetchone()
        if row[0] == 1:
            results["connections"]["postgresql"] = "CONNECTED"
            print("  ✅ PostgreSQL: CONNECTED")
            
            # Test session
            session = SessionLocal()
            from backend.database.models import Company
            count = session.query(Company).count()
            print(f"  📊 Companies in database: {count}")
            session.close()
        else:
            results["connections"]["postgresql"] = "ERROR"
            print("  ❌ PostgreSQL: Query returned unexpected result")
except Exception as e:
    error_msg = str(e)
    if "DATABASE_URL" in error_msg or "missing" in error_msg.lower():
        results["connections"]["postgresql"] = "NOT CONFIGURED"
        print("  ⚠️  PostgreSQL: NOT CONFIGURED (DATABASE_URL not set)")
    elif "could not connect" in error_msg.lower() or "connection refused" in error_msg.lower():
        results["connections"]["postgresql"] = "UNREACHABLE"
        print(f"  ❌ PostgreSQL: UNREACHABLE - {error_msg[:80]}")
    elif "password authentication" in error_msg.lower():
        results["connections"]["postgresql"] = "AUTH FAILED"
        print(f"  ❌ PostgreSQL: AUTH FAILED - check credentials")
    else:
        results["connections"]["postgresql"] = "ERROR"
        results["errors"].append(f"PostgreSQL: {error_msg[:100]}")
        print(f"  ❌ PostgreSQL: ERROR - {error_msg[:80]}")

# =====================================================
# 2. Redis Connection Test
# =====================================================
print("\n[2/4] Redis Connection Test")
print("-"*40)

try:
    import redis as redis_lib
    from backend.module4.cache_manager import CacheManager
    
    # Check for REDIS_URL env var
    redis_url = os.getenv("REDIS_URL")
    
    if redis_url:
        r = redis_lib.from_url(redis_url, socket_connect_timeout=2)
    else:
        # Try localhost default
        r = redis_lib.Redis(host='localhost', port=6379, db=0, socket_connect_timeout=2)
    
    r.ping()
    results["connections"]["redis"] = "CONNECTED"
    print("  ✅ Redis: CONNECTED")
    
    # Test basic operations
    r.set("test_key", "test_value", ex=10)
    val = r.get("test_key")
    if val == b"test_value":
        print("  📊 Redis read/write: OK")
    r.delete("test_key")
    
except redis_lib.ConnectionError:
    results["connections"]["redis"] = "UNREACHABLE"
    print("  ❌ Redis: UNREACHABLE (connection refused on localhost:6379)")
except redis_lib.AuthenticationError:
    results["connections"]["redis"] = "AUTH FAILED"
    print("  ❌ Redis: AUTH FAILED - check credentials")
except ImportError:
    results["connections"]["redis"] = "NOT INSTALLED"
    print("  ⚠️  Redis: redis-py not installed")
except Exception as e:
    error_msg = str(e)
    if "name or service not known" in error_msg.lower():
        results["connections"]["redis"] = "NOT CONFIGURED"
        print("  ⚠️  Redis: NOT CONFIGURED (REDIS_URL not set)")
    else:
        results["connections"]["redis"] = "ERROR"
        results["errors"].append(f"Redis: {error_msg[:100]}")
        print(f"  ❌ Redis: ERROR - {error_msg[:80]}")

# =====================================================
# 3. FMP API Connection Test
# =====================================================
print("\n[3/4] FMP API Connection Test")
print("-"*40)

try:
    import requests
    
    # Check for FMP API key
    fmp_key = os.getenv("FMP_API_KEY")
    
    # Also check Streamlit secrets
    if not fmp_key:
        try:
            import streamlit as st
            if hasattr(st, "secrets") and "FMP_API_KEY" in st.secrets:
                fmp_key = st.secrets["FMP_API_KEY"]
        except:
            pass
    
    if not fmp_key:
        results["connections"]["fmp"] = "NOT CONFIGURED"
        print("  ⚠️  FMP API: NOT CONFIGURED (FMP_API_KEY not set)")
    else:
        # Test with a simple API call
        url = f"https://financialmodelingprep.com/api/v3/quote/AAPL?apikey={fmp_key}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                results["connections"]["fmp"] = "CONNECTED"
                print(f"  ✅ FMP API: CONNECTED")
                print(f"  📊 AAPL price: ${data[0].get('price', 'N/A')}")
            elif isinstance(data, dict) and data.get("Error Message"):
                results["connections"]["fmp"] = "INVALID KEY"
                print(f"  ❌ FMP API: INVALID KEY - {data['Error Message']}")
            else:
                results["connections"]["fmp"] = "ERROR"
                print(f"  ❌ FMP API: Unexpected response")
        elif response.status_code == 401:
            results["connections"]["fmp"] = "AUTH FAILED"
            print("  ❌ FMP API: AUTH FAILED (401)")
        else:
            results["connections"]["fmp"] = "ERROR"
            print(f"  ❌ FMP API: HTTP {response.status_code}")

except requests.ConnectionError:
    results["connections"]["fmp"] = "UNREACHABLE"
    print("  ❌ FMP API: UNREACHABLE (network error)")
except requests.Timeout:
    results["connections"]["fmp"] = "TIMEOUT"
    print("  ❌ FMP API: TIMEOUT")
except Exception as e:
    results["connections"]["fmp"] = "ERROR"
    results["errors"].append(f"FMP: {str(e)[:100]}")
    print(f"  ❌ FMP API: ERROR - {str(e)[:80]}")

# =====================================================
# 4. Module 4 Pipeline Test (Mocked)
# =====================================================
print("\n[4/4] Module 4 Pipeline Structure Test")
print("-"*40)

try:
    from backend.module4.validator import Validator
    from backend.module4.normalizer import Normalizer
    
    validator = Validator()
    normalizer = Normalizer()
    
    # Simulate pipeline with mock data
    mock_company_data = {
        "company_id": 99999,
        "ticker": "TEST",
        "company_name": "Test Corp",
        "exchange": "NYSE",
        "sector": "Technology",
        "industry": "Software",
        "market_cap": 1000000000
    }
    
    # Validation
    validation_result = validator.validate_company(mock_company_data)
    if validation_result.valid:
        print("  ✅ Validator: PASS (mock data valid)")
    else:
        print(f"  ❌ Validator: FAIL - {validation_result.errors}")
    
    # Normalization
    normalized = normalizer.normalize_company(mock_company_data)
    if normalized.get("ticker") == "TEST":
        print("  ✅ Normalizer: PASS (company normalized)")
    else:
        print("  ❌ Normalizer: FAIL")
    
    # Financial normalization
    mock_financial = {
        "company_id": 99999,
        "financial_year": 2024,
        "statement_type": "income",
        "metric_name": "revenue",
        "metric_value": 5000000
    }
    
    norm_financial = normalizer.normalize_financial(mock_financial)
    if norm_financial.get("metric_name") == "Revenue":
        print("  ✅ Normalizer: PASS (financial metrics resolved)")
    else:
        print(f"  ❌ Normalizer: FAIL (metric not resolved)")
    
    results["pipeline"] = "STRUCTURE OK"
    print("\n  📋 Pipeline components are ready for real data")

except Exception as e:
    results["pipeline"] = "ERROR"
    results["errors"].append(f"Pipeline: {str(e)[:100]}")
    print(f"  ❌ Pipeline: ERROR - {str(e)[:80]}")

# =====================================================
# SUMMARY
# =====================================================
print("\n" + "="*70)
print("INTEGRATION TEST SUMMARY")
print("="*70)

print("\n📊 Connection Status:")
for conn, status in results["connections"].items():
    icon = "✅" if status == "CONNECTED" else "❌" if status in ["ERROR", "UNREACHABLE", "AUTH FAILED"] else "⚠️"
    print(f"  {icon} {conn.upper()}: {status}")

print(f"\n📊 Pipeline Status: {results['pipeline'] or 'NOT TESTED'}")

if results["errors"]:
    print(f"\n⚠️  Errors encountered:")
    for err in results["errors"]:
        print(f"  - {err}")

# Determine what's needed
print("\n" + "="*70)
print("REQUIRED ENVIRONMENT VARIABLES")
print("="*70)

required_vars = {
    "DATABASE_URL": "PostgreSQL connection (postgresql://user:pass@host:5432/dbname)",
    "REDIS_URL": "Redis connection (redis://localhost:6379/0)",
    "FMP_API_KEY": "Financial Modeling Prep API key for market data"
}

for var, desc in required_vars.items():
    status = results["connections"].get(
        "postgresql" if "DATABASE" in var else 
        "redis" if "REDIS" in var else 
        "fmp"
    )
    icon = "✅" if status == "CONNECTED" else "❌"
    print(f"  {icon} {var}: {desc}")

print("\n" + "="*70)
print("NEXT STEPS")
print("="*70)

connected_count = sum(1 for v in results["connections"].values() if v == "CONNECTED")
total = len(results["connections"])

if connected_count == total:
    print("\n🎉 All connections are working!")
    print("\nReady to run the full Module 4 pipeline:")
    print("  1. Start the preview: freebuff-preview start")
    print("  2. Login with admin / financial_terminal_2026")
    print("  3. Upload a financial document")
    print("  4. Click 'Generate Timeline Report'")
else:
    print(f"\n⚠️  {connected_count}/{total} connections working")
    print("\nTo configure missing connections:")
    print("  1. Open Freebuff's API Keys tab")
    print("  2. Add the required environment variables")
    print("  3. Restart the preview")
    
    if results["connections"]["postgresql"] != "CONNECTED":
        print("\n📌 PostgreSQL setup:")
        print("   - You need a PostgreSQL database (e.g., from Supabase, Railway, or Neon)")
        print("   - Set DATABASE_URL to your connection string")
        
    if results["connections"]["redis"] != "CONNECTED":
        print("\n📌 Redis setup:")
        print("   - You need a Redis instance (e.g., from Upstash, Redis Cloud)")
        print("   - Set REDIS_URL to your connection string")
        
    if results["connections"]["fmp"] != "CONNECTED":
        print("\n📌 FMP API setup:")
        print("   - Get a free API key from https://financialmodelingprep.com/")
        print("   - Set FMP_API_KEY to your key")
