"""
E2E Integration Test — Finnhub & Alpha Vantage Providers

Tests the new providers through the complete Module 4 pipeline:

    News Provider (Finnhub → Alpha Vantage → YFinance → ...)
        ↓
    Normalizer → Database → Cache → RetrievalAgent → EvidenceConsolidator

Reports per-provider success/failure, latency, fallback behavior,
articles retrieved, articles stored.

Tickers: AAPL, MSFT, NVDA, HDFCBANK.NS, INFY.NS
"""

import sys, os, time, json
from collections import defaultdict

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

from sqlalchemy import text

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

TICKERS = ["AAPL", "MSFT", "NVDA", "HDFCBANK.NS", "INFY.NS"]
NEWS_LOG = []  # (ticker, provider, status, latency_ms, count, error)

print("=" * 60)
print("  FINNHUB & ALPHA VANTAGE — PROVIDER INTEGRATION TEST")
print("  Tickers: " + ", ".join(TICKERS))
print("=" * 60)

# ═══════════════════════════════════════════════════════════════════
# 1. Import & Init
# ═══════════════════════════════════════════════════════════════════

section("INITIALIZATION")

try:
    from backend.module4.provider_manager import initialize_default_providers, provider_manager
    from backend.module4.provider_orchestrator import ProviderOrchestrator
    from backend.module4.provider.finnhub_adapter import FinnhubAdapter
    from backend.module4.provider.alpha_vantage_adapter import AlphaVantageAdapter
    from backend.module4.normalizer import normalizer
    from backend.module4.database_manager import DatabaseManager
    from backend.intelligence.retrieval_agent import RetrievalAgent
    from backend.intelligence.evidence_consolidator import EvidenceConsolidator
    check("All imports", "PASS")
except Exception as e:
    check("All imports", "FAIL", str(e))

initialize_default_providers()
orchestrator = ProviderOrchestrator()

# ═══════════════════════════════════════════════════════════════════
# 2. Verify Both Adapters Are Registered
# ═══════════════════════════════════════════════════════════════════

section("PROVIDER REGISTRATION")

available = provider_manager.list_providers()
check("Registered providers", "PASS", f"{len(available)} providers: {', '.join(sorted(available))}")
check("Finnhub registered", "PASS" if "finnhub" in available else "FAIL")
check("Alpha Vantage registered", "PASS" if "alpha_vantage" in available else "FAIL")

# ═══════════════════════════════════════════════════════════════════
# 3. Direct Adapter Tests
# ═══════════════════════════════════════════════════════════════════

section("DIRECT ADAPTER TESTS")

# Test Finnhub fetch_news directly
try:
    finnhub = provider_manager.get_provider("finnhub")
    has_key = finnhub.api_key is not None and len(finnhub.api_key) > 0
    check("Finnhub has API key", "PASS" if has_key else "WARN", "No FINNHUB_API_KEY configured")

    if has_key:
        start = time.monotonic()
        news = finnhub.fetch_news("AAPL")
        latency = (time.monotonic() - start) * 1000
        count = len(news)
        check("Finnhub AAPL news", "PASS" if count > 0 else "WARN",
              f"{count} articles in {latency:.0f}ms")
        if count > 0:
            check("Finnhub news format", "PASS",
                  f"keys={list(news[0].keys())} title='{news[0].get('title','')[:40]}'")
        NEWS_LOG.append(("AAPL", "finnhub", "PASS" if count > 0 else "WARN", latency, count, None))
except Exception as e:
    NEWS_LOG.append(("AAPL", "finnhub", "FAIL", 0, 0, str(e)))
    check("Finnhub AAPL news", "FAIL", str(e))

# Test Alpha Vantage fetch_news directly
try:
    alpha = provider_manager.get_provider("alpha_vantage")
    has_key_av = alpha.api_key is not None and len(alpha.api_key) > 0
    check("Alpha Vantage has API key", "PASS" if has_key_av else "WARN",
          "No ALPHA_VANTAGE_API_KEY configured")

    if has_key_av:
        start = time.monotonic()
        news_av = alpha.fetch_news("MSFT")
        latency_av = (time.monotonic() - start) * 1000
        count_av = len(news_av)
        check("Alpha Vantage MSFT news", "PASS" if count_av > 0 else "WARN",
              f"{count_av} articles in {latency_av:.0f}ms")
        if count_av > 0:
            check("Alpha Vantage news format", "PASS",
                  f"keys={list(news_av[0].keys())} title='{news_av[0].get('title','')[:40]}'")
        NEWS_LOG.append(("MSFT", "alpha_vantage", "PASS" if count_av > 0 else "WARN", latency_av, count_av, None))
except Exception as e:
    NEWS_LOG.append(("MSFT", "alpha_vantage", "FAIL", 0, 0, str(e)))
    check("Alpha Vantage MSFT news", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════
# 4. ProviderOrchestrator News Fallback Test
# ═══════════════════════════════════════════════════════════════════

section("ORCHESTRATOR NEWS FALLBACK")

# Fetch news through orchestrator (should try finnhub first, then alpha, then yfinance)
for ticker in TICKERS:
    start = time.monotonic()
    try:
        news = orchestrator.fetch_news(ticker)
        latency = (time.monotonic() - start) * 1000
        count = len(news) if isinstance(news, list) else 0

        if count > 0:
            check(f"{ticker} orchestrator news", "PASS", f"{count} articles in {latency:.0f}ms")
        else:
            check(f"{ticker} orchestrator news", "WARN", f"0 articles in {latency:.0f}ms")

        NEWS_LOG.append((ticker, "orchestrator", "PASS" if count > 0 else "WARN", latency, count, None))

        # Verify normalized format
        if count > 0:
            normalized = normalizer.normalize_news(news[0])
            has_headline = bool(normalized.get("headline"))
            has_source = bool(normalized.get("source"))
            has_url = bool(normalized.get("url"))
            check(f"  {ticker} normalized news", "PASS" if has_headline else "WARN",
                  f"headline={'YES' if has_headline else 'NO'} "
                  f"source={'YES' if has_source else 'NO'} "
                  f"url={'YES' if has_url else 'NO'}")
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        NEWS_LOG.append((ticker, "orchestrator", "FAIL", latency, 0, str(e)))
        check(f"{ticker} orchestrator news", "FAIL", f"{latency:.0f}ms | {e}")

# ═══════════════════════════════════════════════════════════════════
# 5. Database Persistence Test
# ═══════════════════════════════════════════════════════════════════

section("NEWS DATABASE PERSISTENCE")

# Inject news items into DB with company_id
db = DatabaseManager()
try:
    # Clean existing news
    db.connection.execute(text("DELETE FROM news"))
    db.commit()

    # Get or create companies
    from backend.database.models import Company
    company_map = {}
    for ticker in TICKERS:
        existing = db.connection.query(Company).filter(Company.ticker == ticker).first()
        if existing:
            company_map[ticker] = existing
        else:
            company = Company(ticker=ticker, company_name=f"{ticker} Test")
            db.connection.add(company)
            db.connection.flush()
            company_map[ticker] = company

    # Fetch and store news for each ticker
    total_stored = 0
    for ticker in TICKERS:
        try:
            news_items = orchestrator.fetch_news(ticker)
            if isinstance(news_items, list):
                cid = company_map[ticker].id
                stored = 0
                for item in news_items:
                    normalized = normalizer.normalize_news(item)
                    normalized["company_id"] = cid
                    db.save_news(normalized)
                    stored += 1
                total_stored += stored
                check(f"  {ticker} news stored", "PASS" if stored > 0 else "WARN",
                      f"{stored} articles")
        except Exception as e:
            check(f"  {ticker} news store", "FAIL", str(e))

    db.commit()

    # Verify row counts
    news_count = db.connection.execute(text("SELECT COUNT(*) FROM news")).scalar()
    check("Total news in DB", "PASS" if news_count > 0 else "WARN",
          f"{news_count} rows")

    # Per-company breakdown
    for ticker, company in company_map.items():
        cnt = db.connection.execute(
            text("SELECT COUNT(*) FROM news WHERE company_id = :cid"),
            {"cid": company.id}
        ).scalar()
        check(f"  {ticker} DB count", "PASS" if cnt > 0 else "WARN", f"{cnt} rows")

    # Sample a news record
    sample = db.connection.execute(
        text("SELECT id, company_id, headline, source, url FROM news LIMIT 1")
    ).fetchone()
    if sample:
        check("Sample news record", "PASS",
              f"id={sample[0]} cid={sample[1]} headline='{str(sample[2])[:50]}' source={sample[3]}")

except Exception as e:
    check("News persistence", "FAIL", str(e))
    import traceback
    traceback.print_exc()
finally:
    db.close()

# ═══════════════════════════════════════════════════════════════════
# 6. RetrievalAgent Test
# ═══════════════════════════════════════════════════════════════════

section("RETRIEVAL AGENT — NEWS")

try:
    for ticker in TICKERS:
        agent = RetrievalAgent(ticker)
        result = agent.retrieve_all()
        news = result.get("news", [])
        check(f"{ticker} RetrievalAgent news", "PASS" if len(news) > 0 else "WARN",
              f"{len(news)} articles")
except Exception as e:
    check("RetrievalAgent", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════
# 7. Evidence Consolidator Test
# ═══════════════════════════════════════════════════════════════════

section("EVIDENCE CONSOLIDATOR — NEWS")

try:
    for ticker in TICKERS[:2]:
        da = __import__("backend.intelligence.data_agent", fromlist=["DataAgent"]).DataAgent(ticker)
        live_data = da.fetch_all()
        agent = RetrievalAgent(ticker)
        stored = agent.retrieve_all()
        consolidator = EvidenceConsolidator(ticker)
        consolidated = consolidator.consolidate(
            master_summary=None, module3_result=None,
            module4_data=live_data, stored_data=stored,
        )
        ctx = consolidated.get("context_text", "")
        has_news_section = "news" in ctx.lower()
        check(f"{ticker} EvidenceConsolidator news", "PASS" if has_news_section else "WARN",
              f"Context={len(ctx)} chars News={'YES' if has_news_section else 'NO'}")
except Exception as e:
    check("EvidenceConsolidator", "FAIL", str(e))
    import traceback
    traceback.print_exc()

# ═══════════════════════════════════════════════════════════════════
# 8. Provider Health Report
# ═══════════════════════════════════════════════════════════════════

section("PROVIDER HEALTH REPORT")

try:
    diag = orchestrator.get_diagnostics()
    health = diag.get("provider_health", {}).get("providers", diag.get("provider_health", {}))
    cache = diag.get("cache_stats", {})

    check("Cache stats", "PASS",
          f"Hits={cache.get('hits', '?')} Misses={cache.get('misses', '?')} "
          f"Rate={cache.get('hit_rate_pct', '?')}%")

    # Show health for finnhub and alpha specifically
    for pname in ["finnhub", "alpha_vantage", "yfinance"]:
        pdata = {}
        if isinstance(health, dict):
            pdata = health.get(pname, {})
        if pdata:
            status = pdata.get("status", "unknown")
            latency = pdata.get("avg_latency_ms", 0)
            check(f"  Provider: {pname}", "PASS",
                  f"Status={status} AvgLat={latency:.0f}ms "
                  f"Success={pdata.get('success_count', 0)} "
                  f"Failure={pdata.get('failure_count', 0)}")
        else:
            check(f"  Provider: {pname}", "WARN", "No health data recorded yet")

except Exception as e:
    check("Provider health report", "WARN", str(e))

# ═══════════════════════════════════════════════════════════════════
# 9. News Provider Comparison Report
# ═══════════════════════════════════════════════════════════════════

section("NEWS PROVIDER COMPARISON REPORT")

print("\n  Provider Comparison by Ticker:\n")
print(f"  {'Ticker':<16} {'Provider':<16} {'Status':<8} {'Latency':<10} {'Articles':<10}")
print(f"  {'-'*16} {'-'*16} {'-'*8} {'-'*10} {'-'*10}")
for entry in NEWS_LOG:
    ticker, prov, status, lat, cnt, err = entry
    status_str = "✅" if "PASS" in status else ("⚠️" if "WARN" in status else "❌")
    lat_str = f"{lat:.0f}ms" if lat else "N/A"
    cnt_str = str(cnt) if cnt is not None else "?"
    print(f"  {ticker:<16} {prov:<16} {status_str:<8} {lat_str:<10} {cnt_str:<10}")

# ═══════════════════════════════════════════════════════════════════
# 10. RESULTS SUMMARY
# ═══════════════════════════════════════════════════════════════════

section("FINAL RESULTS")

print(f"\n{'=' * 60}")
print(f"  RESULTS: {PASS} ✅ PASS, {FAIL} ❌ FAIL, {WARN} ⚠️  WARN")
print(f"{'=' * 60}\n")

for name, status, detail in results:
    if status:
        print(f"  {status}  {name}")
        if detail:
            print(f"          {detail}")
    else:
        print(f"  {name}")

print(f"\n{'=' * 60}")
if FAIL == 0 and PASS >= 20:
    print("  ✅ PROVIDER INTEGRATION — PASSED")
elif FAIL <= 2:
    print(f"  ⚠️  PROVIDER INTEGRATION — MOSTLY PASSED ({FAIL} failures)")
else:
    print(f"  ❌ PROVIDER INTEGRATION — FAILED ({FAIL} failures)")
print(f"{'=' * 60}")
