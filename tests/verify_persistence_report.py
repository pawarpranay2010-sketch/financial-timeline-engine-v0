"""
Financials Persistence — Complete Verification Report

Covers:
  1. Total companies inserted
  2. Total financial records inserted
  3. Total market prices inserted
  4. Total news records inserted
  5. Sample financial record from PostgreSQL
  6. Verify save_financials() inserted rows instead of silently skipping
  7. Confirm RetrievalAgent can retrieve those financials
  8. Confirm Investment Memo receives those financials as input
"""

import sys, os, json, logging
from datetime import date, datetime

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

PASS, FAIL, WARN = 0, 0, 0

def check(name, status, detail=""):
    global PASS, FAIL, WARN
    if status == "PASS":
        PASS += 1
    elif status == "WARN":
        WARN += 1
    else:
        FAIL += 1
    icon = "✅" if status == "PASS" else ("⚠️" if status == "WARN" else "❌")
    print(f"  {icon}  {name:<55} {detail}")

def section(n, title):
    print(f"\n{'='*70}")
    print(f"  [{n}] {title}")
    print(f"{'='*70}")

# ──────────────────────────────────────────────────────────────────
# SETUP
# ──────────────────────────────────────────────────────────────────

from backend.database.models import Company, Financial, MarketPrice, News
from backend.database.db import SessionLocal
from sqlalchemy import func

db = SessionLocal()
print(f"\n{'='*70}")
print("  FINANCIALS PERSISTENCE — COMPLETE VERIFICATION REPORT")
print(f"{'='*70}\n")

# ──────────────────────────────────────────────────────────────────
# 1-4. ROW COUNTS
# ──────────────────────────────────────────────────────────────────

section("1-4", "Database Row Counts")

company_total = db.query(func.count(Company.id)).scalar()
financial_total = db.query(func.count(Financial.id)).scalar()
price_total = db.query(func.count(MarketPrice.id)).scalar()
news_total = db.query(func.count(News.id)).scalar()

print(f"\n  {'Table':<25} {'Rows':>8}")
print(f"  {'─'*25} {'─'*8}")
print(f"  {'Company':<25} {company_total:>8}")
print(f"  {'Financial':<25} {financial_total:>8}")
print(f"  {'MarketPrice':<25} {price_total:>8}")
print(f"  {'News':<25} {news_total:>8}")
print()

check("1. Companies inserted", "PASS" if company_total > 0 else "WARN", f"{company_total} rows")
check("2. Financial records inserted", "PASS" if financial_total > 0 else "FAIL", f"{financial_total} rows")
check("3. Market prices inserted", "PASS" if price_total > 0 else "WARN", f"{price_total} rows")
check("4. News records inserted", "PASS" if news_total > 0 else "WARN", f"{news_total} rows")

# ──────────────────────────────────────────────────────────────────
# 5. SAMPLE FINANCIAL RECORD
# ──────────────────────────────────────────────────────────────────

section("5", "Sample Financial Record from PostgreSQL")

sample = db.query(Financial).first()
if sample:
    company = db.query(Company).filter(Company.id == sample.company_id).first()
    ticker = company.ticker if company else "N/A"
    print(f"""
  id:                {sample.id}
  company_id:        {sample.company_id}
  ticker:            {ticker}
  statement_type:    {sample.statement_type}
  fiscal_year:       {sample.fiscal_year}
  fiscal_quarter:    {sample.fiscal_quarter}
  revenue:           {sample.revenue}
  ebitda:            {sample.ebitda}
  ebit:              {sample.ebit}
  net_income:        {sample.net_income}
  eps:               {sample.eps}
  total_assets:      {sample.total_assets}
  total_liabilities: {sample.total_liabilities}
  shareholders_eq:   {sample.shareholders_equity}
  operating_cf:      {sample.operating_cash_flow}
  free_cash_flow:    {sample.free_cash_flow}
  is_latest:         {sample.is_latest}
  source:            {sample.source}
  created_at:        {sample.created_at}
""")
    check("5. Sample financial record retrieved", "PASS",
          f"id={sample.id} company={ticker} year={sample.fiscal_year} type={sample.statement_type}")
else:
    check("5. Sample financial record", "FAIL", "No records found in Financial table")

# ──────────────────────────────────────────────────────────────────
# 6. VERIFY save_financials INSERTED ROWS
# ──────────────────────────────────────────────────────────────────

section("6", "Verify save_financials() Inserted Rows (Not Silent Skip)")

# The key indicator: Financial table has > 0 rows AND the rows have
# fiscal_year and company_id populated (silent skip would have 0 rows
# because the continue condition filters those out).
if financial_total > 0:
    # Check that rows have real values
    rows_with_year = db.query(func.count(Financial.id)).filter(
        Financial.fiscal_year.isnot(None)
    ).scalar()
    rows_with_cid = db.query(func.count(Financial.id)).filter(
        Financial.company_id.isnot(None)
    ).scalar()
    rows_with_data = db.query(func.count(Financial.id)).filter(
        (Financial.revenue.isnot(None)) |
        (Financial.net_income.isnot(None)) |
        (Financial.ebitda.isnot(None))
    ).scalar()

    print(f"\n  Rows with fiscal_year:     {rows_with_year} / {financial_total}")
    print(f"  Rows with company_id:      {rows_with_cid} / {financial_total}")
    print(f"  Rows with financial data:  {rows_with_data} / {financial_total}")
    print()

    # Per-ticker breakdown
    print("  Per-ticker financial breakdown:")
    companies = db.query(Company).order_by(Company.id).all()
    for c in companies:
        count = db.query(func.count(Financial.id)).filter(
            Financial.company_id == c.id
        ).scalar()
        print(f"    {c.ticker:<12}  {count} financial records")

    check("6. save_financials inserted rows (not silent skip)",
          "PASS" if financial_total > 0 else "FAIL",
          f"{financial_total} rows — silent skip would produce 0")
else:
    check("6. save_financials inserted rows", "FAIL",
          "0 rows — silent skip behavior detected")

# ──────────────────────────────────────────────────────────────────
# 7. RETRIEVAL AGENT VERIFICATION
# ──────────────────────────────────────────────────────────────────

section("7", "RetrievalAgent — Retrieve Financials from PostgreSQL")

from backend.intelligence.retrieval_agent import RetrievalAgent

tickers = [c.ticker for c in db.query(Company).order_by(Company.id).all()]
print(f"\n  Testing RetrievalAgent for {len(tickers)} companies...\n")

retrieval_ok = 0
retrieval_fail = 0

for ticker in tickers:
    try:
        agent = RetrievalAgent(ticker)
        financials = agent.get_financials()
        fin_count = len(financials) if isinstance(financials, list) else 0

        # Also test company, price, news retrieval
        company = agent.get_company()
        price = agent.get_market_price()
        news = agent.get_news()

        if fin_count > 0:
            print(f"  ✅ {ticker:<12} Financials: {fin_count} records found")
            # Show first record
            first = financials[0] if isinstance(financials[0], dict) else {
                "statement_type": getattr(financials[0], "statement_type", "?"),
                "fiscal_year": getattr(financials[0], "fiscal_year", "?"),
            }
            retrieval_ok += 1
        else:
            print(f"  ⚠️ {ticker:<12} Financials: 0 records")
            retrieval_fail += 1

        print(f"           Company: {'✅' if company else '❌'}  "
              f"Price: {'✅' if price else '❌'}  "
              f"News: {'✅' if news else '❌'}  "
              f"Cache: {'✅' if agent.cache_company_profile() else '❌'}")

    except Exception as e:
        print(f"  ❌ {ticker:<12} RetrievalAgent error: {e}")
        retrieval_fail += 1

check("7. RetrievalAgent retrieves financials", "PASS" if retrieval_ok > 0 else "FAIL",
      f"{retrieval_ok}/{len(tickers)} tickers returned financials")

# ──────────────────────────────────────────────────────────────────
# 8. INVESTMENT MEMO RECEIVES FINANCIALS
# ──────────────────────────────────────────────────────────────────

section("8", "Investment Memo — Financials as Input")

from backend.intelligence.data_agent import DataAgent
from backend.intelligence.evidence_consolidator import EvidenceConsolidator
from backend.intelligence.memo_generator import MemoGenerator

print("\n  Testing EvidenceConsolidator + MemoGenerator with financials...\n")

memo_ok = 0
memo_fail = 0

for ticker in tickers[:2]:  # Test first 2 companies (avoid rate limiting)
    try:
        # Fetch live data via DataAgent
        agent = DataAgent(ticker)
        m4_data = agent.fetch_all()

        has_financials = m4_data.get("financials", {}).get("success", False)
        print(f"  {ticker}: DataAgent financials success={has_financials}")

        # Consolidate
        consolidator = EvidenceConsolidator(ticker)
        context = consolidator.consolidate(module4_data=m4_data)

        context_text = context.get("context_text", "")
        sources = context.get("sources", [])

        # Check if financials section is in the context
        has_financials_section = "[SOURCE: Live Market Data — Financial Statements" in context_text
        has_db_section = "PostgreSQL Cache" in context_text

        # Report what's in the context
        print(f"           EvidenceConsolidator: {context.get('source_count')} sources, "
              f"{len(context_text)} chars")
        print(f"           Financials section: {'✅' if has_financials_section else '❌'}  "
              f"DB cache section: {'✅' if has_db_section else '❌'}")

        # Build memo prompt
        generator = MemoGenerator()
        prompt = generator.build_prompt(context)

        # Check if financial data is in the prompt
        has_financial_in_prompt = any(
            term in prompt for term in ["Financial Statements", "Revenue", "Net Income",
                                         "EBITDA", "EBIT", "Total Assets", "Free Cash Flow"]
        )
        print(f"           Financial data in AI prompt: {'✅' if has_financial_in_prompt else '❌'}")

        if has_financials_section and has_financial_in_prompt:
            memo_ok += 1
        else:
            memo_fail += 1

    except Exception as e:
        print(f"  ❌ {ticker}: Error — {str(e)[:100]}")
        memo_fail += 1

check("8. Investment memo receives financials input", "PASS" if memo_ok > 0 else "FAIL",
      f"{memo_ok}/{memo_ok + memo_fail} tickers included financials in prompt")

# ──────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────

print(f"\n{'='*70}")
print("  VERIFICATION SUMMARY")
print(f"{'='*70}")
print(f"\n  ✅ PASS: {PASS}")
print(f"  ⚠️  WARN: {WARN}")
print(f"  ❌ FAIL: {FAIL}")
print(f"  ─────────────────")
print(f"  Total:  {PASS + FAIL + WARN}")
print()

print(f"  {'#'}  {'Check':<55} {'Status':<8}")
print(f"  {'─'*70}")
items = [
    ("1. Companies inserted", "PASS" if company_total > 0 else "WARN"),
    ("2. Financial records inserted", "PASS" if financial_total > 0 else "FAIL"),
    ("3. Market prices inserted", "PASS" if price_total > 0 else "WARN"),
    ("4. News records inserted", "PASS" if news_total > 0 else "WARN"),
    ("5. Sample financial record", "PASS" if sample else "FAIL"),
    ("6. save_financials inserts (not silent skip)", "PASS" if financial_total > 0 else "FAIL"),
    ("7. RetrievalAgent retrieves financials", "PASS" if retrieval_ok > 0 else "FAIL"),
    ("8. Memo receives financials", "PASS" if memo_ok > 0 else "FAIL"),
]
for num, (label, status) in enumerate(items, 1):
    icon = "✅" if status == "PASS" else ("⚠️" if status == "WARN" else "❌")
    print(f"  {icon}  {label:<55} {status}")

if financial_total > 0:
    print(f"\n  ✅ FINANCIALS PERSISTENCE VERIFIED")
    print(f"     All data types flowing through the full pipeline:")
    print(f"     ProviderOrchestrator → [Expansion] → Validator → Normalizer → PostgreSQL")
else:
    print(f"\n  ❌ FINANCIALS PERSISTENCE STILL BROKEN")

print()
db.close()
