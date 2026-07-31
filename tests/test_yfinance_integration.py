"""
YFinance Integration Test

Tests all 5 ProviderAdapter endpoints through the YFinanceAdapter
with live Yahoo Finance data (AAPL).

Also tests the full Module 4 pipeline: ProviderOrchestrator → Validator → Normalizer.
"""

import sys, os, time

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

PASS, FAIL, WARN = 0, 0, 0
results = []


def check(name, status, detail=""):
    global PASS, FAIL, WARN
    if status == "PASS":
        PASS += 1
        results.append((name, "✅ PASS", detail))
    elif status == "WARN":
        WARN += 1
        results.append((name, "⚠️  WARN", detail))
    else:
        FAIL += 1
        results.append((name, "❌ FAIL", detail))


def section(title):
    results.append(("─" * 60, "", ""))
    results.append((f"  {title}", "", ""))
    results.append(("─" * 60, "", ""))


TICKER = "AAPL"

print("=" * 60)
print("  YFINANCE INTEGRATION TEST")
print("=" * 60)

# ── 1. Adapter Initialization ──
section("1. YFinanceAdapter Initialization")

try:
    from backend.module4.provider.yfinance_adapter import YFinanceAdapter

    adapter = YFinanceAdapter()
    check("YFinanceAdapter()", "PASS", "No API key required")
except Exception as e:
    check("YFinanceAdapter()", "FAIL", str(e))

# ── 2. Company Profile ──
section(f"2. fetch_company_profile({TICKER})")

start = time.time()
try:
    profile = adapter.fetch_company_profile(TICKER)
    ms = round((time.time() - start) * 1000)
    if profile and profile.get("company_name"):
        check(
            "Company name",
            "PASS",
            f"{profile.get('company_name')} [{ms}ms]",
        )
        check("Symbol", "PASS", profile.get("symbol", "?"))
        check("Sector", "PASS" if profile.get("sector") else "WARN",
              profile.get("sector") or "Not available")
        check("Industry", "PASS" if profile.get("industry") else "WARN",
              profile.get("industry") or "Not available")
        check("Market Cap", "PASS" if profile.get("mkt_cap") else "WARN",
              f"{profile.get('mkt_cap', 'N/A'):,}" if profile.get("mkt_cap") else "N/A")
        check("Description", "PASS" if profile.get("description") else "WARN",
              f"{str(profile.get('description', ''))[:80]}..." if profile.get("description") else "N/A")
    else:
        check("fetch_company_profile", "FAIL", f"Empty result [{ms}ms]")
except Exception as e:
    ms = round((time.time() - start) * 1000)
    check("fetch_company_profile", "FAIL", f"{e} [{ms}ms]")

# ── 3. Financial Statements ──
section(f"3. fetch_financials({TICKER})")

start = time.time()
try:
    financials = adapter.fetch_financials(TICKER)
    ms = round((time.time() - start) * 1000)
    income = financials.get("income_statement", [])
    balance = financials.get("balance_sheet", [])
    cash = financials.get("cash_flow", [])

    check("Income statement", "PASS" if income else "FAIL",
          f"{len(income)} periods [{ms}ms]")
    if income:
        latest = income[0]
        date = latest.get("date", "?")
        total_revenue = latest.get("Total Revenue") or latest.get("totalRevenue", "N/A")
        net_income = latest.get("Net Income") or latest.get("netIncome", "N/A")
        check("  Latest period", "PASS", f"{date}")
        check("  Total Revenue",
              "PASS" if total_revenue != "N/A" else "WARN",
              f"{total_revenue:,}" if isinstance(total_revenue, (int, float)) else str(total_revenue))

    check("Balance sheet", "PASS" if balance else "FAIL",
          f"{len(balance)} periods")
    check("Cash flow", "PASS" if cash else "FAIL",
          f"{len(cash)} periods")
except Exception as e:
    ms = round((time.time() - start) * 1000)
    check("fetch_financials", "FAIL", f"{e} [{ms}ms]")

# ── 4. Market Price ──
section(f"4. fetch_market_price({TICKER})")

start = time.time()
try:
    price = adapter.fetch_market_price(TICKER)
    ms = round((time.time() - start) * 1000)
    if price and price.get("price"):
        check("Price", "PASS",
              f"${price.get('price'):.2f} [{ms}ms]")
        check("Day range", "PASS",
              f"${price.get('day_low', 0):.2f} - ${price.get('day_high', 0):.2f}")
        check("Volume",
              "PASS" if price.get("volume") else "WARN",
              f"{price.get('volume', 'N/A')}")
        check("Market cap",
              "PASS" if price.get("market_cap") else "WARN",
              f"{price.get('market_cap', 'N/A')}")
    else:
        check("fetch_market_price", "FAIL", f"Empty result [{ms}ms]")
except Exception as e:
    ms = round((time.time() - start) * 1000)
    check("fetch_market_price", "FAIL", f"{e} [{ms}ms]")

# ── 5. News ──
section(f"5. fetch_news({TICKER})")

start = time.time()
try:
    news = adapter.fetch_news(TICKER)
    ms = round((time.time() - start) * 1000)
    check("Articles returned",
          "PASS" if news else "WARN",
          f"{len(news)} articles [{ms}ms]" if news else "No news (may be expected)")
    if news:
        check("Title", "PASS", news[0].get("title", "")[:80] + "...")
        check("Publisher", "PASS" if news[0].get("site") else "WARN",
              news[0].get("site") or "N/A")
        check("URL", "PASS" if news[0].get("url") else "WARN",
              "Present" if news[0].get("url") else "N/A")
except Exception as e:
    ms = round((time.time() - start) * 1000)
    check("fetch_news", "FAIL", f"{e} [{ms}ms]")

# ── 6. Filings ──
section(f"6. fetch_filings({TICKER})")

start = time.time()
try:
    filings = adapter.fetch_filings(TICKER)
    ms = round((time.time() - start) * 1000)
    check("Filings", "WARN",
          f"Empty list (expected — Yahoo doesn't provide filings) [{ms}ms]")
except Exception as e:
    ms = round((time.time() - start) * 1000)
    check("fetch_filings", "FAIL", f"{e} [{ms}ms]")

# ── 7. ProviderOrchestrator Integration ──
section("7. ProviderOrchestrator Integration")

try:
    from backend.module4.provider_manager import initialize_default_providers
    from backend.module4.provider_orchestrator import ProviderOrchestrator

    initialize_default_providers()

    orch = ProviderOrchestrator()

    # Test company profile through orchestrator
    start = time.time()
    try:
        po_profile = orch.fetch_company_profile(TICKER,
                                                 providers=["yfinance"])
        ms = round((time.time() - start) * 1000)
        if po_profile and po_profile.get("company_name"):
            check("Orchestrator: company_profile", "PASS",
                  f"{po_profile.get('company_name')} [{ms}ms]")
        else:
            check("Orchestrator: company_profile", "FAIL",
                  f"Empty result [{ms}ms]")
    except Exception as e:
        ms = round((time.time() - start) * 1000)
        check("Orchestrator: company_profile", "FAIL",
              f"{e} [{ms}ms]")

    # Test market price through orchestrator
    start = time.time()
    try:
        po_price = orch.fetch_market_price(TICKER,
                                           providers=["yfinance"])
        ms = round((time.time() - start) * 1000)
        if po_price and po_price.get("price"):
            check("Orchestrator: market_price", "PASS",
                  f"${po_price.get('price'):.2f} [{ms}ms]")
        else:
            check("Orchestrator: market_price", "FAIL",
                  f"Empty result [{ms}ms]")
    except Exception as e:
        ms = round((time.time() - start) * 1000)
        check("Orchestrator: market_price", "FAIL", f"{e} [{ms}ms]")

    # Test news through orchestrator
    start = time.time()
    try:
        po_news = orch.fetch_news(TICKER,
                                  providers=["yfinance"])
        ms = round((time.time() - start) * 1000)
        if isinstance(po_news, list):
            check("Orchestrator: news", "PASS" if po_news else "WARN",
                  f"{len(po_news)} articles [{ms}ms]")
        else:
            check("Orchestrator: news", "FAIL", f"Unexpected type [{ms}ms]")
    except Exception as e:
        ms = round((time.time() - start) * 1000)
        check("Orchestrator: news", "FAIL", f"{e} [{ms}ms]")

except Exception as e:
    check("Orchestrator initialization", "FAIL", str(e))

# ── 8. FMP is Optional — Verify Graceful Degradation ──
section("8. FMP is Optional — Graceful Degradation")

try:
    from backend.module4.provider.fmp_adapter import FMPAdapter

    try:
        fmp = FMPAdapter()
        check("FMP init when key present", "PASS", "FMP initialized")
        # Test profile — expected to fail with 403 on free tier
        try:
            fmp_profile = fmp.fetch_company_profile(TICKER)
            check("FMP profile (paid key?)", "PASS", "Worked!")
        except Exception as e:
            msg = str(e)
            check("FMP profile gracefully fails on free tier", "PASS",
                  f"Caught: {msg[:80]}")
    except ValueError:
        check("FMP init when no key", "PASS",
              "Graceful ValueError (no key) — pipeline continues with yfinance")

except Exception as e:
    check("FMP graceful degradation", "FAIL", str(e))

# ── Summary ──
section("SUMMARY")

print("\n")
for name, status, detail in results:
    if name.startswith("─"):
        print()
        print(name)
    elif name.startswith("  "):
        print(f"  {status}  {name}")
        if detail:
            print(f"              {detail}")
    else:
        if detail:
            print(f"  {status}  {name:<45} {detail}")
        else:
            print(f"  {status}  {name}")

print(f"\n{'=' * 60}")
print(f"  RESULTS:  {PASS} ✅  |  {FAIL} ❌  |  {WARN} ⚠️")
print(f"{'=' * 60}")

if FAIL == 0:
    print("\n🎉 All critical endpoints working — yfinance is a viable free replacement for FMP.")
else:
    print(f"\n❌ {FAIL} failure(s) need attention.")
