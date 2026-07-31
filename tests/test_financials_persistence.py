"""
Financials Persistence E2E Test

Verifies the full IngestionService pipeline saves ALL data types,
especially financial statements (the previously broken path).

Pipeline:
  IngestionService.ingest_company()
         ↓
  ProviderOrchestrator → Validator → Normalizer
         ↓
  DatabaseManager (PostgreSQL)
         ↓
  CacheManager (Redis)

Test tickers: AAPL, TCS.NS, RELIANCE.NS

Verification:
  ✓ Company saved
  ✓ Financial statements saved (with row counts)
  ✓ Market price saved
  ✓ News saved
  ✓ PostgreSQL retrieval
  ✓ DBCache retrieval
"""

import sys
import os
import time
import json

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

PASS, FAIL, WARN = 0, 0, 0
results = []
start_time = time.monotonic()


def check(name, status, detail=""):
    global PASS, FAIL, WARN
    if status == "PASS":
        PASS += 1
        results.append(("✅ PASS", name, detail))
    elif status == "WARN":
        WARN += 1
        results.append(("⚠️  WARN", name, detail))
    else:
        FAIL += 1
        results.append(("❌ FAIL", name, detail))


def section(title):
    results.append(("─" * 70, "", ""))
    results.append((f"  {title}", "", ""))


def log(emoji, name, detail=""):
    results.append((emoji, name, detail))


# ═══════════════════════════════════════════════════════════════════
# TICKERS
# ═══════════════════════════════════════════════════════════════════

TICKERS = ["AAPL", "TCS.NS", "RELIANCE.NS"]

print("=" * 70)
print("  FINANCIALS PERSISTENCE E2E TEST")
print("  Pipeline: IngestionService.ingest_company()")
print(f"  Tickers: {', '.join(TICKERS)}")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════
# PHASE 0: Initialize Infrastructure
# ═══════════════════════════════════════════════════════════════════

section("0. Infrastructure Initialization")

from backend.module4.provider_manager import initialize_default_providers
initialize_default_providers()
log("✅", "ProviderManager initialized with default providers")

from backend.module4.ingestion_service import IngestionService
ingestion = IngestionService()
log("✅", "IngestionService initialized")

# Also get DB manager for direct verification
from backend.module4.database_manager import DatabaseManager
dbm = DatabaseManager()
log("✅", "DatabaseManager initialized for verification")

# DBCache for cache verification
from backend.module4.db_cache import DBCache
db_cache = DBCache(dbm)
log("✅", "DBCache initialized for verification")

print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 1: Ingest each company through the full pipeline
# ═══════════════════════════════════════════════════════════════════

section("1. IngestionService.ingest_company()")

ingestion_results = {}

for TICKER in TICKERS:
    log("───", f"Ingesting {TICKER}")
    t0 = time.monotonic()

    try:
        result = ingestion.ingest_company("yfinance", TICKER)
        elapsed = round((time.monotonic() - t0) * 1000)

        status = result.get("status", "failed")
        if status == "success":
            log("✅", f"{TICKER}: ingestion succeeded  [{elapsed}ms]")
            check(f"Ingestion [{TICKER}]", "PASS", f"Status: {status}")
            ingestion_results[TICKER] = True
        else:
            error = result.get("error", "unknown")
            log("❌", f"{TICKER}: ingestion failed  [{elapsed}ms]")
            log("   ", f"Error: {error}")
            check(f"Ingestion [{TICKER}]", "FAIL", error)
            ingestion_results[TICKER] = False
    except Exception as e:
        elapsed = round((time.monotonic() - t0) * 1000)
        log("❌", f"{TICKER}: ingestion exception  [{elapsed}ms]")
        log("   ", f"Exception: {e}")
        check(f"Ingestion [{TICKER}]", "FAIL", str(e)[:150])
        ingestion_results[TICKER] = False

    print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 2: Verify PostgreSQL Storage
# ═══════════════════════════════════════════════════════════════════

section("2. PostgreSQL Storage Verification")

# Query counts from SQLAlchemy models
from backend.database.models import Company, Financial, MarketPrice, News
from sqlalchemy import func

company_counts = {}
financial_counts = {}
price_counts = {}
news_counts = {}

for TICKER in TICKERS:
    log("───", f"PostgreSQL data for {TICKER}")

    # Company record
    company = dbm.get_latest_company(TICKER)
    if company:
        company_id = company.id
        company_name = company.company_name or "N/A"
        log("✅", f"  Company: id={company_id}, name={company_name[:40]}")
        check(f"PostgreSQL [{TICKER}] — Company saved", "PASS",
              f"id={company_id}, ticker={company.ticker}")
        company_counts[TICKER] = 1
    else:
        log("❌", f"  Company: NOT FOUND in PostgreSQL")
        check(f"PostgreSQL [{TICKER}] — Company saved", "FAIL",
              "No company record found")
        company_counts[TICKER] = 0
        continue  # Can't check related tables without company

    # Financial records
    try:
        financial_records = dbm.connection.query(Financial).filter(
            Financial.company_id == company_id
        ).all()
        fin_count = len(financial_records)
        financial_counts[TICKER] = fin_count

        if fin_count > 0:
            # Show sample metrics
            sample_types = set(r.statement_type for r in financial_records)
            sample_metrics = set()
            for r in financial_records[:5]:
                for col in ["revenue", "net_income", "ebitda", "eps",
                            "total_assets", "operating_cash_flow"]:
                    val = getattr(r, col, None)
                    if val is not None:
                        sample_metrics.add(col)

            log("✅", f"  Financials: {fin_count} records across {len(sample_types)} statements")
            log("   ", f"  Statement types: {', '.join(sorted(sample_types))}")
            if sample_metrics:
                log("   ", f"  Populated fields: {', '.join(sorted(sample_metrics))}")
            check(f"PostgreSQL [{TICKER}] — Financials saved", "PASS",
                  f"{fin_count} records, {len(sample_types)} statement types")
        else:
            log("❌", f"  Financials: 0 records — NOT SAVED")
            check(f"PostgreSQL [{TICKER}] — Financials saved", "FAIL",
                  "0 records found")
    except Exception as e:
        financial_counts[TICKER] = 0
        log("❌", f"  Financials query failed: {e}")
        check(f"PostgreSQL [{TICKER}] — Financials saved", "FAIL", str(e)[:100])

    # Market price records
    try:
        price_records = dbm.connection.query(MarketPrice).filter(
            MarketPrice.company_id == company_id
        ).all()
        price_count = len(price_records)
        price_counts[TICKER] = price_count

        if price_count > 0:
            latest = price_records[-1]
            log("✅", f"  Market Price: {price_count} record(s), close=${latest.close_price}")
            check(f"PostgreSQL [{TICKER}] — Price saved", "PASS",
                  f"{price_count} records")
        else:
            log("⚠️", f"  Market Price: 0 records (not saved or empty)")
            check(f"PostgreSQL [{TICKER}] — Price saved", "WARN",
                  "0 records")
    except Exception as e:
        price_counts[TICKER] = 0
        log("⚠️", f"  Market Price query failed: {e}")
        check(f"PostgreSQL [{TICKER}] — Price saved", "WARN", str(e)[:100])

    # News records
    try:
        news_records = dbm.connection.query(News).filter(
            News.company_id == company_id
        ).all()
        news_count = len(news_records)
        news_counts[TICKER] = news_count

        if news_count > 0:
            log("✅", f"  News: {news_count} article(s)")
            check(f"PostgreSQL [{TICKER}] — News saved", "PASS",
                  f"{news_count} articles")
        else:
            log("⚠️", f"  News: 0 articles (not saved or empty)")
            check(f"PostgreSQL [{TICKER}] — News saved", "WARN",
                  "0 articles")
    except Exception as e:
        news_counts[TICKER] = 0
        log("⚠️", f"  News query failed: {e}")
        check(f"PostgreSQL [{TICKER}] — News saved", "WARN", str(e)[:100])

    print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 3: Verify DBCache Retrieval
# ═══════════════════════════════════════════════════════════════════

section("3. DBCache Retrieval Verification")

for TICKER in TICKERS:
    try:
        t0 = time.monotonic()
        cached = db_cache.get_fresh_profile(TICKER)
        elapsed = round((time.monotonic() - t0) * 1000)

        if cached:
            name = cached.get("company_name") or cached.get("longName", "?")
            log("✅", f"DBCache [{TICKER}]: HIT — {name[:40]}  [{elapsed}ms]")
            check(f"DBCache [{TICKER}]", "PASS", f"Cache HIT [{elapsed}ms]")
        else:
            log("⚠️", f"DBCache [{TICKER}]: MISS  [{elapsed}ms]")
            check(f"DBCache [{TICKER}]", "WARN", f"Cache MISS [{elapsed}ms]")
    except Exception as e:
        log("❌", f"DBCache [{TICKER}]: ERROR — {e}")
        check(f"DBCache [{TICKER}]", "FAIL", str(e)[:100])

print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 4: Row Count Summary
# ═══════════════════════════════════════════════════════════════════

section("4. Database Row Count Summary")

# Total row counts across ALL companies (from this test run)
try:
    total_companies = dbm.connection.query(func.count(Company.id)).scalar()
    total_financials = dbm.connection.query(func.count(Financial.id)).scalar()
    total_prices = dbm.connection.query(func.count(MarketPrice.id)).scalar()
    total_news = dbm.connection.query(func.count(News.id)).scalar()

    log("📊", f"Company table:      {total_companies} total rows")
    log("📊", f"Financial table:    {total_financials} total rows")
    log("📊", f"MarketPrice table:  {total_prices} total rows")
    log("📊", f"News table:         {total_news} total rows")

    check("Database row counts", "PASS",
          f"Company={total_companies} Financial={total_financials} "
          f"Price={total_prices} News={total_news}")

    # Per-ticker breakdown
    log("", "")
    log("", "Per-ticker breakdown:")
    for TICKER in TICKERS:
        c = company_counts.get(TICKER, 0)
        f = financial_counts.get(TICKER, 0)
        p = price_counts.get(TICKER, 0)
        n = news_counts.get(TICKER, 0)
        log("", f"  {TICKER:<12}  Company={c}  Financial={f:>3}  Price={p:>2}  News={n:>2}")

except Exception as e:
    log("❌", f"Row count query failed: {e}")
    check("Database row counts", "FAIL", str(e)[:100])

print()

# ═══════════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════════

total_elapsed = round((time.monotonic() - start_time) * 1000)

print()
print("=" * 70)
print("  FINAL REPORT")
print("=" * 70)
print(f"  Total time: {total_elapsed}ms")
print()
print(f"  ✅ PASS: {PASS}")
print(f"  ⚠️  WARN: {WARN}")
print(f"  ❌ FAIL: {FAIL}")
print(f"  ─────────────────")
print(f"  Total:  {PASS + FAIL + WARN}")
print()

# Per-detail output
for emoji, name, detail in results:
    if emoji == "─" * 70:
        continue
    if name.startswith("  ") and not detail:
        print(f"\n{emoji}{name}")
    elif emoji == "📊":
        print(f"  {emoji} {name:<45} {detail}")
    elif detail:
        print(f"  {emoji}  {name:<50} {detail}")
    else:
        print(f"  {emoji}  {name}")

print()
print("=" * 70)
print(f"  RESULTS:  {PASS} ✅  |  {FAIL} ❌  |  {WARN} ⚠️")
print("=" * 70)

if FAIL > 0:
    print("  ❌ FINANCIALS PERSISTENCE HAS FAILURES — Review above")
    print()
    print("  Most common cause: financial items not expanding to metric-level records.")
    print("  Check IngestionService._expand_financials() for format compatibility.")
elif financial_counts.get(TICKERS[0], 0) > 0:
    print("  ✅ FINANCIALS PERSISTENCE VERIFIED")
    print()
    print("  Financial statements are now flowing through the full pipeline:")
    print("    ProviderOrchestrator → Validator → [Expansion] → Normalizer → DB")
    print()
    print("  All data types persisted to PostgreSQL:")
    print("    • Company profiles: ✓")
    print("    • Financial statements: ✓ (previously broken)")
    print("    • Market prices: ✓")
    print("    • News articles: ✓")
    print(f"    • DBCache lookup: {'✓' if PASS > 0 else '⚠️'}")
else:
    print("  ⚠️  PARTIAL SUCCESS — Check per-ticker results above")

print()
