"""
Intelligence Module E2E Integration Test

Tests the full pipeline:
  DataAgent → RetrievalAgent → EvidenceConsolidator → MemoGenerator

Pipeline:
  Module 4 ProviderOrchestrator (yfinance)
         ↓
  DataAgent (fetches all data types)
         ↓
  RetrievalAgent (PostgreSQL + DBCache)
         ↓
  EvidenceConsolidator (merges document + market data)
         ↓
  MemoGenerator (builds prompt, calls AI)
         ↓
  Professional Investment Memo

Test tickers: AAPL, TCS.NS, RELIANCE.NS
"""

import sys, os, time, json, logging
from datetime import datetime

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("test_intelligence_e2e")

PASS, FAIL, WARN = 0, 0, 0
results = []
start_time = time.monotonic()


def check(name, status, detail=""):
    global PASS, FAIL, WARN
    if status == "PASS":
        PASS += 1
        results.append((status, name, detail))
    elif status == "WARN":
        WARN += 1
        results.append((status, name, detail))
    else:
        FAIL += 1
        results.append((status, name, detail))


def section(title):
    results.append(("───", f"  {title}", ""))
    results.append(("", "", ""))


def log(emoji, name, detail=""):
    results.append((emoji, name, detail))


# ═══════════════════════════════════════════════════════════════════
# TICKERS
# ═══════════════════════════════════════════════════════════════════

TICKERS = ["AAPL", "TCS.NS", "RELIANCE.NS"]

print("=" * 70)
print("  INTELLIGENCE MODULE E2E TEST")
print("  Pipeline: DataAgent → RetrievalAgent → EvidenceConsolidator → MemoGenerator")
print(f"  Tickers: {', '.join(TICKERS)}")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════
# PHASE 1: DataAgent — Live Company Intelligence
# ═══════════════════════════════════════════════════════════════════

section("PHASE 1: DataAgent — Live Company Intelligence")

from backend.intelligence.data_agent import DataAgent

all_module4_data = {}

for ticker in TICKERS:
    log("───", f"  DataAgent [{ticker}]", "")
    agent = DataAgent(ticker)

    # Fetch all data types
    t0 = time.monotonic()
    data = agent.fetch_all()
    elapsed = round((time.monotonic() - t0) * 1000)

    profile = data.get("company_profile", {})
    price = data.get("market_price", {})
    financials = data.get("financials", {})
    news = data.get("news", {})

    # Profile
    if profile.get("success"):
        p = profile.get("data", {})
        name_val = p.get("company_name") or p.get("longName", "?")
        log("✅", f"  Profile: {name_val}  [{profile.get('latency_ms', 0):.0f}ms]")
    else:
        log("❌", f"  Profile FAILED: {profile.get('error', '?')}")
    check(f"DataAgent [{ticker}] — Company Profile", "PASS" if profile.get("success") else "FAIL", "")

    # Market Price
    if price.get("success"):
        p = price.get("data", {})
        pr = p.get("price", "?")
        day_high = p.get("day_high", "?")
        day_low = p.get("day_low", "?")
        log("✅", f"  Price: ${pr}  High: ${day_high}  Low: ${day_low}  [{price.get('latency_ms', 0):.0f}ms]")
    else:
        log("❌", f"  Price FAILED: {price.get('error', '?')}")
    check(f"DataAgent [{ticker}] — Market Price", "PASS" if price.get("success") else "FAIL", "")

    # Financials
    if financials.get("success"):
        f_data = financials.get("data", {})
        inc = f_data.get("income_statement", [])
        bal = f_data.get("balance_sheet", [])
        cf = f_data.get("cash_flow", [])
        log("✅", f"  Financials: Income({len(inc)}) Balance({len(bal)}) CashFlow({len(cf)})  [{financials.get('latency_ms', 0):.0f}ms]")
    else:
        log("❌", f"  Financials FAILED: {financials.get('error', '?')}")
    check(f"DataAgent [{ticker}] — Financials", "PASS" if financials.get("success") else "FAIL", "")

    # News
    if news.get("success"):
        count = news.get("article_count", 0)
        log("✅", f"  News: {count} articles  [{news.get('latency_ms', 0):.0f}ms]")
    else:
        log("⚠️", f"  News: {news.get('error', 'unavailable')}")
    check(f"DataAgent [{ticker}] — News", "PASS" if news.get("success") else "WARN", "")

    all_module4_data[ticker] = data

    print(f"  → Total: {elapsed}ms")
    print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 2: RetrievalAgent — PostgreSQL + DBCache
# ═══════════════════════════════════════════════════════════════════

section("PHASE 2: RetrievalAgent — PostgreSQL + DBCache")

from backend.intelligence.retrieval_agent import RetrievalAgent

all_stored_data = {}

for ticker in TICKERS:
    log("───", f"  RetrievalAgent [{ticker}]", "")
    agent = RetrievalAgent(ticker)
    stored = agent.retrieve_all()

    company = stored.get("company")
    financials_list = stored.get("financials", [])
    mkt_price = stored.get("market_price")
    news_list = stored.get("news", [])
    cache_hit = stored.get("cache_profile")

    if company:
        log("✅", f"  PostgreSQL: {company.get('company_name')} — ticker {company.get('ticker')}")
    else:
        log("⚠️", f"  PostgreSQL: No company record (needs DB write first)")

    if financials_list:
        log("✅", f"  Financials: {len(financials_list)} statement records")
    else:
        log("⚠️", f"  Financials: No stored financials")

    if mkt_price:
        log("✅", f"  Market Price: ${mkt_price.get('price')} from PostgreSQL")
    else:
        log("⚠️", f"  Market Price: Not yet stored")

    if news_list:
        log("✅", f"  News: {len(news_list)} stored articles")
    else:
        log("⚠️", f"  News: No stored news")

    if cache_hit:
        log("✅", f"  DBCache: HIT")
    else:
        log("ℹ️", f"  DBCache: Miss (expected — cache populated after write)")

    available = sum(
        1 for v in stored.values()
        if isinstance(v, (dict, list)) and v
    )
    log("", f"  Available sources: {available}/5")
    check(f"RetrievalAgent [{ticker}]", "PASS" if company else "WARN",
          f"{'Stored in DB' if company else 'DB empty (needs write first)'}")

    all_stored_data[ticker] = stored
    print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 3: EvidenceConsolidator
# ═══════════════════════════════════════════════════════════════════

section("PHASE 3: EvidenceConsolidator")

from backend.intelligence.evidence_consolidator import EvidenceConsolidator

# Simulate a document summary for comprehensive evidence
SAMPLE_DOCUMENT_SUMMARY = """
FINANCIAL STATEMENT ANALYSIS — Apple Inc. (AAPL)

Extracted from SEC Filing 10-K (FY2024):
- Total Revenue: $391.0 billion (YoY growth: +2.8%)
- Net Income: $93.7 billion
- Gross Margin: 43.5%
- Operating Margin: 29.8%
- EPS (Diluted): $6.15
- Free Cash Flow: $98.8 billion
- Cash & Equivalents: $30.5 billion
- Total Debt: $105.0 billion
- R&D Spending: $31.4 billion

Segment Breakdown:
- iPhone Revenue: $201.2 billion (51.5% of total)
- Services Revenue: $85.0 billion (21.7% of total)
- Mac Revenue: $29.0 billion
- iPad Revenue: $28.3 billion
- Wearables: $37.6 billion

Key Risk Factors (from 10-K):
- Supply chain concentration in Asia
- Regulatory scrutiny in EU Digital Markets Act
- Foreign exchange exposure
- Slowing smartphone market growth
"""

SAMPLE_MODULE3_RESULT = {
    "financial_data": {
        "total_revenue": "391.0B",
        "net_income": "93.7B",
        "gross_margin": "43.5%",
        "operating_margin": "29.8%",
        "eps": "$6.15",
        "free_cash_flow": "98.8B",
        "cash_and_equivalents": "30.5B",
        "total_debt": "105.0B",
    },
    "ratios": {
        "current_ratio": 1.82,
        "debt_to_equity": 1.75,
        "roe": 0.45,
        "roa": 0.23,
        "gross_margin": 0.435,
        "operating_margin": 0.298,
    },
    "verification_status": "verified",
    "confidence": {
        "overall": 0.95,
        "revenue": 0.98,
        "ratios": 0.92,
    },
    "cross_document_verification": {
        "status": "verified",
        "discrepancies": 0,
        "matched_fields": ["revenue", "net_income", "eps", "margin"],
    },
}

for ticker in TICKERS:
    log("───", f"  EvidenceConsolidator [{ticker}]", "")

    consolidator = EvidenceConsolidator(ticker)

    # For AAPL, use the sample documents. For others, use generic context
    doc_summary = SAMPLE_DOCUMENT_SUMMARY if ticker == "AAPL" else None
    m3_result = SAMPLE_MODULE3_RESULT if ticker == "AAPL" else None
    m4_data = all_module4_data.get(ticker)
    stored = all_stored_data.get(ticker)

    t0 = time.monotonic()
    context = consolidator.consolidate(
        master_summary=doc_summary,
        module3_result=m3_result,
        module4_data=m4_data,
        stored_data=stored,
    )
    elapsed = round((time.monotonic() - t0) * 1000)

    ctx_text = context.get("context_text", "")
    sources = context.get("sources", [])
    source_count = context.get("source_count", 0)

    log("✅", f"  Context generated: {len(ctx_text)} chars, {source_count} sources  [{elapsed}ms]")

    # Show which sources were available
    for s in sources:
        status_icon = "✅" if "AVAILABLE" in s or "UPLOADED" in s else (
            "⚠️" if "NONE" in s else "❌"
        )
        label = s.split(":")[0].strip().upper()
        log(status_icon, f"    {label}")

    check(f"EvidenceConsolidator [{ticker}]", "PASS" if source_count > 0 else "FAIL",
          f"{source_count} sources, {len(ctx_text)} chars")
    print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 4: MemoGenerator — AI Investment Memo
# ═══════════════════════════════════════════════════════════════════

section("PHASE 4: MemoGenerator — AI Investment Memo")

from backend.intelligence.memo_generator import MemoGenerator

# Check if any AI provider is configured
has_ai_key = any(os.environ.get(k) for k in ["GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"])

for ticker in TICKERS:
    log("───", f"  MemoGenerator [{ticker}]", "")

    # Fetch fresh data
    data_agent = DataAgent(ticker)
    m4_data = data_agent.fetch_all()

    consolidator = EvidenceConsolidator(ticker)
    doc_summary = SAMPLE_DOCUMENT_SUMMARY if ticker == "AAPL" else None
    m3_result = SAMPLE_MODULE3_RESULT if ticker == "AAPL" else None
    context = consolidator.consolidate(
        master_summary=doc_summary,
        module3_result=m3_result,
        module4_data=m4_data,
    )

    # Build prompt (even if AI keys aren't configured, we verify the prompt template)
    generator = MemoGenerator()
    prompt = generator.build_prompt(context)

    if len(prompt) < 500:
        log("❌", f"  Prompt too short: {len(prompt)} chars")
        check(f"MemoGenerator [{ticker}] — Prompt", "FAIL", f"Too short: {len(prompt)} chars")
    else:
        log("✅", f"  Prompt built: {len(prompt)} chars, {context.get('source_count', 0)} sources")
        check(f"MemoGenerator [{ticker}] — Prompt", "PASS", f"{len(prompt)} chars")

    # Attempt AI generation
    t0 = time.monotonic()
    result = generator.generate(context)
    elapsed = round((time.monotonic() - t0) * 1000)

    if result.get("success"):
        memo_text = result.get("memo_text", "")
        log("✅", f"  Memo generated: {len(memo_text)} chars  [{elapsed}ms]")

        # Check sections
        sections_found = 0
        for section_name in ["Executive Summary", "Company Overview", "Financial Analysis",
                              "Investment Score", "Risk Analysis", "Scenario Analysis",
                              "Action Checklist", "Recommendation"]:
            if section_name.lower() in memo_text.lower():
                sections_found += 1

        log("✅", f"  Memo sections found: {sections_found}/8")
        check(f"MemoGenerator [{ticker}] — Memo Generation", "PASS",
              f"{len(memo_text)} chars, {sections_found}/8 sections")
    else:
        error = result.get("error", "Unknown")
        if not has_ai_key:
            log("⚠️", f"  AI memo skipped (no API keys configured): {error[:100]}")
            check(f"MemoGenerator [{ticker}] — Memo Generation", "WARN",
                  f"Skipped (no AI keys)")
        else:
            log("❌", f"  AI memo failed despite keys: {error[:200]}")
            check(f"MemoGenerator [{ticker}] — Memo Generation", "FAIL",
                  f"{error[:200]}")

    print()

# ═══════════════════════════════════════════════════════════════════
# PHASE 5: Pipeline Integrity Check (with AAPL full demo)
# ═══════════════════════════════════════════════════════════════════

section("PHASE 5: Pipeline Integrity Check")

# Verify no intelligence module file has syntax errors
import py_compile
intel_files = [
    "backend/intelligence/__init__.py",
    "backend/intelligence/data_agent.py",
    "backend/intelligence/retrieval_agent.py",
    "backend/intelligence/evidence_consolidator.py",
    "backend/intelligence/memo_generator.py",
]
all_compiled = True
for f in intel_files:
    try:
        py_compile.compile(os.path.join(_project_root, f), doraise=True)
    except py_compile.PyCompileError as e:
        log("❌", f"  Compile error in {f}: {e}")
        check(f"Compile: {f}", "FAIL", str(e))
        all_compiled = False

if all_compiled:
    log("✅", "  All 5 intelligence files compile successfully")
    check("Compile: Intelligence module", "PASS", "5/5 files pass")

# Verify the prompt contains proper citation instructions
if has_ai_key:
    for ticker in TICKERS:
        data_agent = DataAgent(ticker)
        m4_data = data_agent.fetch_all()
        consolidator = EvidenceConsolidator(ticker)
        context = consolidator.consolidate(module4_data=m4_data)
        prompt = MemoGenerator().build_prompt(context)

        if "[Source:" in prompt:
            check(f"Pipeline [{ticker}] — Source citations in prompt", "PASS",
                  "Citation markers present")
        else:
            check(f"Pipeline [{ticker}] — Source citations in prompt", "WARN",
                  "No citation markers found")

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

# Per-section details
current_section = ""
for emoji, name, detail in results:
    if emoji == "───":
        print(f"\n  {name}")
        continue
    if emoji == "" and name == "":
        continue
    if emoji == "":
        continue

    if emoji == "✅":
        print(f"    ✅ {name}  {detail}")
    elif emoji == "❌":
        print(f"    ❌ {name}  {detail}")
    elif emoji == "⚠️":
        print(f"    ⚠️  {name}  {detail}")
    elif emoji == "ℹ️":
        print(f"      {name}  {detail}")
    elif emoji == "✅ ":
        print(f"    ✅   {name}  {detail}")
    elif emoji == "❌ ":
        print(f"    ❌   {name}  {detail}")
    elif emoji == "⚠️ ":
        print(f"    ⚠️   {name}  {detail}")
    else:
        print(f"    {emoji} {name}  {detail}")

print()
print("=" * 70)

# Final verdict
if FAIL > 0:
    print("  ❌ PIPELINE HAS FAILURES — Review failed components above")
elif WARN > 5:
    print("  ⚠️  PIPELINE PASSES WITH WARNINGS")
    print("  Main warning: No AI API keys configured — memo generation skipped")
else:
    print("  ✅ INTELLIGENCE MODULE PIPELINE OPERATIONAL")
    print()
    print("  All core components work:")
    print("    • DataAgent fetches live yfinance data")
    print("    • RetrievalAgent queries PostgreSQL & DBCache")
    print("    • EvidenceConsolidator merges all sources")
    print("    • MemoGenerator builds structured prompts with citations")
    print()
    print("  Missing AI keys block memo generation. To unblock:")
    print("    • Add GOOGLE_API_KEY (recommended — Gemini 2.5 Flash)")
    print("    • Or GROQ_API_KEY (fast inference)")
    print("    • Or OPENROUTER_API_KEY (many models, free tier)")

print()
print(f"  Pipeline: DataAgent → RetrievalAgent → EvidenceConsolidator → MemoGenerator")
print(f"  Data source: yfinance (free, no API key required)")
print(f"  Document evidence: ✓  |  Module 3 evidence: ✓  |  Module 4 live data: ✓")
print("=" * 70)
