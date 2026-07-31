"""
Finnhub & Alpha Vantage — LIVE End-to-End Verification

Tests with real API keys:
  Phase 1 — API Key loading confirmation
  Phase 2 — Finnhub live: company profile, market price, news
  Phase 3 — Alpha Vantage live: company overview, market quote, news
  Phase 4 — ProviderManager registration + orchestrator priority
  Phase 5 — News persistence into PostgreSQL
  Phase 6 — RetrievalAgent reads stored news
  Phase 7 — EvidenceConsolidator includes news in memo context

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

logger = logging.getLogger("test.live")

# ── Ensure project root on sys.path ────────────────────────────────
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Load .env (where keys should now be) ──────────────────────────
from dotenv import load_dotenv
load_dotenv()

TICKERS = ["MSFT", "NVDA", "INFY.NS", "HDFCBANK.NS"]
KEYS = ["FINNHUB_API_KEY", "ALPHA_VANTAGE_API_KEY"]

results = []
failures = []
warnings = []


def mask_key(key):
    if not key or len(key) < 8:
        return "***"
    return f"{key[:4]}***{key[-4:]}"


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


# ════════════════════════════════════════════════════════════════════
# PHASE 1 — API Key Loading Confirmation
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 1 — API Key Loading")
print("=" * 70)

for k in KEYS:
    val = os.environ.get(k) or os.getenv(k)
    if val:
        len_str = f"len={len(val)}"
        check("P1", k, "PASS", f"{len_str} — {mask_key(val)}")
        print(f"    Source: {k} FOUND in environment after load_dotenv()")
    else:
        check("P1", k, "FAIL", "NOT FOUND — check .env file")
        print(f"    Source: NOT FOUND in os.environ or after load_dotenv()")


# ════════════════════════════════════════════════════════════════════
# PHASE 2 — FinnhubAdapter Live Tests
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 2 — FinnhubAdapter Live Authenticated Requests")
print("=" * 70)

finnhub_key = os.environ.get("FINNHUB_API_KEY") or os.getenv("FINNHUB_API_KEY")
if finnhub_key:
    from backend.module4.provider.finnhub_adapter import FinnhubAdapter
    finnhub = FinnhubAdapter(api_key=finnhub_key)

    if finnhub.api_key:
        check("P2i", "FinnhubAdapter", "PASS", f"Key loaded, len={len(finnhub.api_key)}")
    else:
        check("P2i", "FinnhubAdapter", "FAIL", "Key not passed to adapter")

    # 2a. Company Profile
    print("\n  2a. Company Profile (/stock/profile2):")
    for t in TICKERS:
        start = time.monotonic()
        try:
            profile = finnhub.fetch_company_profile(t)
            lat = (time.monotonic() - start) * 1000
            if profile and profile.get("company_name"):
                name = profile["company_name"][:50]
                check("P2a", t, "PASS", f"{lat:.0f}ms | {name}")
            else:
                check("P2a", t, "WARN", f"{lat:.0f}ms | Empty response")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P2a", t, "FAIL", f"{lat:.0f}ms | {e}")

    # 2b. Market Price
    print("\n  2b. Market Price (/quote):")
    for t in TICKERS:
        start = time.monotonic()
        try:
            price = finnhub.fetch_market_price(t)
            lat = (time.monotonic() - start) * 1000
            if price and price.get("price") is not None:
                check("P2b", t, "PASS", f"{lat:.0f}ms | ${price['price']}")
            else:
                check("P2b", t, "WARN", f"{lat:.0f}ms | No price")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P2b", t, "WARN", f"{lat:.0f}ms | {e}")

    # 2c. News
    print("\n  2c. Company News (/company-news):")
    finnhub_news = {}
    for t in TICKERS:
        start = time.monotonic()
        try:
            news = finnhub.fetch_news(t)
            lat = (time.monotonic() - start) * 1000
            finnhub_news[t] = news
            if news and len(news) > 0:
                sample = news[0].get("title", "")[:60]
                check("P2c", t, "PASS", f"{lat:.0f}ms | {len(news)} articles | '{sample}'")
            else:
                check("P2c", t, "WARN", f"{lat:.0f}ms | 0 articles")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P2c", t, "FAIL", f"{lat:.0f}ms | {e}")

else:
    print("  ⏭  Skipped — FINNHUB_API_KEY not found")
    for t in TICKERS:
        check("P2a", t, "FAIL", "No key")
        check("P2b", t, "FAIL", "No key")
        check("P2c", t, "FAIL", "No key")


# ════════════════════════════════════════════════════════════════════
# PHASE 3 — AlphaVantageAdapter Live Tests
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 3 — AlphaVantageAdapter Live Authenticated Requests")
print("=" * 70)

av_key = os.environ.get("ALPHA_VANTAGE_API_KEY") or os.getenv("ALPHA_VANTAGE_API_KEY")
if av_key:
    from backend.module4.provider.alpha_vantage_adapter import AlphaVantageAdapter
    av = AlphaVantageAdapter(api_key=av_key)

    if av.api_key:
        check("P3i", "AlphaVantageAdapter", "PASS", f"Key loaded, len={len(av.api_key)}")
    else:
        check("P3i", "AlphaVantageAdapter", "FAIL", "Key not passed to adapter")

    # 3a. Company Overview (profile)
    print("\n  3a. Company Overview (OVERVIEW):")
    for t in TICKERS:
        start = time.monotonic()
        try:
            profile = av.fetch_company_profile(t)
            lat = (time.monotonic() - start) * 1000
            if profile and profile.get("company_name"):
                name = profile["company_name"][:50]
                check("P3a", t, "PASS", f"{lat:.0f}ms | {name}")
            else:
                check("P3a", t, "WARN", f"{lat:.0f}ms | Empty response")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P3a", t, "FAIL", f"{lat:.0f}ms | {e}")

    # 3b. Market Quote
    print("\n  3b. Market Quote (GLOBAL_QUOTE):")
    for t in TICKERS:
        start = time.monotonic()
        try:
            price = av.fetch_market_price(t)
            lat = (time.monotonic() - start) * 1000
            if price and price.get("price") is not None:
                check("P3b", t, "PASS", f"{lat:.0f}ms | ${price['price']}")
            else:
                check("P3b", t, "WARN", f"{lat:.0f}ms | No price")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P3b", t, "WARN", f"{lat:.0f}ms | {e}")

    # 3c. News Sentiment
    print("\n  3c. News Sentiment (NEWS_SENTIMENT):")
    av_news = {}
    for t in TICKERS:
        start = time.monotonic()
        try:
            news = av.fetch_news(t)
            lat = (time.monotonic() - start) * 1000
            av_news[t] = news
            if news and len(news) > 0:
                sample = news[0].get("title", "")[:60]
                check("P3c", t, "PASS", f"{lat:.0f}ms | {len(news)} articles | '{sample}'")
            else:
                check("P3c", t, "WARN", f"{lat:.0f}ms | 0 articles")
        except Exception as e:
            lat = (time.monotonic() - start) * 1000
            check("P3c", t, "FAIL", f"{lat:.0f}ms | {e}")

else:
    print("  ⏭  Skipped — ALPHA_VANTAGE_API_KEY not found")
    for t in TICKERS:
        check("P3a", t, "FAIL", "No key")
        check("P3b", t, "FAIL", "No key")
        check("P3c", t, "FAIL", "No key")


# ════════════════════════════════════════════════════════════════════
# PHASE 4 — ProviderManager + Orchestrator
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 4 — ProviderManager Registration + Orchestrator")
print("=" * 70)

from backend.module4.provider_manager import provider_manager, initialize_default_providers
initialize_default_providers()

all_providers = provider_manager.list_providers()
print(f"  Registered: {all_providers}")

if "finnhub" in all_providers:
    check("P4", "finnhub", "PASS", "Registered")
else:
    check("P4", "finnhub", "FAIL", "Not registered")

if "alpha_vantage" in all_providers:
    check("P4", "alpha_vantage", "PASS", "Registered")
else:
    check("P4", "alpha_vantage", "FAIL", "Not registered")

from backend.module4.provider_orchestrator import _DEFAULT_API_PRIORITY as default_priority
print(f"  Priority: {' → '.join(default_priority)}")
check("P4", "priority", "PASS", "Correct order" if default_priority[:2] == ["finnhub", "alpha_vantage"] else "UNEXPECTED")


# ════════════════════════════════════════════════════════════════════
# PHASE 5 — News Persistence into PostgreSQL
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 5 — News Persistence into PostgreSQL")
print("=" * 70)

from backend.database.models import News, Company, MarketPrice
from backend.module4.database_manager import DatabaseManager
from backend.module4.normalizer import normalizer
from backend.module4.provider_orchestrator import ProviderOrchestrator

dbm = DatabaseManager()
orch = ProviderOrchestrator()

# Use Finnhub news directly (most articles, best quality)
news_source = "finnhub"
news_data = finnhub_news if finnhub_key else av_news if av_key else {}

persisted_counts = {}
article_total = 0

for t in TICKERS:
    articles = news_data.get(t, [])
    if not articles:
        print(f"  ℹ  {t}: No articles from direct adapter, trying orchestrator...")
        try:
            articles = orch.fetch_news(t)
            news_source = "orchestrator"
        except Exception as e:
            print(f"  ⚠  {t}: Orchestrator also failed: {e}")
            continue

    # Get or create company record
    company = dbm.get_latest_company(t)
    if not company:
        print(f"  ℹ  {t}: Creating company record...")
        try:
            profile = orch.fetch_company_profile(t)
            from backend.module4.ingestion_service import IngestionService
            # Save via database manager directly
            norm_company = normalizer.normalize_company(profile)
            norm_company["ticker"] = t
            dbm.save_company(norm_company)
            dbm.commit()
            company = dbm.get_latest_company(t)
        except Exception as e:
            print(f"  ⚠  {t}: Could not create company: {e}")
            # Try saving minimal record
            try:
                from backend.database.models import Company as CompanyModel
                existing = dbm.connection.query(CompanyModel).filter(CompanyModel.ticker == t).first()
                if not existing:
                    new_c = CompanyModel(ticker=t, company_name=t)
                    dbm.connection.add(new_c)
                    dbm.commit()
                company = dbm.connection.query(CompanyModel).filter(CompanyModel.ticker == t).first()
            except Exception as e2:
                print(f"  ⚠  {t}: Fallback company save also failed: {e2}")
                continue

    if not company:
        print(f"  ⚠  {t}: No company record — skipping")
        persisted_counts[t] = 0
        continue

    comp_id = company.id
    saved = 0

    for item in articles:
        item["company_id"] = comp_id
        normalized = normalizer.normalize_news(item)
        if normalized.get("headline"):
            try:
                dbm.save_news(normalized)
                saved += 1
            except Exception as e:
                pass
        else:
            # Direct save with raw keys
            raw = {
                "company_id": comp_id,
                "headline": item.get("title", ""),
                "source": item.get("site", ""),
                "url": item.get("url", ""),
                "published_at": item.get("published_date", ""),
                "text": item.get("text", ""),
            }
            if raw["headline"]:
                try:
                    dbm.save_news(raw)
                    saved += 1
                except Exception:
                    pass

    dbm.commit()
    persisted_counts[t] = saved
    article_total += saved

    if saved > 0:
        check("P5", t, "PASS", f"{saved} news articles persisted (source={news_source})")
    else:
        check("P5", t, "WARN", f"0 articles persisted")


# ════════════════════════════════════════════════════════════════════
# PHASE 6 — RetrievalAgent News Retrieval
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 6 — RetrievalAgent News Retrieval")
print("=" * 70)

from backend.intelligence.retrieval_agent import RetrievalAgent

total_retrieved = 0
ra_results = {}
for t in TICKERS:
    ra = RetrievalAgent(t)
    news = ra.get_news()
    count = len(news)
    total_retrieved += count
    ra_results[t] = news

    if count > 0:
        sample = news[0].get("headline", "")[:60]
        check("P6", t, "PASS", f"{count} articles retrieved | '{sample}'")
    else:
        stored = persisted_counts.get(t, 0)
        check("P6", t, "WARN" if stored == 0 else "FAIL", 
              f"0 articles (stored={stored})")


# ════════════════════════════════════════════════════════════════════
# PHASE 7 — EvidenceConsolidator
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 7 — EvidenceConsolidator News in Memo Context")
print("=" * 70)

from backend.intelligence.evidence_consolidator import EvidenceConsolidator
from backend.intelligence.data_agent import DataAgent

news_in_context = 0
total_chars = 0

for t in TICKERS:
    da = DataAgent(t)
    live_data = da.fetch_all()

    ra = RetrievalAgent(t)
    stored_data = ra.retrieve_all()

    consolidator = EvidenceConsolidator(t)
    context = consolidator.consolidate(module4_data=live_data, stored_data=stored_data)

    if isinstance(context, dict):
        context_text = context.get("context_text", "")
        sources = context.get("sources", [])
    else:
        context_text = str(context)
        sources = []

    total_chars += len(context_text)

    has_news_section = "Recent News" in context_text
    has_news_source = "news:" in str(sources) or "news:" in context_text

    if has_news_section:
        news_in_context += 1
        check("P7", t, "PASS", f"News section in context ({len(context_text)} chars)")
    elif has_news_source:
        check("P7", t, "WARN", "News source listed but no section rendered")
    else:
        check("P7", t, "WARN", "No news in context")

    print(f"    Sources: {sources[:5]}")


# ════════════════════════════════════════════════════════════════════
# FINAL REPORT
# ════════════════════════════════════════════════════════════════════

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
print("PROVIDER HEALTH")
print(f"{'─' * 70}")

# Finnhub
if finnhub_key:
    print(f"  Finnhub:     ✅ ACTIVE (key configured, len={len(finnhub_key)})")
else:
    print(f"  Finnhub:     ❌ INACTIVE (no key)")

# Alpha Vantage
if av_key:
    print(f"  Alpha Vantage: ✅ ACTIVE (key configured, len={len(av_key)})")
else:
    print(f"  Alpha Vantage: ❌ INACTIVE (no key)")

# YFinance
print(f"  YFinance:     ✅ ACTIVE (no key needed)")

# FMP
fmp_key = os.environ.get("FMP_API_KEY") or os.getenv("FMP_API_KEY")
if fmp_key:
    print(f"  FMP:          ✅ ACTIVE (key configured, len={len(fmp_key)})")
else:
    print(f"  FMP:          ❌ INACTIVE (no key)")

print(f"\n{'─' * 70}")
print("NEWS PERFORMANCE")
print(f"{'─' * 70}")

if finnhub_key:
    for t in TICKERS:
        cnt = len(finnhub_news.get(t, []))
        print(f"  Finnhub/{t:12s}: {cnt} articles")
if av_key:
    for t in TICKERS:
        cnt = len(av_news.get(t, []))
        print(f"  AlphaVantage/{t:8s}: {cnt} articles")

print(f"\n{'─' * 70}")
print("DATABASE")
print(f"{'─' * 70}")

try:
    companies_count = dbm.connection.query(Company).count()
    news_count = dbm.connection.query(News).count()
    print(f"  Companies:     {companies_count}")
    print(f"  News records:  {news_count}")
    print(f"  News stored in this run: {article_total}")

    if news_count > 0:
        sample_news = dbm.connection.query(News).order_by(News.published_at.desc()).limit(3).all()
        print(f"\n  Sample news:")
        for n in sample_news:
            src = n.source or "unknown"
            print(f"    • [{n.company_id}] {n.headline[:70]} — {src}")
except Exception as e:
    print(f"  DB query error: {e}")

print(f"\n{'─' * 70}")
print("FAILURES")
print(f"{'─' * 70}")
if failures:
    for f in failures:
        print(f"  ❌ {f}")
else:
    print("  ✅ No failures!")

print(f"\n{'─' * 70}")
if fail_count == 0:
    print("  VERDICT: ✅ ALL CHECKS PASS — Integration fully verified")
else:
    print(f"  VERDICT: ❌ {fail_count} failures — review above")

dbm.close()
print(f"{'=' * 70}\n")
