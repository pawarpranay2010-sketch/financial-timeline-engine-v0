"""
FMP Integration Test
====================
Tests all 5 FMP endpoints through the FMPAdapter with the fixed key-loading logic.

The adapter now correctly falls through:
    Constructor param → st.secrets → os.environ

Logs key source and masked key before making API calls.
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging to see the [FMPAdapter] key-source log
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

PASS = 0
FAIL = 0
WARN = 0
results = []

def check(name, status, detail=""):
    global PASS, FAIL, WARN
    if status == "PASS":
        PASS += 1
        results.append((name, "✅ PASS", detail))
    elif status == "WARN":
        WARN += 1
        results.append((name, "⚠️ WARN", detail))
    else:
        FAIL += 1
        results.append((name, "❌ FAIL", detail))

def section(title):
    results.append(("─" * 60, "", ""))
    results.append((f"  {title}", "", ""))
    results.append(("─" * 60, "", ""))

section("1. Load FMP Key via Fixed Adapter")

try:
    from backend.module4.provider.fmp_adapter import FMPAdapter
    
    # Let adapter use its own priority chain (no constructor param)
    # The fix ensures it falls through to os.environ when st.secrets is unavailable
    adapter = FMPAdapter()
    key = adapter.api_key
    
    masked = f"{key[:4]}***{key[-4:]}" if key and len(key) >= 8 else "***"
    
    # Determine source by checking where the key could have come from
    import streamlit as st
    has_st_secrets = False
    try:
        has_st_secrets = hasattr(st, "secrets") and "FMP_API_KEY" in st.secrets
    except:
        pass
    
    env_key = os.environ.get("FMP_API_KEY", "")
    
    if env_key and key == env_key:
        source = "os.environ"
    else:
        source = "unknown"
    
    check(f"FMPAdapter initialized", "PASS",
          f"Key source: {source} | Masked: {masked}")
    
except ValueError as e:
    check("FMPAdapter initialization", "FAIL",
          "No API key found in any source. "
          "Set FMP_API_KEY via API Keys tab or os.environ.")
    # Can't proceed without a key
    check("All endpoint tests skipped", "FAIL", "No API key available")
    _skip_all = True
except Exception as e:
    check("FMPAdapter initialization", "FAIL", str(e)[:200])
    _skip_all = True

# ── Endpoint Tests ──
section("2. fetch_company_profile() — AAPL")

if not locals().get("_skip_all"):
    try:
        start = time.time()
        profile = adapter.fetch_company_profile("AAPL")
        elapsed = round((time.time() - start) * 1000)
        
        if profile and profile.get("company_name"):
            check(f"fetch_company_profile() [{elapsed}ms]", "PASS",
                  f"{profile.get('symbol', 'N/A')} — {profile.get('company_name', 'N/A')}")
        else:
            check(f"fetch_company_profile() [{elapsed}ms]", "WARN",
                  f"Response: {str(profile)[:100]}")
    except Exception as e:
        check("fetch_company_profile()", "FAIL", str(e)[:200])

section("3. fetch_financials() — AAPL")

if not locals().get("_skip_all"):
    try:
        start = time.time()
        financials = adapter.fetch_financials("AAPL")
        elapsed = round((time.time() - start) * 1000)
        
        income = financials.get("income_statement", [])
        balance = financials.get("balance_sheet", [])
        cashflow = financials.get("cash_flow", [])
        
        check(f"fetch_financials() [{elapsed}ms]", "PASS",
              f"Income: {len(income)} yrs | Balance: {len(balance)} yrs | CashFlow: {len(cashflow)} yrs")
        
        # Show key data points from latest year
        if income and len(income) > 0:
            latest = income[0]
            rev = latest.get("revenue", "N/A")
            ni = latest.get("netIncome", "N/A")
            fy = latest.get("calendarYear", "N/A")
            check(f"  Latest data (FY {fy})", "PASS",
                  f"Revenue: {rev:,} | Net Income: {ni:,}" if isinstance(rev, (int, float)) else f"Revenue: {rev}")
    except Exception as e:
        check("fetch_financials()", "FAIL", str(e)[:200])

section("4. fetch_market_price() — AAPL")

if not locals().get("_skip_all"):
    try:
        start = time.time()
        price = adapter.fetch_market_price("AAPL")
        elapsed = round((time.time() - start) * 1000)
        
        if price and price.get("price"):
            check(f"fetch_market_price() [{elapsed}ms]", "PASS",
                  f"${price.get('price'):.2f} | Day: ${price.get('day_low', 0):.2f}–${price.get('day_high', 0):.2f} | Vol: {price.get('volume', 'N/A')}")
        else:
            check(f"fetch_market_price() [{elapsed}ms]", "WARN",
                  f"Response: {str(price)[:100]}")
    except Exception as e:
        check("fetch_market_price()", "FAIL", str(e)[:200])

section("5. fetch_news() — AAPL")

if not locals().get("_skip_all"):
    try:
        start = time.time()
        news = adapter.fetch_news("AAPL")
        elapsed = round((time.time() - start) * 1000)
        
        if isinstance(news, list):
            articles_count = len(news)
            check(f"fetch_news() [{elapsed}ms]", "PASS",
                  f"{articles_count} articles returned")
            if articles_count > 0:
                # Show first article headline
                first = news[0]
                check(f"  Latest headline", "PASS",
                      f"{first.get('title', 'N/A')[:80]}..." if len(first.get('title', '')) > 80 else first.get('title', 'N/A'))
        else:
            check(f"fetch_news() [{elapsed}ms]", "WARN",
                  f"Unexpected type: {type(news)}")
    except Exception as e:
        check("fetch_news()", "FAIL", str(e)[:200])

section("6. fetch_filings() — AAPL")

if not locals().get("_skip_all"):
    try:
        start = time.time()
        filings = adapter.fetch_filings("AAPL")
        elapsed = round((time.time() - start) * 1000)
        
        if isinstance(filings, list):
            check(f"fetch_filings() [{elapsed}ms]", "PASS",
                  f"{len(filings)} filings returned")
            if len(filings) > 0:
                first = filings[0]
                check(f"  Latest filing", "PASS",
                      f"{first.get('form', 'N/A')} | Date: {first.get('filling_date', 'N/A')}")
        else:
            check(f"fetch_filings() [{elapsed}ms]", "WARN",
                  f"Unexpected type: {type(filings)}")
    except Exception as e:
        check("fetch_filings()", "FAIL", str(e)[:200])

# ── Summary ──
section(f"RESULTS: {PASS} passed, {FAIL} failed, {WARN} warnings")

print("\n\n")
print("=" * 70)
print("  DETAILED RESULTS")
print("=" * 70)
for name, status, detail in results:
    if status in ("PASS", "FAIL", "WARN"):
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(status, "  ")
        print(f"  {icon} {status:<6} | {name}")
        if detail:
            print(f"            {detail[:150]}")
    else:
        print(f"\n  {name}")

print(f"\n{'=' * 70}")
print(f"  TOTAL: {PASS} ✅  |  {FAIL} ❌  |  {WARN} ⚠️")
print(f"{'=' * 70}")
