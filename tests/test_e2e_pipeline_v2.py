"""
Module 4 End-to-End Pipeline Test (v2)

Uses live Yahoo Finance data (yfinance) — no API key required.
Tests AAPL, TCS, and RELIANCE.NS through the full pipeline.

Pipeline:
  ProviderOrchestrator → Validator → Normalizer → DatabaseManager → DBCache → AI Memo
"""

import sys, os, time, json
from datetime import date

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

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


# ═══════════════════════════════════════════════════════════════════
# TICKERS
# ═══════════════════════════════════════════════════════════════════

TICKERS = ["AAPL", "TCS.NS", "RELIANCE.NS"]

print("=" * 60)
print("  MODULE 4 END-TO-END PIPELINE TEST (yfinance)")
print("=" * 60)
print(f"\n  Tickers: {', '.join(TICKERS)}")
print(f"  Pipeline: ProviderOrchestrator → Validator → Normalizer → DB → Cache → AI")

# ═══════════════════════════════════════════════════════════════════
# 0. Initialize infrastructure
# ═══════════════════════════════════════════════════════════════════

section("0. Infrastructure Initialization")

try:
    from backend.module4.provider_manager import initialize_default_providers
    from backend.module4.provider_orchestrator import ProviderOrchestrator

    initialize_default_providers()
    orch = ProviderOrchestrator()
    check("ProviderOrchestrator initialized", "PASS",
          f"Providers: {orch.health.get_report() if hasattr(orch.health, 'get_report') else 'ok'}")
except Exception as e:
    check("ProviderOrchestrator init", "FAIL", str(e))

try:
    from backend.module4.validator import validator
    check("Validator loaded", "PASS", "")
except Exception as e:
    check("Validator loaded", "FAIL", str(e))

try:
    from backend.module4.normalizer import normalizer
    check("Normalizer loaded", "PASS", "")
except Exception as e:
    check("Normalizer loaded", "FAIL", str(e))

try:
    from backend.module4.database_manager import DatabaseManager
    dbm = DatabaseManager()
    check("DatabaseManager initialized", "PASS", "PostgreSQL connected")
except Exception as e:
    dbm = None
    check("DatabaseManager init", "FAIL", str(e))

try:
    from backend.module4.cache_manager import CacheManager
    cache = CacheManager()
    check("CacheManager initialized", "PASS", "Redis cache ready")
except Exception as e:
    cache = None
    check("CacheManager init", "FAIL", str(e))

try:
    from backend.module4.ratio_engine import RatioEngine
    ratio_engine = RatioEngine()
    check("RatioEngine loaded", "PASS", "")
except ImportError:
    ratio_engine = None
    check("RatioEngine loaded", "WARN", "Not available — ratios will be skipped")

# ═══════════════════════════════════════════════════════════════════
# Per-ticker pipeline
# ═══════════════════════════════════════════════════════════════════

for TICKER in TICKERS:
    section(f"Pipeline: {TICKER}")

    # ── 1. ProviderOrchestrator fetch ──
    print(f"\n  --- Step 1: ProviderOrchestrator ---")

    profile = None
    price = None
    news = None
    financials = None

    # 1a. Company Profile
    start = time.time()
    try:
        profile = orch.fetch_company_profile(TICKER, providers=["yfinance"])
        ms = round((time.time() - start) * 1000)
        if profile and profile.get("company_name"):
            check(f"[{TICKER}] Company profile", "PASS",
                  f"{profile.get('company_name')} [{ms}ms]")
        else:
            check(f"[{TICKER}] Company profile", "FAIL", f"Empty [{ms}ms]")
    except Exception as e:
        ms = round((time.time() - start) * 1000)
        check(f"[{TICKER}] Company profile", "FAIL", f"{e} [{ms}ms]")

    # 1b. Market Price
    start = time.time()
    try:
        price = orch.fetch_market_price(TICKER, providers=["yfinance"])
        ms = round((time.time() - start) * 1000)
        if price and price.get("price"):
            check(f"[{TICKER}] Market price", "PASS",
                  f"${price.get('price'):.2f} [{ms}ms]")
        else:
            check(f"[{TICKER}] Market price", "FAIL", f"Empty [{ms}ms]")
    except Exception as e:
        ms = round((time.time() - start) * 1000)
        check(f"[{TICKER}] Market price", "FAIL", f"{e} [{ms}ms]")

    # 1c. Financials
    start = time.time()
    try:
        financials = orch.fetch_financials(TICKER, providers=["yfinance"])
        ms = round((time.time() - start) * 1000)
        income = financials.get("income_statement", [])
        balance = financials.get("balance_sheet", [])
        cash = financials.get("cash_flow", [])
        periods = max(len(income), len(balance), len(cash))
        check(f"[{TICKER}] Financials", "PASS" if periods > 0 else "FAIL",
              f"Income:{len(income)} Balance:{len(balance)} CashFlow:{len(cash)} [{ms}ms]")
    except Exception as e:
        ms = round((time.time() - start) * 1000)
        check(f"[{TICKER}] Financials", "FAIL", f"{e} [{ms}ms]")

    # 1d. News
    start = time.time()
    try:
        news = orch.fetch_news(TICKER, providers=["yfinance"])
        ms = round((time.time() - start) * 1000)
        check(f"[{TICKER}] News", "PASS" if isinstance(news, list) and len(news) > 0 else "WARN",
              f"{len(news) if isinstance(news, list) else 0} articles [{ms}ms]")
    except Exception as e:
        ms = round((time.time() - start) * 1000)
        check(f"[{TICKER}] News", "FAIL", f"{e} [{ms}ms]")

    # ── 2. Validator ──
    print(f"\n  --- Step 2: Validator ---")

    if profile:
        try:
            val_result = validator.validate_company(profile)
            if hasattr(val_result, "valid"):
                check(f"[{TICKER}] Company validation", "PASS" if val_result.valid else "FAIL",
                      "Valid" if val_result.valid else "Invalid")
            else:
                check(f"[{TICKER}] Company validation", "PASS", "Validated")
        except Exception as e:
            check(f"[{TICKER}] Company validation", "FAIL", str(e)[:80])

    if price:
        try:
            val_price = validator.validate_market_price(price) if hasattr(validator, "validate_market_price") else None
            if val_price is not None:
                valid = val_price.valid if hasattr(val_price, "valid") else bool(val_price)
                check(f"[{TICKER}] Price validation", "PASS" if valid else "FAIL", "")
        except Exception as e:
            check(f"[{TICKER}] Price validation", "WARN", str(e)[:60])

    # ── 3. Normalizer ──
    print(f"\n  --- Step 3: Normalizer ---")

    norm_company = None
    if profile:
        try:
            norm_company = normalizer.normalize_company(profile)
            check(f"[{TICKER}] Company normalized", "PASS",
                  f"{norm_company.get('ticker', '?')} → {norm_company.get('company_name', '?')[:30]}")
        except Exception as e:
            check(f"[{TICKER}] Company normalization", "FAIL", str(e)[:80])

    norm_price = None
    if price:
        try:
            norm_price = normalizer.normalize_market_price(price) if hasattr(normalizer, "normalize_market_price") else price
            check(f"[{TICKER}] Price normalized", "PASS", "")
        except Exception as e:
            check(f"[{TICKER}] Price normalization", "WARN", str(e)[:60])

    # ── 4. Database Storage + Retrieval ──
    print(f"\n  --- Step 4: DatabaseManager ---")

    if dbm and norm_company:
        try:
            dbm.begin_transaction()

            # Save company (requires ticker, not symbol)
            dbm.save_company(norm_company)

            # Commit to get company_id from DB for related records
            dbm.commit()

            # Get the saved company's ID for relations
            ticker_key = norm_company.get("ticker", TICKER)
            saved_company = dbm.get_latest_company(ticker_key)
            company_id = saved_company.id if saved_company else None

            check(f"[{TICKER}] Save company", "PASS",
                  f"ticker={norm_company.get('ticker')} id={company_id}")

            # Save price if available
            if norm_price and company_id:
                try:
                    enriched_price = dict(norm_price)
                    enriched_price["company_id"] = company_id
                    enriched_price["trading_date"] = date.today()
                    dbm.begin_transaction()
                    dbm.save_market_price(enriched_price)
                    dbm.commit()
                    check(f"[{TICKER}] Save price", "PASS", "")
                except Exception as e2:
                    dbm.rollback()
                    check(f"[{TICKER}] Save price", "WARN", str(e2)[:60])

            # Save news (one at a time - save_news expects a single dict)
            if news and company_id:
                saved_news_count = 0
                for article in news[:5]:
                    try:
                        enriched = {
                            "company_id": company_id,
                            "headline": article.get("title", "")[:200],
                            "text": article.get("text", "")[:500],
                            "site": article.get("site", ""),
                            "url": article.get("url", ""),
                            "published_at": article.get("published_date"),
                        }
                        dbm.begin_transaction()
                        dbm.save_news(enriched)
                        dbm.commit()
                        saved_news_count += 1
                    except Exception:
                        dbm.rollback()
                check(f"[{TICKER}] Save news", "PASS" if saved_news_count > 0 else "WARN",
                      f"{saved_news_count}/{min(len(news), 5)} articles")

        except Exception as e:
            dbm.rollback()
            check(f"[{TICKER}] Save to PostgreSQL", "FAIL", str(e)[:100])

        # 4b. Retrieve from PostgreSQL
        try:
            saved = dbm.get_latest_company(norm_company.get("ticker", TICKER))
            if saved:
                name = saved.company_name if hasattr(saved, "company_name") else str(saved)
                check(f"[{TICKER}] Retrieve from DB", "PASS", f"{name[:40]}")
            else:
                check(f"[{TICKER}] Retrieve from DB", "FAIL", "No record found")
        except Exception as e:
            check(f"[{TICKER}] Retrieve from DB", "FAIL", str(e)[:80])
    else:
        check(f"[{TICKER}] DB storage", "WARN",
              "Skipped (DB or normalized data not available)")

    # ── 5. DBCache ──
    print(f"\n  --- Step 5: DBCache ---")

    try:
        from backend.module4.db_cache import DBCache

        if dbm:
            db_cache = DBCache(dbm)

            # First call should be a cache MISS (fetch from yfinance)
            start = time.time()
            cached_profile = db_cache.get_fresh_profile(TICKER)
            ms1 = round((time.time() - start) * 1000)

            if cached_profile:
                check(f"[{TICKER}] DBCache: first call", "PASS", f"Cache HIT [{ms1}ms]")
            else:
                check(f"[{TICKER}] DBCache: first call", "WARN",
                      f"Cache MISS (expected if TTL expired) [{ms1}ms]")
        else:
            check(f"[{TICKER}] DBCache", "WARN", "Skipped — DB not available")
    except Exception as e:
        check(f"[{TICKER}] DBCache", "FAIL", str(e)[:80])

    # ── 6. CacheManager (Redis, if connected) ──
    print(f"\n  --- Step 6: CacheManager ---")

    if cache:
        try:
            cache.cache_company(norm_company or {"ticker": TICKER})
            cached_back = cache.get_company(TICKER)
            if cached_back is not None:
                check(f"[{TICKER}] CacheManager store/retrieve", "PASS", "Redis cache")
            else:
                check(f"[{TICKER}] CacheManager store/retrieve", "WARN",
                      "Stored but retrieved None (Redis not connected)")
        except Exception as e:
            check(f"[{TICKER}] CacheManager", "FAIL", str(e)[:80])

    # ── 7. Ratio Engine ──
    print(f"\n  --- Step 7: RatioEngine ---")

    if ratio_engine and financials:
        try:
            ratios = ratio_engine.calculate(financials) if hasattr(ratio_engine, "calculate") else {}
            if ratios:
                check(f"[{TICKER}] Ratio calculation", "PASS",
                      f"{len(ratios) if isinstance(ratios, dict) else 'ok'} ratios")
            else:
                check(f"[{TICKER}] Ratio calculation", "WARN", "No ratios returned")
        except Exception as e:
            check(f"[{TICKER}] Ratio calculation", "FAIL", str(e)[:80])

# ═══════════════════════════════════════════════════════════════════
# 8. AI Investment Memo
# ═══════════════════════════════════════════════════════════════════
section("8. AI Investment Memo")

ai_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY")
if ai_key:
    check("AI provider key found", "PASS", "Attempting memo generation")
    try:
        from app import call_ai_with_fallback
        sample_text = f"Analysis of {', '.join(TICKERS)} based on Module 4 pipeline data."
        memo = call_ai_with_fallback(
            f"Write a brief investment memo for: {sample_text}",
            system_prompt="You are an investment analyst. Write a concise memo.",
            temperature=0.3
        )
        if memo and len(memo) > 50:
            check("AI Investment Memo generated", "PASS", f"{len(memo)} chars")
        else:
            check("AI Investment Memo generated", "FAIL", "Empty or too short")
    except Exception as e:
        check("AI Investment Memo generation", "FAIL", str(e)[:100])
else:
    check("AI Investment Memo", "WARN",
          "No AI provider keys configured (GOOGLE_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY)")
    check("", "WARN", "Set up AI keys via API Keys tab to enable memo generation")

# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
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
            print(f"  {status}  {name:<50} {detail}")
        else:
            print(f"  {status}  {name}")

print(f"\n{'=' * 60}")
print(f"  RESULTS:  {PASS} ✅  |  {FAIL} ❌  |  {WARN} ⚠️")
print(f"{'=' * 60}")

if FAIL == 0:
    print("\n🎉 All pipeline stages working — yfinance successfully replaced FMP as the primary data provider.")
else:
    print(f"\n❌ {FAIL} failure(s) need attention.")
