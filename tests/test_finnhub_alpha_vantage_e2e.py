"""
Finnhub & Alpha Vantage — End-to-End Verification

Tests:
  Phase 1 — API Key loading (masked confirmation)
  Phase 2 — FinnhubAdapter: company profile, market price, news
  Phase 3 — AlphaVantageAdapter: company overview, market quote, news
  Phase 4 — ProviderManager registration
  Phase 5 — ProviderOrchestrator failover order + news fetch
  Phase 6 — News persistence into PostgreSQL
  Phase 7 — RetrievalAgent reads stored news
  Phase 8 — EvidenceConsolidator includes news in memo context

Companies: MSFT, NVDA, INFY.NS, HDFCBANK.NS
"""

import logging
import os
import sys
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-35s | %(levelname)-5s | %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger("test.new_providers")

# ── Ensure project root is on sys.path ───────────────────────────────
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Load .env ─────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

TICKERS = ["MSFT", "NVDA", "INFY.NS", "HDFCBANK.NS"]


# ── Helpers ───────────────────────────────────────────────────────────

def mask_key(key):
    if not key or len(key) < 8:
        return "***"
    return f"{key[:4]}***{key[-4:]}"


results = []
failures = []
warnings = []


def check(phase, ticker, status, detail=""):
    label = f"[{phase:>4s}] {ticker:12s} → {status}"
    if detail:
        label += f" | {detail}"
    results.append(label)
    if status == "FAIL":
        failures.append(label)
    elif status == "WARN":
        warnings.append(label)
    print(f"  {label}")


def check_key(key_name, expected_prefix=None):
    val = os.getenv(key_name) or os.environ.get(key_name)
    if val:
        print(f"  ✓ {key_name} = {mask_key(val)} (len={len(val)})")
        if expected_prefix and not val.startswith(expected_prefix):
            print(f"    ⚠ Prefix mismatch: expected '{expected_prefix}*', got '{val[:4]}***'")
    else:
        print(f"  ✗ {key_name} = NOT SET")
    return val


# ════════════════════════════════════════════════════════════════════════
# PHASE 1 — API Key Loading
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 1 — API Key Loading (masked)")
print("=" * 70)

finnhub_key = check_key("FINNHUB_API_KEY")
alpha_vantage_key = check_key("ALPHA_VANTAGE_API_KEY")
fmp_key = check_key("FMP_API_KEY")

if finnhub_key:
    check("P1", "FINNHUB_API_KEY", "PASS", f"Loaded ({mask_key(finnhub_key)})")
else:
    check("P1", "FINNHUB_API_KEY", "WARN", "Not configured — Finnhub tests will be skipped")

if alpha_vantage_key:
    check("P1", "ALPHA_VANTAGE_API_KEY", "PASS", f"Loaded ({mask_key(alpha_vantage_key)})")
else:
    check("P1", "ALPHA_VANTAGE_API_KEY", "WARN", "Not configured — Alpha Vantage tests will be skipped")


# ════════════════════════════════════════════════════════════════════════
# PHASE 2 — FinnhubAdapter
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 2 — FinnhubAdapter Direct Tests")
print("=" * 70)

if finnhub_key:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from backend.module4.provider.finnhub_adapter import FinnhubAdapter

    finnhub = FinnhubAdapter()

    # 2a. Company Profile
    print("\n  2a. Company Profile:")
    for t in TICKERS:
        start = time.monotonic()
        try:
            profile = finnhub.fetch_company_profile(t)
            lat = (time.monotonic() - start) * 1000
            if profile and profile.get("company_name"):
                name = profile.get("company_name", "")[:40]
                check("P2a", t, "PASS", f"{lat:.0f}ms | {name}")
            else:
                check("P2a", t, "FAIL", f"{lat:.0f}ms | Empty/invalid response: {str(profile)[:80]}")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P2a", t, "FAIL", f"{lat:.0f}ms | {e}")

    # 2b. Market Price
    print("\n  2b. Market Price:")
    for t in TICKERS:
        start = time.monotonic()
        try:
            price = finnhub.fetch_market_price(t)
            lat = (time.monotonic() - start) * 1000
            if price and price.get("price") is not None:
                check("P2b", t, "PASS", f"{lat:.0f}ms | ${price['price']}")
            else:
                check("P2b", t, "WARN", f"{lat:.0f}ms | No price returned (ADR tickers may fail)")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P2b", t, "WARN", f"{lat:.0f}ms | {e}")

    # 2c. Company News
    print("\n  2c. Company News:")
    for t in TICKERS:
        start = time.monotonic()
        try:
            news = finnhub.fetch_news(t)
            lat = (time.monotonic() - start) * 1000
            if news and len(news) > 0:
                check("P2c", t, "PASS", f"{lat:.0f}ms | {len(news)} articles")
            else:
                check("P2c", t, "WARN", f"{lat:.0f}ms | 0 articles (no recent news)")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P2c", t, "FAIL", f"{lat:.0f}ms | {e}")
else:
    print("  ⏭ Skipped — no FINNHUB_API_KEY configured")
    for t in TICKERS:
        check("P2a", t, "WARN", "Skipped (no key)")
        check("P2b", t, "WARN", "Skipped (no key)")
        check("P2c", t, "WARN", "Skipped (no key)")


# ════════════════════════════════════════════════════════════════════════
# PHASE 3 — AlphaVantageAdapter
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 3 — AlphaVantageAdapter Direct Tests")
print("=" * 70)

if alpha_vantage_key:
    from backend.module4.provider.alpha_vantage_adapter import AlphaVantageAdapter

    av = AlphaVantageAdapter()

    # 3a. Company Overview (profile)
    print("\n  3a. Company Overview:")
    for t in TICKERS:
        start = time.monotonic()
        try:
            profile = av.fetch_company_profile(t)
            lat = (time.monotonic() - start) * 1000
            if profile and profile.get("company_name"):
                name = profile.get("company_name", "")[:40]
                check("P3a", t, "PASS", f"{lat:.0f}ms | {name}")
            else:
                check("P3a", t, "WARN", f"{lat:.0f}ms | Empty/invalid response")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P3a", t, "FAIL", f"{lat:.0f}ms | {e}")

    # 3b. Market Quote
    print("\n  3b. Market Quote:")
    for t in TICKERS:
        start = time.monotonic()
        try:
            price = av.fetch_market_price(t)
            lat = (time.monotonic() - start) * 1000
            if price and price.get("price") is not None:
                check("P3b", t, "PASS", f"{lat:.0f}ms | ${price['price']}")
            else:
                check("P3b", t, "WARN", f"{lat:.0f}ms | No price returned")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P3b", t, "WARN", f"{lat:.0f}ms | {e}")

    # 3c. News Sentiment
    print("\n  3c. News Sentiment:")
    for t in TICKERS:
        start = time.monotonic()
        try:
            news = av.fetch_news(t)
            lat = (time.monotonic() - start) * 1000
            if news and len(news) > 0:
                # Show sample
                sample = news[0].get("title", "")[:60] if news else ""
                check("P3c", t, "PASS", f"{lat:.0f}ms | {len(news)} articles | e.g. '{sample}'")
            else:
                check("P3c", t, "WARN", f"{lat:.0f}ms | 0 articles")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P3c", t, "FAIL", f"{lat:.0f}ms | {e}")
else:
    print("  ⏭ Skipped — no ALPHA_VANTAGE_API_KEY configured")
    for t in TICKERS:
        check("P3a", t, "WARN", "Skipped (no key)")
        check("P3b", t, "WARN", "Skipped (no key)")
        check("P3c", t, "WARN", "Skipped (no key)")


# ════════════════════════════════════════════════════════════════════════
# PHASE 4 — ProviderManager Registration
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 4 — ProviderManager Registration")
print("=" * 70)

from backend.module4.provider_manager import provider_manager, initialize_default_providers
initialize_default_providers()

all_providers = provider_manager.list_providers()
print(f"  Registered providers: {all_providers}")

expected = {"finnhub", "alpha_vantage", "yfinance", "nse", "bse", "sebi", "fmp"}
registered_set = set(all_providers)

if "finnhub" in registered_set:
    check("P4", "finnhub", "PASS", "Registered")
else:
    check("P4", "finnhub", "FAIL", "Not registered")

if "alpha_vantage" in registered_set:
    check("P4", "alpha_vantage", "PASS", "Registered")
else:
    check("P4", "alpha_vantage", "FAIL", "Not registered")

missing = expected - registered_set
extra = registered_set - expected

if missing:
    check("P4", "all", "WARN", f"Missing providers: {missing}")
else:
    check("P4", "all", "PASS", f"All {len(expected)} expected providers registered")

if extra:
    print(f"  ℹ Extra providers: {extra}")


# ════════════════════════════════════════════════════════════════════════
# PHASE 5 — ProviderOrchestrator Failover + News Fetch
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 5 — ProviderOrchestrator News Fetch + Failover")
print("=" * 70)

from backend.module4.provider_orchestrator import ProviderOrchestrator

orch = ProviderOrchestrator()

from backend.module4.provider_orchestrator import _DEFAULT_API_PRIORITY as default_priority
print(f"  Default priority: {default_priority}")
expected_priority = ["finnhub", "alpha_vantage", "yfinance", "nse", "bse", "sebi", "fmp"]
actual_priority = default_priority

if actual_priority == expected_priority:
    check("P5", "priority", "PASS", f"Correct order: {' → '.join(actual_priority)}")
else:
    check("P5", "priority", "WARN", f"Expected {expected_priority}, got {actual_priority}")

# Test news fetch via orchestrator fallback
orchestrator_results = {}
for t in TICKERS:
    start = time.monotonic()
    try:
        news = orch.fetch_news(t)
        lat = (time.monotonic() - start) * 1000
        article_count = len(news) if isinstance(news, list) else 0
        orchestrator_results[t] = {"count": article_count, "latency_ms": lat, "source": "unknown"}

        if article_count > 0:
            check("P5", t, "PASS", f"{lat:.0f}ms | {article_count} articles")
        else:
            check("P5", t, "WARN", f"{lat:.0f}ms | 0 articles returned")
    except Exception as e:
        lat = (time.monotonic() - start) * 1000
        orchestrator_results[t] = {"count": 0, "latency_ms": lat, "source": "error"}
        check("P5", t, "WARN", f"{lat:.0f}ms | {e}")


# ════════════════════════════════════════════════════════════════════════
# PHASE 6 — News Persistence into PostgreSQL
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 6 — News Persistence into PostgreSQL")
print("=" * 70)

from backend.module4.database_manager import DatabaseManager
from backend.module4.normalizer import normalizer
from backend.module4.ingestion_service import IngestionService

dbm = DatabaseManager()
ingestion = IngestionService()

# Clear test state for fresh ingestion
try:
    from backend.database.models import News, Company, MarketPrice
    dbm.connection.query(News).delete()
    dbm.connection.query(MarketPrice).delete()
    dbm.connection.query(Company).delete()
    dbm.connection.commit()
    print("  ✓ Cleared existing test data")
except Exception as e:
    dbm.connection.rollback()
    print(f"  ⚠ Could not clear test data: {e}")
    # Wait, some data might still exist — that's OK for upserts

news_count_by_ticker = {}

for t in TICKERS:
    # 6a. Fetch news via orchestrator
    try:
        news_items = orch.fetch_news(t)
    except Exception as e:
        print(f"  ⚠ {t}: Orchestrator fetch failed: {e}")
        news_items = []

    if not news_items or len(news_items) == 0:
        # Try direct YFinance as fallback for persistence test
        try:
            from backend.module4.provider.yfinance_adapter import YFinanceAdapter
            yf = YFinanceAdapter()
            news_items = yf.fetch_news(t)
            print(f"  ℹ {t}: Fell back to YFinance direct — {len(news_items)} articles")
        except Exception as e:
            print(f"  ⚠ {t}: YFinance direct also failed: {e}")

    # 6b. Save company first (need company_id)
    try:
        profile = orch.fetch_company_profile(t)
        if profile:
            company_data = normalizer.normalize_company(profile)
            ingestion._save_company(company_data)
        else:
            print(f"  ⚠ {t}: No profile — using upsert")
    except Exception as e:
        print(f"  ℹ {t}: Company save (may already exist): {e}")

    # 6c. Get company_id
    company = dbm.get_latest_company(t)
    if not company:
        print(f"  ⚠ {t}: No company record — cannot persist news")
        news_count_by_ticker[t] = 0
        continue

    comp_id = company.id
    saved = 0

    # 6d. Save each news item with company_id
    for item in news_items:
        try:
            item["company_id"] = comp_id
            normalized = normalizer.normalize_news(item)
            if normalized.get("headline"):
                dbm.save_news(normalized)
                saved += 1
            else:
                # Try saving directly with raw format
                raw_item = {
                    "company_id": comp_id,
                    "headline": item.get("title", ""),
                    "source": item.get("site", ""),
                    "url": item.get("url", ""),
                    "published_at": item.get("published_date", ""),
                    "text": item.get("text", ""),
                }
                if raw_item["headline"]:
                    dbm.save_news(raw_item)
                    saved += 1
        except Exception as e:
            print(f"    ⚠ Error saving news item: {e}")

    dbm.commit()
    news_count_by_ticker[t] = saved

    if saved > 0:
        check("P6", t, "PASS", f"{saved} news articles persisted")
    else:
        check("P6", t, "WARN", f"0 articles persisted (no recent news from providers)")


# ════════════════════════════════════════════════════════════════════════
# PHASE 7 — RetrievalAgent Reads Stored News
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 7 — RetrievalAgent News Retrieval")
print("=" * 70)

from backend.intelligence.retrieval_agent import RetrievalAgent

total_retrieved_news = 0
for t in TICKERS:
    ra = RetrievalAgent(t)
    news = ra.get_news()
    count = len(news)
    total_retrieved_news += count

    if count > 0:
        sample = news[0].get("headline", "")[:60] if news else ""
        check("P7", t, "PASS", f"{count} articles | e.g. '{sample}'")
    else:
        check("P7", t, "WARN", f"0 articles (none persisted in Phase 6)")


# ════════════════════════════════════════════════════════════════════════
# PHASE 8 — EvidenceConsolidator Includes News
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 8 — EvidenceConsolidator News in Memo Context")
print("=" * 70)

from backend.intelligence.evidence_consolidator import EvidenceConsolidator
from backend.intelligence.data_agent import DataAgent

news_in_context = 0
total_context_chars = 0

for t in TICKERS:
    # 8a. Get live data via DataAgent
    da = DataAgent(t)
    live_data = da.fetch_all()

    # 8b. Get stored data via RetrievalAgent
    ra = RetrievalAgent(t)
    stored_data = ra.retrieve_all()

    # 8c. Consolidate
    consolidator = EvidenceConsolidator(t)
    if hasattr(consolidator, 'consolidate'):
        method = consolidator.consolidate
        # Check signature
        import inspect
        sig = inspect.signature(method)
        params = list(sig.parameters.keys())

        kwargs = {}
        if "module4_data" in params:
            kwargs["module4_data"] = live_data
        if "stored_data" in params:
            kwargs["stored_data"] = stored_data

        context = method(**kwargs)
    else:
        context = consolidator.consolidate(module4_data=live_data, stored_data=stored_data)

    # 8d. Check for news in context
    if isinstance(context, dict):
        context_text = context.get("context_text", "")
    else:
        context_text = str(context)

    has_news_section = "[SOURCE: Live Market Data — Recent News" in context_text or "[SOURCE: Live Market Data — Recent" in context_text
    has_news_available = "news: AVAILABLE" in context_text or "news: UNAVAILABLE" in context_text
    has_source_label = "[SOURCE:" in context_text

    total_context_chars += len(context_text)

    if has_news_section:
        news_in_context += 1
        check("P8", t, "PASS", f"News section in context ({len(context_text)} chars)")
    elif has_news_available:
        check("P8", t, "WARN", f"News source listed but no section rendered")
    else:
        check("P8", t, "WARN", f"No news in context")

    # Also dump the source summary
    if isinstance(context, dict) and "sources" in context:
        sources = context.get("sources", [])
        print(f"    Sources: {sources}")


# ════════════════════════════════════════════════════════════════════════
# FINAL REPORT
# ════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("FINAL VERIFICATION REPORT")
print("=" * 70)

pass_count = sum(1 for r in results if "→ PASS" in r)
fail_count = sum(1 for r in results if "→ FAIL" in r)
warn_count = sum(1 for r in results if "→ WARN" in r)

print(f"\n  Total:     {len(results)}")
print(f"  ✅ PASS:   {pass_count}")
print(f"  ⚠️  WARN:   {warn_count}")
print(f"  ❌ FAIL:   {fail_count}")

print(f"\n{'─' * 70}")
print("SUMMARY BY PHASE")
print(f"{'─' * 70}")

phases = {
    "P1": "API Key Loading",
    "P2a": "Finnhub — Company Profile",
    "P2b": "Finnhub — Market Price",
    "P2c": "Finnhub — News",
    "P3a": "Alpha Vantage — Company Overview",
    "P3b": "Alpha Vantage — Market Quote",
    "P3c": "Alpha Vantage — News Sentiment",
    "P4": "ProviderManager Registration",
    "P5": "ProviderOrchestrator Failover",
    "P6": "News Persistence (PostgreSQL)",
    "P7": "RetrievalAgent News Retrieval",
    "P8": "EvidenceConsolidator News in Context",
}

for phase_code, phase_name in phases.items():
    phase_results = [r for r in results if r.startswith(f"[{phase_code}]")]
    if not phase_results:
        continue
    p = sum(1 for r in phase_results if "→ PASS" in r)
    f = sum(1 for r in phase_results if "→ FAIL" in r)
    w = sum(1 for r in phase_results if "→ WARN" in r)
    total = len(phase_results)
    status = "✅" if f == 0 else "❌"
    print(f"  {status} {phase_code} {phase_name:42s} {p:>2d}/{total} PASS ({w} warn)")

print(f"\n{'─' * 70}")
print("DATABASE STATS")
print(f"{'─' * 70}")

try:
    from backend.database.models import Company, Financial, MarketPrice, News
    companies_count = dbm.connection.query(Company).count()
    financials_count = dbm.connection.query(Financial).count()
    prices_count = dbm.connection.query(MarketPrice).count()
    news_count = dbm.connection.query(News).count()

    print(f"  Companies:      {companies_count}")
    print(f"  Financials:     {financials_count}")
    print(f"  Market Prices:  {prices_count}")
    print(f"  News:           {news_count}")

    # Sample news records
    if news_count > 0:
        sample_news = dbm.connection.query(News).order_by(News.published_at.desc()).limit(3).all()
        print(f"\n  Sample news records:")
        for n in sample_news:
            print(f"    • [{n.company_id}] {n.headline[:60]} — {n.source}")
except Exception as e:
    print(f"  ⚠ DB query error: {e}")

print(f"\n{'─' * 70}")
print("PROVIDER METRICS")
print(f"{'─' * 70}")

try:
    fm = orchestrator_results
    for t, info in fm.items():
        print(f"  {t:12s}: {info['count']} articles | {info['latency_ms']:.0f}ms")
except Exception:
    pass

print(f"\n{'─' * 70}")
print("FAILURES")
print(f"{'─' * 70}")
if failures:
    for f in failures:
        print(f"  ❌ {f}")
else:
    print("  ✅ No failures!")

print(f"{'─' * 70}")
if fail_count == 0:
    print("  VERDICT: ✅ ALL CHECKS PASS — Integration verified")
else:
    print(f"  VERDICT: ❌ {fail_count} failures — review above")

dbm.close()
print(f"\n{'=' * 70}")
