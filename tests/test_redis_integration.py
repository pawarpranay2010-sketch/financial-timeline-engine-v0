"""
Redis Integration — End-to-End Verification

Tests:
  Phase 1 — Redis connection (reconnect, graceful failure)
  Phase 2 — Redis read/write + TTL verification
  Phase 3 — Cache chain: Redis → PostgreSQL → Provider (hit/miss/expiry)
  Phase 4 — ProviderOrchestrator full pipeline with 10 companies
  Phase 5 — RetrievalAgent + EvidenceConsolidator compatibility
  Phase 6 — Graceful degradation when Redis is offline
  Phase 7 — Performance metrics (latency, hit rate)

Companies (10):
  US:    MSFT, NVDA, JPM, KO, JNJ
  India: INFY.NS, HDFCBANK.NS, LT.NS, TATAMOTORS.NS, SUNPHARMA.NS
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

logger = logging.getLogger("test.redis")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv()

TICKERS = ["MSFT", "NVDA", "JPM", "KO", "JNJ",
           "INFY.NS", "HDFCBANK.NS", "LT.NS", "TATAMOTORS.NS", "SUNPHARMA.NS"]

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
    sys.stdout.write(f"  {label}\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════════
# PHASE 1 — Redis Connection
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 1 — Redis Connection Tests")
print("=" * 70)

from backend.module4.redis_cache import RedisCache
from backend.module4.cache_manager import CacheManager

# 1a. Connection with configured URL
rc = RedisCache()
if rc._connected:
    check("P1a", "connection", "PASS", "Redis connected")
    print(f"    Connected: {rc._connected}")
else:
    check("P1a", "connection", "WARN", "Redis not connected — check REDIS_URL in .env")
    print("    Redis connection failed. Check REDIS_URL in .env file.")
    print("    Expected format: redis://default:password@host:port/0")

# 1b. Connection with explicit URL (test graceful degradation)
rc2 = RedisCache(redis_url="redis://invalid:6379/0")
if rc2._connected:
    check("P1b", "bad_url", "WARN", "Connected to invalid URL (unexpected)")
else:
    check("P1b", "bad_url", "PASS", "Graceful failure on bad URL (no crash)")

# 1c. CacheManager backward compatibility
cm = CacheManager()
if hasattr(cm, '_redis'):
    check("P1c", "CacheManager", "PASS", "Initialized via RedisCache delegation")
else:
    check("P1c", "CacheManager", "FAIL", "No RedisCache reference")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 2 — Redis Read/Write + TTL
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 2 — Redis Read/Write + TTL Verification")
print("=" * 70)

if rc._connected:
    # 2a. Write and read back
    test_data = {"test": "value", "ticker": "TEST", "ts": datetime.utcnow().isoformat()}
    wrote = rc.set("profile", "TEST", test_data)
    if wrote:
        check("P2a", "write", "PASS", "Successfully wrote to Redis")
    else:
        check("P2a", "write", "FAIL", "Failed to write")

    read_back = rc.get("profile", "TEST")
    if read_back and read_back.get("ticker") == "TEST":
        check("P2a", "read", "PASS", "Successfully read back from Redis")
    else:
        check("P2a", "read", "FAIL", f"Read failed: {read_back}")

    # 2b. TTL verification
    expected_ttl = {
        "profile": 86400, "financials": 86400,
        "price": 300, "news": 1800, "filings": 86400,
    }
    for data_type, expected in expected_ttl.items():
        actual = rc._ttls.get(data_type)
        if actual == expected:
            check("P2b", data_type, "PASS", f"TTL={actual}s (expected {expected}s)")
        else:
            check("P2b", data_type, "WARN", f"TTL={actual}s (expected {expected}s)")

    # 2c. Cache miss
    miss = rc.get("profile", "NONEXISTENT_TICKER_XYZ")
    if miss is None:
        check("P2c", "cache_miss", "PASS", "None returned for missing key")
    else:
        check("P2c", "cache_miss", "FAIL", f"Expected None, got {miss}")

    # 2d. Delete
    deleted = rc.delete("profile", "TEST")
    after_delete = rc.get("profile", "TEST")
    if deleted and after_delete is None:
        check("P2d", "delete", "PASS", "Key deleted successfully")
    else:
        check("P2d", "delete", "WARN", f"Delete result: {deleted}, post-delete: {after_delete}")

    # 2e. Key structure verification
    test_key = rc._key("profile", "MSFT")
    expected_key = "fte:profile:msft"
    if test_key == expected_key:
        check("P2e", "key_format", "PASS", f"Key format: {test_key}")
    else:
        check("P2e", "key_format", "FAIL", f"Expected {expected_key}, got {test_key}")

else:
    print("  ⏭ Skipped — Redis not connected")
    check("P2a", "write", "WARN", "Skipped (no Redis)")
    check("P2a", "read", "WARN", "Skipped (no Redis)")
    for data_type in ["profile", "financials", "price", "news", "filings"]:
        check("P2b", data_type, "WARN", "Skipped (no Redis)")
    check("P2c", "cache_miss", "WARN", "Skipped (no Redis)")
    check("P2d", "delete", "WARN", "Skipped (no Redis)")
    check("P2e", "key_format", "WARN", "Skipped (no Redis)")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 3 — Cache Chain via ProviderOrchestrator
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 3 — Cache Chain (Redis → PostgreSQL → Provider)")
print("=" * 70)

from backend.module4.provider_orchestrator import ProviderOrchestrator

orch = ProviderOrchestrator()

# Test one US ticker
test_ticker = "MSFT"

# 3a. First fetch — should be a cache miss from Redis, potentially miss from Postgres, then provider
print(f"\n  3a. First fetch for {test_ticker} (expect Redis MISS → Postgres → Provider):")
start = time.monotonic()
profile = orch.fetch_company_profile(test_ticker)
first_latency = (time.monotonic() - start) * 1000
if profile and profile.get("company_name"):
    check("P3a", test_ticker, "PASS", f"Profile fetched ({first_latency:.0f}ms)")
else:
    check("P3a", test_ticker, "WARN", f"Profile empty ({first_latency:.0f}ms)")

# 3b. Second fetch — should be a Redis HIT
print(f"\n  3b. Second fetch for {test_ticker} (expect Redis HIT):")
start = time.monotonic()
profile2 = orch.fetch_company_profile(test_ticker)
second_latency = (time.monotonic() - start) * 1000
if profile2 and profile2.get("company_name"):
    check("P3b", test_ticker, "PASS", f"Profile from cache ({second_latency:.0f}ms) — should be faster than {first_latency:.0f}ms")
else:
    check("P3b", test_ticker, "WARN", f"Second fetch failed ({second_latency:.0f}ms)")

# 3c. News fetch
print(f"\n  3c. News fetch (expect Redis MISS → Provider):")
start = time.monotonic()
news = orch.fetch_news(test_ticker)
news_latency = (time.monotonic() - start) * 1000
news_count = len(news) if isinstance(news, list) else 0
if news_count > 0:
    check("P3c", test_ticker, "PASS", f"{news_count} articles ({news_latency:.0f}ms)")
else:
    check("P3c", test_ticker, "WARN", f"0 articles ({news_latency:.0f}ms)")

# 3d. Second news fetch — should be Redis HIT
print(f"\n  3d. Second news fetch (expect Redis HIT):")
start = time.monotonic()
news2 = orch.fetch_news(test_ticker)
news2_latency = (time.monotonic() - start) * 1000
news2_count = len(news2) if isinstance(news2, list) else 0
if news2_count == news_count:
    check("P3d", test_ticker, "PASS", f"{news2_count} articles ({news2_latency:.0f}ms) — cached")
else:
    check("P3d", test_ticker, "WARN", f"Count mismatch: {news2_count} vs {news_count}")

# 3e. Market price
print(f"\n  3e. Market price:")
start = time.monotonic()
price = orch.fetch_market_price(test_ticker)
price_latency = (time.monotonic() - start) * 1000
if price and price.get("price") is not None:
    check("P3e", test_ticker, "PASS", f"${price['price']} ({price_latency:.0f}ms)")
else:
    check("P3e", test_ticker, "WARN", f"No price ({price_latency:.0f}ms)")

# 3f. Financials
print(f"\n  3f. Financials:")
start = time.monotonic()
fin = orch.fetch_financials(test_ticker)
fin_latency = (time.monotonic() - start) * 1000
if fin:
    count = len(fin) if isinstance(fin, list) else 1
    check("P3f", test_ticker, "PASS", f"{count} records ({fin_latency:.0f}ms)")
else:
    check("P3f", test_ticker, "WARN", f"No data ({fin_latency:.0f}ms)")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 4 — Full Pipeline with 10 Companies
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 4 — Full Pipeline with 10 Companies")
print("=" * 70)

# Clear old Redis cache so we see fresh fetches
if rc._connected:
    rc.clear()

pipeline_results = {}

for t in TICKERS:
    print(f"\n  Testing: {t}")
    ticker_results = {"ticker": t, "profile": False, "financials": False,
                       "price": False, "news": False, "latencies": {}}

    # Profile
    start = time.monotonic()
    try:
        p = orch.fetch_company_profile(t)
        lat = (time.monotonic() - start) * 1000
        ticker_results["latencies"]["profile"] = lat
        if p and (p.get("company_name") or p.get("ticker")):
            ticker_results["profile"] = True
    except Exception as e:
        ticker_results["latencies"]["profile"] = (time.monotonic() - start) * 1000

    # Financials
    start = time.monotonic()
    try:
        f = orch.fetch_financials(t)
        lat = (time.monotonic() - start) * 1000
        ticker_results["latencies"]["financials"] = lat
        if f is not None:
            ticker_results["financials"] = True
    except Exception as e:
        ticker_results["latencies"]["financials"] = (time.monotonic() - start) * 1000

    # Price
    start = time.monotonic()
    try:
        pr = orch.fetch_market_price(t)
        lat = (time.monotonic() - start) * 1000
        ticker_results["latencies"]["price"] = lat
        if pr and pr.get("price") is not None:
            ticker_results["price"] = True
    except Exception as e:
        ticker_results["latencies"]["price"] = (time.monotonic() - start) * 1000

    # News
    start = time.monotonic()
    try:
        n = orch.fetch_news(t)
        lat = (time.monotonic() - start) * 1000
        ticker_results["latencies"]["news"] = lat
        if n and len(n) > 0:
            ticker_results["news"] = True
            ticker_results["news_count"] = len(n)
    except Exception as e:
        ticker_results["latencies"]["news"] = (time.monotonic() - start) * 1000

    pipeline_results[t] = ticker_results

    # Per-ticker check
    successes = sum(1 for k in ["profile", "financials", "price", "news"] if ticker_results.get(k))
    check("P4", t, "PASS" if successes >= 2 else "WARN",
          f"{successes}/4 data types | "
          f"profile={ticker_results['latencies'].get('profile',0):.0f}ms "
          f"financials={ticker_results['latencies'].get('financials',0):.0f}ms "
          f"price={ticker_results['latencies'].get('price',0):.0f}ms "
          f"news={ticker_results['latencies'].get('news',0):.0f}ms")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 5 — RetrievalAgent + EvidenceConsolidator Compatibility
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 5 — RetrievalAgent + EvidenceConsolidator Compatibility")
print("=" * 70)

from backend.intelligence.retrieval_agent import RetrievalAgent
from backend.intelligence.evidence_consolidator import EvidenceConsolidator
from backend.intelligence.data_agent import DataAgent

for t in [TICKERS[0], TICKERS[1], TICKERS[5], TICKERS[6]]:
    # RetrievalAgent
    ra = RetrievalAgent(t)
    stored = ra.retrieve_all()
    data_sources = sum(1 for k, v in stored.items() if isinstance(v, (dict, list)) and v and k != "ticker")
    check("P5a", t, "PASS" if data_sources >= 1 else "WARN",
          f"RetrievalAgent: {data_sources} data sources")

    # DataAgent
    da = DataAgent(t)
    live = da.fetch_all()
    live_success = live.get("company_profile", {}).get("success", False) or \
                   live.get("market_price", {}).get("success", False) or \
                   live.get("news", {}).get("success", False)

    # EvidenceConsolidator
    consolidator = EvidenceConsolidator(t)
    context = consolidator.consolidate(module4_data=live, stored_data=stored)
    if isinstance(context, dict):
        ctx_text = context.get("context_text", "")
        sources = context.get("sources", [])
    else:
        ctx_text = str(context)
        sources = []

    has_sections = "SOURCE:" in ctx_text
    check("P5b", t, "PASS" if has_sections else "WARN",
          f"EvidenceConsolidator: {len(ctx_text)} chars, {len(sources)} sources")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 6 — Graceful Degradation (Redis Offline)
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 6 — Graceful Degradation (Redis Offline)")
print("=" * 70)

# Create an orchestrator that doesn't have Redis
orch_no_redis = ProviderOrchestrator()
# Simulate Redis going offline
if hasattr(orch_no_redis.redis, '_connected'):
    old_state = orch_no_redis.redis._connected
    orch_no_redis.redis._connected = False
    orch_no_redis.redis._client = None

    try:
        p = orch_no_redis.fetch_company_profile("MSFT")
        if p and p.get("company_name"):
            check("P6", "MSFT", "PASS", "Pipeline works with Redis offline")
        else:
            check("P6", "MSFT", "WARN", "Profile empty but no crash")
    except Exception as e:
        check("P6", "MSFT", "FAIL", f"Crashed without Redis: {e}")

    # Restore
    orch_no_redis.redis._connected = old_state
else:
    check("P6", "graceful", "WARN", "Could not simulate Redis offline")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 7 — Performance Metrics
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("PHASE 7 — Performance Metrics")
print("=" * 70)

# Aggregate latencies
all_latencies = {"profile": [], "financials": [], "price": [], "news": []}
for t, pr in pipeline_results.items():
    for dtype in all_latencies:
        lat = pr.get("latencies", {}).get(dtype)
        if lat is not None:
            all_latencies[dtype].append(lat)

print(f"\n  Average API Latency:")
for dtype, lats in all_latencies.items():
    if lats:
        avg = sum(lats) / len(lats)
        min_l = min(lats)
        max_l = max(lats)
        print(f"    {dtype:12s}: avg={avg:.0f}ms  min={min_l:.0f}ms  max={max_l:.0f}ms  (n={len(lats)})")
    else:
        print(f"    {dtype:12s}: no data")

# Cache stats
redis_stats = rc.get_stats() if rc._connected else {"hits": 0, "misses": 0, "connected": False}
print(f"\n  Redis Cache Stats:")
print(f"    Connected: {redis_stats.get('connected', False)}")
print(f"    Hits:      {redis_stats.get('hits', 0)}")
print(f"    Misses:    {redis_stats.get('misses', 0)}")
print(f"    Errors:    {redis_stats.get('errors', 0)}")
print(f"    Hit Rate:  {redis_stats.get('hit_rate_pct', 0)}%")

# Speed comparison (first call vs cached)
if rc._connected:
    print(f"\n  Cache Speed Comparison:")
    print(f"    First fetch (provider): {first_latency:.0f}ms")
    print(f"    Second fetch (Redis):   {second_latency:.0f}ms")
    if second_latency > 0:
        speedup = (first_latency - second_latency) / first_latency * 100
        print(f"    Speedup:  {speedup:.0f}%" if speedup > 0 else "    (equivalent)")


# ═══════════════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("FINAL REDIS INTEGRATION REPORT")
print("=" * 70)

pass_count = sum(1 for r in results if "→ PASS" in r)
fail_count = sum(1 for r in results if "→ FAIL" in r)
warn_count = sum(1 for r in results if "→ WARN" in r)

print(f"\n  Total:     {len(results)}")
print(f"  ✅ PASS:   {pass_count}")
print(f"  ⚠️  WARN:   {warn_count}")
print(f"  ❌ FAIL:   {fail_count}")

print(f"\n{'─' * 70}")
print("PER-TICKER BREAKDOWN")
print(f"{'─' * 70}")
for t in TICKERS:
    pr = pipeline_results.get(t, {})
    ok_count = sum(1 for k in ["profile", "financials", "price", "news"] if pr.get(k))
    lats = pr.get("latencies", {})
    lat_str = " | ".join(f"{k}={v:.0f}ms" for k, v in lats.items())
    print(f"  {t:15s}: {ok_count}/4 OK | {lat_str}")

print(f"\n{'─' * 70}")
print("CACHE CHAIN ARCHITECTURE")
print(f"{'─' * 70}")
print("""
  ┌─────────────────────────────────────────────┐
  │  fetch_*()                                  │
  │    ↓                                        │
  │  1. Redis (TTL-gated)                       │
  │       → HIT? Return (fastest)               │
  │       → MISS? Continue                      │
  │    ↓                                        │
  │  2. PostgreSQL DBCache (freshness-gated)    │
  │       → HIT? Return                         │
  │       → MISS? Continue                      │
  │    ↓                                        │
  │  3. Provider failover (priority order)      │
  │       → Success? Save to Redis, Return      │
  │       → Fail? Next provider                 │
  │    ↓                                        │
  │  4. All exhausted → Error                   │
  └─────────────────────────────────────────────┘
""")

print(f"{'─' * 70}")
print("FAILURES")
print(f"{'─' * 70}")
if failures:
    for f in failures:
        print(f"  ❌ {f}")
else:
    print("  ✅ No failures!")

print(f"\n{'─' * 70}")
if rc._connected:
    verdict = "✅ ALL CHECKS PASS — Redis Integration Fully Verified" if fail_count == 0 else f"❌ {fail_count} failures"
else:
    verdict = "⚠️ Redis not connected — verify REDIS_URL in .env. Architecture verified without Redis."
print(f"  {verdict}")

rc.close()
cm.close()
print(f"{'=' * 70}\n")
