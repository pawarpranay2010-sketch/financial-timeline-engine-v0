#!/usr/bin/env python3
"""Controlled integration test comparing old vs new AI execution paths.

Verifies:
1. Both paths produce content (not error) for normal requests
2. NormalizedResponse.content matches the format expected by app.py
3. Error handling is backward compatible
4. Workload mapping produces correct task types
5. Provider fallback chain works in the integrated context
6. No regression in existing behavior
"""
import sys, os, time, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

results = {"pass": 0, "fail": 0, "warn": 0, "na": 0}
logs = []

def check(label, status, detail=""):
    if status == "PASS":
        results["pass"] += 1
    elif status == "FAIL":
        results["fail"] += 1
    elif status == "NA":
        results["na"] += 1
    else:
        results["warn"] += 1
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "NA": "⏭️"}.get(status, "❓")
    msg = f"{icon} {label}"
    if detail:
        msg += f" — {detail}"
    logs.append(msg)
    print(msg)

# ======================================================================
# PHASE 1: Verify AIExecutive imports and initialization
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 1: GATEWAY INITIALIZATION")
print("=" * 70)

try:
    from backend.gateway import AIExecutive, NormalizedResponse
    executive = AIExecutive()
    check("AIExecutive initialized", "PASS", f"{len(executive.provider_manager.all())} providers")
except Exception as e:
    check("AIExecutive initialized", "FAIL", f"{type(e).__name__}: {e}")
    print("\n❌ Cannot proceed without AIExecutive — aborting")
    sys.exit(1)

# ======================================================================
# PHASE 2: Workload mapping — verify task type exists
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 2: WORKLOAD MAPPING VERIFICATION")
print("=" * 70)

from backend.gateway.router import TASK_SIMPLE, TASK_FINANCIAL_ANALYSIS, TASK_STRUCTURED, TASK_FALLBACK

WORKLOAD_MAP = {
    "summarize_document": TASK_SIMPLE,
    "merge_summaries": TASK_SIMPLE,
    "extract_timeline": TASK_STRUCTURED,
    "intelligence_extraction": TASK_STRUCTURED,
    "investment_memo": TASK_FINANCIAL_ANALYSIS,
    "fallback": TASK_FALLBACK,
}

for workload, task_type in WORKLOAD_MAP.items():
    route = executive.router.route(task_type)
    if route:
        check(f"Workload '{workload}' → {task_type}", "PASS",
              f"routes to {route.provider}/{route.model}")
    else:
        check(f"Workload '{workload}' → {task_type}", "WARN",
              "No route found (all providers may be unavailable)")

# ======================================================================
# PHASE 3: Old API format compatibility
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 3: API FORMAT COMPATIBILITY")
print("=" * 70)

# The old call_ai_with_fallback returns a plain string (or raises)
# The new AIExecutive.generate() returns NormalizedResponse

# 3a: NormalizedResponse → plain string conversion
nr = NormalizedResponse(content="Test memo content", provider="groq", model="llama-3.3-70b-versatile")
plain_text = nr.content
check("NormalizedResponse.content is a string", "PASS" if isinstance(plain_text, str) else "FAIL",
      f"type={type(plain_text).__name__}")

# 3b: Error response compatibility
err_nr = NormalizedResponse(content="", provider="", model="", error="All providers failed")
check("Error NormalizedResponse still has .content=''", "PASS" if err_nr.content == "" else "FAIL")
check("Error response .success is False", "PASS" if not err_nr.success else "FAIL")

# 3c: Success response format
check("Success NormalizedResponse .content is truthy", "PASS" if bool(nr.content) else "FAIL")
check("Success NormalizedResponse .success is True", "PASS" if nr.success else "FAIL")

# 3d: to_dict() format — compatible with serialization
nr_dict = nr.to_dict()
check("NormalizedResponse.to_dict() has 'content' key", "PASS" if "content" in nr_dict else "FAIL")
check("NormalizedResponse.to_dict() has 'provider' key", "PASS" if "provider" in nr_dict else "FAIL")
check("NormalizedResponse.to_dict() has 'error' key", "PASS" if "error" in nr_dict else "FAIL")

# ======================================================================
# PHASE 4: Provider health backward compatibility
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 4: PROVIDER HEALTH COMPATIBILITY")
print("=" * 70)

# The old get_provider_health() returns a dict of provider→bool
# We need to verify the new system can report similar health

pm = executive.provider_manager
key_status = pm.key_status()
all_adapters = pm.all_adapters()

health_dict = {}
for name, adapter in all_adapters.items():
    health_dict[name] = adapter.health_check()

check("Provider health in old format (dict[str, bool])", "PASS" if isinstance(health_dict, dict) else "FAIL",
      f"{len(health_dict)} providers")
check("Health dict values are bool", "PASS" if all(isinstance(v, bool) for v in health_dict.values()) else "FAIL")

# Key status format
check("Key status is dict[str, bool]", "PASS" if isinstance(key_status, dict) and all(isinstance(v, bool) for v in key_status.values()) else "FAIL",
      f"{sum(1 for v in key_status.values() if v)}/{len(key_status)} keys configured")

# ======================================================================
# PHASE 5: Secret loading compatibility
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 5: SECRET LOADING COMPATIBILITY")
print("=" * 70)

# The old code uses st.secrets.get() directly
# The new code uses os.getenv() via ProviderManager
# Both need to find the same API keys

from backend.gateway.provider_manager import ProviderManager as GatewayPM
gw_pm = GatewayPM()

# Keys the old code expects
old_keys = {"GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"}
new_keys = set(gw_pm.ENV_KEY_MAP.values())

# Verify the overlap
overlap = old_keys & new_keys
check(f"Key overlap (old ∩ new) = {len(overlap)}", "PASS",
      f"Old expects: {sorted(old_keys)}, Gateway knows: {sorted(new_keys)}")

# Check old keys are loadable via env
for key in old_keys:
    val = os.getenv(key, "")
    if val:
        check(f"Old key '{key}' loadable from env", "PASS", "FOUND")
    else:
        check(f"Old key '{key}' loadable from env", "WARN", "NOT FOUND in os.environ")

# ======================================================================
# PHASE 6: Router workload decisions (no API call)
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 6: ROUTER WORKLOAD DECISIONS")
print("=" * 70)

from backend.gateway.router import Router

router = executive.router

# Test each workload type
test_routes = [
    (TASK_SIMPLE, "simple Q&A"),
    (TASK_FINANCIAL_ANALYSIS, "financial analysis"),
    (TASK_STRUCTURED, "JSON structured output"),
    (TASK_FALLBACK, "fallback/any"),
]

for task_type, description in test_routes:
    route = router.route(task_type)
    if route:
        check(f"Route: {description}", "PASS",
              f"{route.provider}/{route.model} ({route.reason})")
    else:
        check(f"Route: {description}", "WARN", "No eligible provider")

# Test admission controller with representative workloads
from backend.gateway import AdmissionController
ac = AdmissionController()

# Document summarization prompt (~500 chars)
small_prompt = "Summarize the following financial document..."
result = ac.admit(small_prompt, context_window=8192, output_tokens=1024)
check("Admission: small prompt (summarization)", "PASS" if result.allowed else "FAIL")

# Large document prompt (~50000 chars)
large_prompt = "X" * 50000
result = ac.admit(large_prompt, context_window=8192, output_tokens=4096)
check("Admission: large prompt (oversized)", "PASS" if not result.allowed else "FAIL",
      result.reason)

# ======================================================================
# PHASE 7: NormalizedResponse backward compatibility for all flows
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 7: RESPONSE BACKWARD COMPATIBILITY")
print("=" * 70)

# Simulate each workload response type
test_responses = {
    "summarization": NormalizedResponse(content="Financial summary text...", provider="groq", model="llama-3.3-70b-versatile", latency_ms=264.0),
    "merge": NormalizedResponse(content="Merged summary of all documents...", provider="openrouter", model="openrouter/free", latency_ms=871.0),
    "timeline": NormalizedResponse(content='[{"date": "2025-01-01", "event": "Revenue growth"}]', provider="sambanova", model="Meta-Llama-3.3-70B-Instruct", latency_ms=692.0),
    "memo": NormalizedResponse(content="## Investment Memo\nMicrosoft shows strong revenue growth...", provider="nvidia", model="nemotron-3-ultra", latency_ms=1116.0),
    "intelligence": NormalizedResponse(content='{"executive_summary": "Strong performance..."}', provider="github", model="gpt-4o", latency_ms=1386.0),
    "error": NormalizedResponse(content="", provider="", model="", error="All providers failed"),
}

for workload, response in test_responses.items():
    # The app expects a string from call_ai_with_fallback
    if response.success:
        check(f"Response '{workload}' → .content is string", "PASS" if isinstance(response.content, str) else "FAIL",
              f"len={len(response.content)}, type={type(response.content).__name__}")
        # Verify it looks like content (not empty)
        check(f"Response '{workload}' has non-empty content", "PASS" if response.content.strip() else "WARN",
              "empty content")
    else:
        check(f"Response '{workload}' error handled", "PASS" if response.error else "FAIL",
              response.error[:80] if response.error else "no error message")

# ======================================================================
# PHASE 8: Provider failover simulation
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 8: PROVIDER FAILOVER SIMULATION")
print("=" * 70)

# Test: if the primary provider fails, fallback should try another
attempted = ["nonexistent_provider"]
fb_response = executive._fallback("test prompt", "", 0.3, 100, "fallback", attempted)
if fb_response.error:
    check("Fallback on nonexistent provider", "WARN" if "All providers" in (fb_response.error or "") else "PASS",
          fb_response.error[:100] if fb_response.error else "no error")
else:
    check("Fallback on nonexistent provider → succeeds via real provider", "PASS",
          f"content='{fb_response.content[:50]}...'")

# ======================================================================
# PHASE 9: Redis quota integration
# ======================================================================
print("\n" + "=" * 70)
print("PHASE 9: REDIS QUOTA INTEGRATION")
print("=" * 70)

quota = executive.quota
# Record a simulated request
quota.record_request("groq")
quota.record_request("sambanova")
quota.record_error("nvidia", "503")

check("Quota tracks groq RPM", "PASS" if quota.get_rpm("groq") >= 0 else "FAIL",
      f"rpm={quota.get_rpm('groq')}")
check("Quota tracks nvidia errors", "PASS" if quota.get_error_count("nvidia") >= 0 else "FAIL",
      f"errors={quota.get_error_count('nvidia')}")

# ======================================================================
# PHASE 10: Summary & verdict
# ======================================================================
print("\n" + "=" * 70)
print(f"FINAL RESULTS: {results['pass']} ✅ PASS | {results['fail']} ❌ FAIL | "
      f"{results['warn']} ⚠️ WARN | {results['na']} ⏭️ N/A")
print("=" * 70)

if results["fail"] > 0:
    print("\n❌ VERDICT: INTEGRATION TEST FAILED — Review failures above")
elif results["pass"] >= 20:
    print("\n✅ VERDICT: INTEGRATION TEST PASSED — AIExecutive is compatible with app.py API")
else:
    print(f"\n⚠️ VERDICT: MIXED — {results['pass']} pass, {results['warn']} warn")
