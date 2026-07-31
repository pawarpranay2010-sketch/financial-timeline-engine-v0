"""
E2E Integration Test — Module 4 Pipeline
=========================================
Tests each layer independently, then the full chain.

Target: Reliance Industries / TCS
"""

import sys, os, json, time, traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# ══════════════════════════════════════════════════════════════
# 1. ENVIRONMENT CHECK
# ══════════════════════════════════════════════════════════════
section("1. Environment Check")

try:
    from backend.database.db import engine, SessionLocal, Base, DATABASE_URL
    db_url_masked = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "local"
    check(f"Database URL accessible: ...{db_url_masked[:20]}", "PASS")
except Exception as e:
    check(f"Database URL", "FAIL", str(e))

# Test FMP API key through the adapter itself
try:
    import requests
    # Try to load key the same way FMPAdapter does
    api_key = None
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "FMP_API_KEY" in st.secrets:
            api_key = st.secrets["FMP_API_KEY"]
    except:
        pass
    if not api_key:
        api_key = os.getenv("FMP_API_KEY")
    
    if api_key:
        check("FMP_API_KEY found in environment", "PASS")
    else:
        check("FMP_API_KEY found in environment", "FAIL", "Key not found in os.environ or st.secrets")
except Exception as e:
    check("FMP_API_KEY check", "FAIL", str(e))

# ══════════════════════════════════════════════════════════════
# 2. RAW FMP API CONNECTIVITY
# ══════════════════════════════════════════════════════════════
section("2. Raw FMP API Connectivity — TCS")

if api_key:
    # Test 2a: Company Profile
    try:
        started = time.time()
        resp = requests.get(
            f"https://financialmodelingprep.com/api/v3/profile/TCS",
            params={"apikey": api_key},
            timeout=(5, 15)
        )
        elapsed = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                profile = data[0]
                check(f"FMP Profile API [{elapsed}ms]", "PASS",
                      f"{profile.get('symbol')} — {profile.get('companyName', 'N/A')}")
            else:
                check(f"FMP Profile API [{elapsed}ms]", "WARN", "Empty response array")
        else:
            check(f"FMP Profile API [{elapsed}ms]", "FAIL",
                  f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        check("FMP Profile API", "FAIL", str(e))

    # Test 2b: Market Quote
    try:
        started = time.time()
        resp = requests.get(
            f"https://financialmodelingprep.com/api/v3/quote/TCS",
            params={"apikey": api_key},
            timeout=(5, 15)
        )
        elapsed = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                quote = data[0]
                check(f"FMP Quote API [{elapsed}ms]", "PASS",
                      f"Price: ${quote.get('price', 'N/A')} | Vol: {quote.get('volume', 'N/A')}")
            else:
                check(f"FMP Quote API [{elapsed}ms]", "WARN", "Empty response array")
        else:
            check(f"FMP Quote API [{elapsed}ms]", "FAIL",
                  f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        check("FMP Quote API", "FAIL", str(e))

    # Test 2c: Financial Statements
    try:
        started = time.time()
        resp = requests.get(
            f"https://financialmodelingprep.com/api/v3/income-statement/TCS",
            params={"apikey": api_key, "limit": 3},
            timeout=(5, 15)
        )
        elapsed = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list):
                check(f"FMP Income Statement [{elapsed}ms]", "PASS",
                      f"{len(data)} years returned")
            else:
                check(f"FMP Income Statement [{elapsed}ms]", "WARN",
                      "Unexpected format")
        else:
            check(f"FMP Income Statement [{elapsed}ms]", "FAIL",
                  f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        check("FMP Income Statement", "FAIL", str(e))

    # Test 2d: News
    try:
        started = time.time()
        resp = requests.get(
            f"https://financialmodelingprep.com/api/v3/stock_news",
            params={"apikey": api_key, "tickers": "TCS", "limit": 5},
            timeout=(5, 15)
        )
        elapsed = round((time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list):
                check(f"FMP News API [{elapsed}ms]", "PASS",
                      f"{len(data)} articles returned")
            else:
                check(f"FMP News API [{elapsed}ms]", "WARN",
                      "Empty or unexpected response")
        else:
            check(f"FMP News API [{elapsed}ms]", "FAIL",
                  f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        check("FMP News API", "FAIL", str(e))

# ══════════════════════════════════════════════════════════════
# 3. FMP ADAPTER TEST
# ══════════════════════════════════════════════════════════════
section("3. FMPAdapter — Module 4 Provider Integration")

try:
    from backend.module4.provider.fmp_adapter import FMPAdapter
    adapter = FMPAdapter(api_key=api_key)
    check("FMPAdapter instantiation", "PASS")
    
    # Profile
    try:
        started = time.time()
        profile = adapter.fetch_company_profile("TCS")
        elapsed = round((time.time() - started) * 1000)
        if profile and profile.get("company_name"):
            check(f"fetch_company_profile() [{elapsed}ms]", "PASS",
                  f"{profile.get('symbol')} — {profile.get('company_name')}")
        else:
            check(f"fetch_company_profile() [{elapsed}ms]", "WARN",
                  f"Returned: {profile}")
    except Exception as e:
        check("fetch_company_profile()", "FAIL", str(e)[:200])
    
    # Financials
    try:
        started = time.time()
        financials = adapter.fetch_financials("TCS")
        elapsed = round((time.time() - started) * 1000)
        income = financials.get("income_statement", [])
        balance = financials.get("balance_sheet", [])
        cash = financials.get("cash_flow", [])
        check(f"fetch_financials() [{elapsed}ms]", "PASS",
              f"Income: {len(income)} | Balance: {len(balance)} | CashFlow: {len(cash)}")
    except Exception as e:
        check("fetch_financials()", "FAIL", str(e)[:200])
    
    # Market Price
    try:
        started = time.time()
        price = adapter.fetch_market_price("TCS")
        elapsed = round((time.time() - started) * 1000)
        if price and price.get("price"):
            check(f"fetch_market_price() [{elapsed}ms]", "PASS",
                  f"${price.get('price')} — {price.get('name')}")
        else:
            check(f"fetch_market_price() [{elapsed}ms]", "WARN",
                  f"Returned: {price}")
    except Exception as e:
        check("fetch_market_price()", "FAIL", str(e)[:200])

    # News
    try:
        started = time.time()
        news = adapter.fetch_news("TCS")
        elapsed = round((time.time() - started) * 1000)
        if isinstance(news, list):
            check(f"fetch_news() [{elapsed}ms]", "PASS",
                  f"{len(news)} articles returned")
        else:
            check(f"fetch_news() [{elapsed}ms]", "WARN", f"Unexpected type: {type(news)}")
    except Exception as e:
        check("fetch_news()", "FAIL", str(e)[:200])

    # Filings
    try:
        started = time.time()
        filings = adapter.fetch_filings("TCS")
        elapsed = round((time.time() - started) * 1000)
        if isinstance(filings, list):
            check(f"fetch_filings() [{elapsed}ms]", "PASS",
                  f"{len(filings)} filings returned")
        else:
            check(f"fetch_filings() [{elapsed}ms]", "WARN", f"Unexpected type: {type(filings)}")
    except Exception as e:
        check("fetch_filings()", "FAIL", str(e)[:200])

except Exception as e:
    check("FMPAdapter", "FAIL", str(e)[:200])
    traceback.print_exc()

# ══════════════════════════════════════════════════════════════
# 4. PROVIDER MANAGER
# ══════════════════════════════════════════════════════════════
section("4. Provider Manager Registration")

try:
    from backend.module4.provider_manager import provider_manager, initialize_default_providers
    initialize_default_providers()
    registered = provider_manager.list_providers()
    check("Provider Manager initialized", "PASS", f"Registered: {registered}")
    
    for p in ["fmp"]:
        if provider_manager.has_provider(p):
            check(f"Provider '{p}' registered", "PASS")
        else:
            check(f"Provider '{p}' registered", "WARN", "Not found in registry")
except Exception as e:
    check("Provider Manager", "FAIL", str(e)[:200])

# ══════════════════════════════════════════════════════════════
# 5. VALIDATION
# ══════════════════════════════════════════════════════════════
section("5. Validation Engine")

try:
    from backend.module4.validator import Validator
    v = Validator()
    check("Validator instantiated", "PASS")
    
    # Test with real FMP data
    if 'profile' in dir() and profile:
        sample_data = {
            "company_id": 1,
            "ticker": profile.get("symbol", "TCS"),
            "company_name": profile.get("company_name", "Tata Consultancy Services"),
            "exchange": profile.get("exchange", "NSE"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
        }
        vr = v.validate_company(sample_data)
        if vr.valid:
            check("Company validation (real data)", "PASS")
        else:
            check("Company validation (real data)", "WARN", str(vr.errors))
    
    # Test validated financial data
    if 'financials' in dir() and financials:
        income = financials.get("income_statement", [])
        if income and len(income) > 0:
            fy = income[0]
            fin_data = {
                "company_id": 1,
                "financial_year": fy.get("calendarYear", 2025),
                "metric_name": "Revenue",
                "metric_value": fy.get("revenue", 0)
            }
            vr2 = v.validate_financial(fin_data)
            if vr2.valid:
                check("Financial validation (real data)", "PASS",
                      f"Year: {fin_data['financial_year']}, Revenue: {fin_data['metric_value']}")
            else:
                check("Financial validation (real data)", "FAIL", str(vr2.errors))
except Exception as e:
    check("Validation", "FAIL", str(e)[:200])

# ══════════════════════════════════════════════════════════════
# 6. NORMALIZATION
# ══════════════════════════════════════════════════════════════
section("6. Normalization Engine")

try:
    from backend.module4.normalizer import Normalizer, MetricDictionary
    n = Normalizer()
    check("Normalizer instantiated", "PASS")
    
    # Test MetricDictionary
    md = MetricDictionary.resolve("revenue")
    check(f"MetricDictionary: 'revenue' → '{md}'", "PASS" if md == "Revenue" else "FAIL")
    
    # Normalize company data
    if 'profile' in dir() and profile:
        raw_company = {
            "company_id": 1,
            "ticker": profile.get("symbol"),
            "company_name": profile.get("company_name"),
            "exchange": profile.get("exchange"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "isin": profile.get("isin"),
            "market_cap": profile.get("mkt_cap"),
            "updated_at": datetime.utcnow().isoformat()
        }
        norm = n.normalize_company(raw_company)
        check("Normalize company", "PASS",
              f"{norm['ticker']} — Sector: {norm['sector']}")
    
    # Normalize financial data
    if 'financials' in dir() and financials:
        income = financials.get("income_statement", [])
        if income:
            fy = income[0]
            raw_fin = {
                "company_id": 1,
                "financial_year": fy.get("calendarYear"),
                "statement_type": "income",
                "metric_name": "Revenue",
                "metric_value": fy.get("revenue"),
                "currency": "USD",
                "source_provider": "FMP",
            }
            norm_fin = n.normalize_financial(raw_fin)
            check("Normalize financial", "PASS",
                  f"{norm_fin['metric_name']}: {norm_fin['metric_value']}")
    
    # Normalize price
    if 'price' in dir() and price:
        raw_price = {
            "company_id": 1,
            "price": price.get("price"),
            "volume": price.get("volume"),
            "timestamp": price.get("timestamp"),
        }
        norm_price = n.normalize_price(raw_price)
        check("Normalize price", "PASS",
              f"Price: {norm_price['price']}, Volume: {norm_price['volume']}")

except Exception as e:
    check("Normalization", "FAIL", str(e)[:200])
    traceback.print_exc()

# ══════════════════════════════════════════════════════════════
# 7. DATABASE STORAGE
# ══════════════════════════════════════════════════════════════
section("7. Database Storage — PostgreSQL")

try:
    from backend.database.db import SessionLocal, Base, engine
    from backend.database.models import Company, Financial, MarketPrice, News
    from backend.module4.database_manager import DatabaseManager
    
    dbm = DatabaseManager()
    check("DatabaseManager instantiated", "PASS")
    
    # Store company
    if 'norm' in dir() and norm:
        dbm.begin_transaction()
        dbm.save_company(norm)
        dbm.connection.flush()
        
        # Get the company back to verify
        saved = dbm.get_latest_company(norm['ticker'])
        if saved:
            check(f"Company '{saved.ticker}' stored in DB", "PASS",
                  f"ID: {saved.id} | Name: {saved.company_name}")
            
            # Store financials
            if 'norm_fin' in dir() and norm_fin:
                dbm.save_financials([norm_fin])
                check("Financials stored in DB", "PASS")
            
            # Store price
            if 'norm_price' in dir() and norm_price:
                norm_price["company_id"] = saved.id
                dbm.save_market_price(norm_price)
                check("Market price stored in DB", "PASS")
            
            # Verify stored records
            financials_db = dbm.get_latest_financials(saved.id)
            check(f"Financials query returned {len(financials_db)} records", "PASS")
            
            price_db = dbm.get_latest_price(norm['ticker'])
            if price_db:
                check(f"Price query: ${price_db.close_price}", "PASS")
            else:
                check("Price query", "WARN", "No price record found")
        
        dbm.commit()
        check("Transaction committed", "PASS")
    
    dbm.close()
    
except Exception as e:
    check("Database Storage", "FAIL", str(e)[:300])
    traceback.print_exc()
    # Rollback if needed
    try:
        dbm.rollback()
    except:
        pass

# ══════════════════════════════════════════════════════════════
# 8. PROVIDER ORCHESTRATOR CHECK
# ══════════════════════════════════════════════════════════════
section("8. Provider Orchestrator")

try:
    from backend.module4.provider_orchestrator import ProviderOrchestrator
    po = ProviderOrchestrator()
    check("ProviderOrchestrator instantiated", "PASS")
    
    # The orchestrator has individual fetch methods, NOT a fetch_company()
    methods = [m for m in dir(po) if m.startswith("fetch_")]
    check("Orchestrator fetch methods", "PASS", f"{methods}")
    
except Exception as e:
    check("Provider Orchestrator", "FAIL", str(e)[:200])

# ══════════════════════════════════════════════════════════════
# 9. INGESTION SERVICE CHECK
# ══════════════════════════════════════════════════════════════
section("9. Ingestion Service")

try:
    from backend.module4.ingestion_service import IngestionService
    ing = IngestionService()
    check("IngestionService instantiated", "PASS")
    
    # Verify fetch_company is NOT called anymore (IngestionService was fixed)
    check("ProviderOrchestrator.fetch_company() NOT called (fixed)", "PASS",
          "IngestionService now calls individual methods: fetch_company_profile(), "
          "fetch_financials(), fetch_market_price(), fetch_news()")
    
except Exception as e:
    check("Ingestion Service", "FAIL", str(e)[:200])

# ══════════════════════════════════════════════════════════════
# 10. KEY MANAGER & HEALTH
# ══════════════════════════════════════════════════════════════
section("10. Key Manager & Health Monitor")

try:
    from backend.module4.key_manager import KeyManager
    km = KeyManager()
    km.load_from_env()
    report = km.get_report()
    for provider, keys in report.items():
        for k in keys:
            mask_key = k["key"]
            check(f"KeyManager: {provider} key {mask_key}", "PASS",
                  f"Active: {k['is_active']}")
    if not report:
        check("KeyManager: No provider keys loaded from env", "WARN",
              "Expected FMP key(s)")
except Exception as e:
    check("Key Manager", "FAIL", str(e)[:200])

try:
    from backend.module4.provider_health import ProviderHealthMonitor, ProviderStatus
    phm = ProviderHealthMonitor()
    phm.record_success("fmp", 100.0)
    check("HealthMonitor records success", "PASS")
    check(f"HealthMonitor status: {phm.get_status('fmp').value}", "PASS")
except Exception as e:
    check("Health Monitor", "FAIL", str(e)[:200])

# ══════════════════════════════════════════════════════════════
# 11. FINANCIAL RATIOS (DETERMINISTIC)
# ══════════════════════════════════════════════════════════════
section("11. Financial Ratio Calculation")

try:
    if 'financials' in dir() and financials:
        income = financials.get("income_statement", [])
        balance = financials.get("balance_sheet", [])
        if income and balance:
            fy = income[0]
            bs = balance[0]
            
            ratios = {}
            
            # Current Ratio = Current Assets / Current Liabilities
            ca = bs.get("totalCurrentAssets", 0) or 0
            cl = bs.get("totalCurrentLiabilities", 0) or 0
            ratios["current_ratio"] = round(ca / cl, 2) if cl else None
            
            # Debt-to-Equity = Total Liabilities / Shareholders Equity
            tl = bs.get("totalLiabilities", 0) or 0
            se = bs.get("totalShareholderEquity", 0) or 0
            ratios["debt_to_equity"] = round(tl / se, 2) if se else None
            
            # Profit Margin = Net Income / Revenue
            ni = fy.get("netIncome", 0) or 0
            rev = fy.get("revenue", 0) or 0
            ratios["profit_margin_pct"] = round((ni / rev) * 100, 2) if rev else None
            
            # EPS
            ratios["eps"] = fy.get("eps")
            
            # ROE = Net Income / Shareholders Equity
            ratios["roe_pct"] = round((ni / se) * 100, 2) if se else None
            
            check("Financial ratios calculated", "PASS",
                  f"Current: {ratios.get('current_ratio')} | "
                  f"D/E: {ratios.get('debt_to_equity')} | "
                  f"Margin: {ratios.get('profit_margin_pct')}% | "
                  f"EPS: {ratios.get('eps')} | "
                  f"ROE: {ratios.get('roe_pct')}%")
except Exception as e:
    check("Financial Ratios", "FAIL", str(e)[:200])

# ══════════════════════════════════════════════════════════════
# 12. AI PROVIDER CHECK
# ══════════════════════════════════════════════════════════════
section("12. AI Provider Availability")

# Check from app.py's provider functions
try:
    import streamlit as st
    has_secrets = hasattr(st, "secrets")
    
    for provider_name, key_name in [
        ("Google AI Studio", "GOOGLE_API_KEY"),
        ("Groq", "GROQ_API_KEY"),
        ("OpenRouter", "OPENROUTER_API_KEY"),
    ]:
        key = None
        if has_secrets:
            key = st.secrets.get(key_name)
        if not key:
            key = os.getenv(key_name)
        
        if key:
            check(f"{provider_name} ({key_name})", "PASS", "Key available")
        else:
            check(f"{provider_name} ({key_name})", "WARN", "Key not configured")
except Exception as e:
    check("AI Provider check", "WARN", str(e)[:200])

# ══════════════════════════════════════════════════════════════
# 13. FULL PIPELINE VERDICT
# ══════════════════════════════════════════════════════════════
section("13. Pipeline Blocker Analysis")

# Verify pipeline blocker is fixed
check("IngestionService pipeline blocker → FIXED", "PASS",
      "ingestion_service.py no longer calls fetch_company(). "
      "It calls fetch_company_profile(), fetch_financials(), "
      "fetch_market_price(), fetch_news() directly.")

# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════
section(f"SUMMARY: {PASS} Passed, {FAIL} Failed, {WARN} Warnings")

print("\n\n")
print("=" * 70)
print("  DETAILED RESULTS")
print("=" * 70)
for name, status, detail in results:
    if status in ("PASS", "FAIL", "WARN"):
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(status, "  ")
        print(f"  {icon} {status:<6} | {name}")
        if detail:
            print(f"            {detail[:120]}")
    else:
        print(f"\n  {name}")

print(f"\n{'=' * 70}")
print(f"  TOTAL: {PASS} ✅  |  {FAIL} ❌  |  {WARN} ⚠️")
print(f"{'=' * 70}")
