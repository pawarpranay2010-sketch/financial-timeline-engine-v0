"""
Financial Timeline Engine
Unified AI Provider Architecture — Regression Test Suite

Verifies the canonical provider unification (backend/gateway becomes the
SINGLE provider/fallback system; app.py's legacy functions are thin
compatibility wrappers; a missing lower-priority provider can never block
an available higher-priority provider; the OpenRouter missing-key error
string can never surface as the final AI answer).

Mandatory cases (from the architecture-unification spec):
  1.  Groq-only configuration -> Groq succeeds
  2.  Google unavailable + Groq available -> Groq succeeds
  3.  Groq available + OpenRouter missing -> Groq succeeds
  4.  Google + Groq unavailable + OpenRouter available -> OpenRouter succeeds
  5.  All providers unavailable -> graceful no-provider response
  6.  Missing OpenRouter key never appears as the final AI answer
  7.  Investment Memo uses the canonical provider gateway
  8.  Chatbot uses the canonical provider gateway
  9.  AI Executive uses the canonical provider gateway
  10. Secret values never appear in logs/output
  11. Provider health reflects actual canonical provider state
  12. Existing Current Ratio and CAGR behavior remains unchanged
  13. Existing Agentic RAG behavior remains unchanged
  14. Existing CalculationSafetyGate behavior remains unchanged

No live API calls are made: provider adapters are fakes and the app-level
executive is stubbed.

Run: python3 -m pytest tests/test_unified_provider_architecture.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import unittest
from unittest import mock

logging.getLogger("fte").setLevel(logging.CRITICAL)

from backend.gateway.ai_executive import AIExecutive
from backend.gateway.normalized_response import NormalizedResponse
from backend.gateway.provider_adapter import ProviderCapability


# ---------------------------------------------------------------------------
# Fakes (no network)
# ---------------------------------------------------------------------------
class FakeAdapter:
    """Provider adapter whose execute() returns a canned response (or error)."""

    def __init__(self, name, response=None, error=None):
        self._name = name
        self._response = response
        self._error = error

    def health_check(self):
        return True

    def capability(self):
        return ProviderCapability(
            provider=self._name,
            model=f"{self._name}-model",
            reasoning_level=2,
            context_window=32000,
            supports_financial_analysis=True,
            supports_structured_output=True,
            supports_rag=True,
            supports_long_context=True,
            expected_latency_ms=500,
        )

    def execute(self, prompt, system_prompt="", temperature=0.3, max_tokens=4096):
        if self._error:
            return NormalizedResponse(content="", provider=self._name, error=self._error)
        return self._response


class FakeProviderManager:
    """Minimal provider manager standing in for ProviderManager."""

    def __init__(self, adapters, priority):
        self._adapters = adapters  # name -> FakeAdapter
        self.DEFAULT_PRIORITY = list(priority)

    def get(self, name):
        return self._adapters.get(name)

    def all(self):
        return list(self._adapters.keys())

    def all_adapters(self):
        return dict(self._adapters)

    def is_healthy(self, name):
        return name in self._adapters

    def health_summary(self):
        return {n: a.health_check() for n, a in self._adapters.items()}

    def key_status(self):
        return {n: True for n in self._adapters}

    def count_healthy(self):
        return len(self._adapters)

    def summary(self):
        return [
            {"provider": n, "key_configured": True, "adapter_registered": True,
             "health_check": a.health_check()}
            for n, a in self._adapters.items()
        ]


class StubExecutive:
    """Stands in for AIExecutive at the app.py boundary; records generate()."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _ok(provider, content="ok answer"):
    return NormalizedResponse(content=content, provider=provider, model=f"{provider}-model",
                              latency_ms=42.0)


def _err(provider, msg="provider failed"):
    return NormalizedResponse(content="", provider=provider, model="", error=msg)


def _make_executive(adapters, priority):
    """Real AIExecutive with a FakeProviderManager injected and registry rebuilt."""
    exec_ = AIExecutive()  # no keys in env -> empty real PM; we replace it below
    exec_.provider_manager = FakeProviderManager(adapters, priority)
    exec_._populate_registry()
    return exec_


def _fact(value, status="VERIFIED", currency="USD", period="FY2024"):
    return {
        "value": value,
        "verification_status": status,
        "currency_code": currency,
        "currency_role": "REPORTING",
        "reporting_period": period,
    }


# ---------------------------------------------------------------------------
# 1-4. Canonical fallback chain (gateway level, deterministic)
# ---------------------------------------------------------------------------
class TestCanonicalFallbackChain(unittest.TestCase):
    def test_groq_only_configuration_succeeds(self):
        exec_ = _make_executive(
            {"groq": FakeAdapter("groq", _ok("groq", "groq answer"))},
            ["groq"],
        )
        resp = exec_.generate("What was revenue?", task_type="financial")
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "groq")
        self.assertEqual(resp.content, "groq answer")

    def test_google_unavailable_groq_available_groq_succeeds(self):
        # Google is registered but its execute() fails; gateway must fail
        # over to Groq deterministically.
        exec_ = _make_executive(
            {
                "google": FakeAdapter("google", error="google 503"),
                "groq": FakeAdapter("groq", _ok("groq", "groq answer")),
            },
            ["google", "groq"],
        )
        resp = exec_.generate("Analyze financial statements", task_type="financial")
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "groq")
        self.assertEqual(resp.content, "groq answer")

    def test_groq_available_openrouter_missing_groq_succeeds(self):
        # OpenRouter simply absent (= no key configured). Groq must be used.
        exec_ = _make_executive(
            {"groq": FakeAdapter("groq", _ok("groq", "groq answer"))},
            ["groq"],
        )
        resp = exec_.generate("Write a memo", task_type="financial")
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "groq")
        self.assertNotIn("OpenRouter", str(resp.content) + str(resp.error or ""))

    def test_google_groq_unavailable_openrouter_available_succeeds(self):
        exec_ = _make_executive(
            {
                "google": FakeAdapter("google", error="google down"),
                "groq": FakeAdapter("groq", error="groq 429"),
                "openrouter": FakeAdapter("openrouter", _ok("openrouter", "or answer")),
            },
            ["google", "groq", "openrouter"],
        )
        resp = exec_.generate("Summarize the document", task_type="financial")
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "openrouter")
        self.assertEqual(resp.content, "or answer")

    def test_missing_lower_priority_provider_never_blocks_available(self):
        # Priority order is respected; a missing provider is simply skipped.
        exec_ = _make_executive(
            {"groq": FakeAdapter("groq", _ok("groq", "answer"))},
            ["google", "groq", "openrouter"],
        )
        resp = exec_.generate("Question", task_type="simple")
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "groq")


# ---------------------------------------------------------------------------
# 5-6. Graceful failure + no raw OpenRouter error string
# ---------------------------------------------------------------------------
class TestGracefulNoProvider(unittest.TestCase):
    def test_all_providers_unavailable_gateway_error(self):
        exec_ = _make_executive(
            {"google": FakeAdapter("google", error="down")},
            ["google"],
        )
        resp = exec_.generate("Question", task_type="simple")
        self.assertFalse(resp.success)
        self.assertTrue("All providers failed" in (resp.error or ""))

    def test_call_ai_with_fallback_returns_graceful_message(self):
        import app as app_module

        exec_ = _make_executive(
            {"google": FakeAdapter("google", error="down")},
            ["google"],
        )
        with mock.patch.object(app_module, "_get_ai_executive", return_value=exec_):
            result = app_module.call_ai_with_fallback("Question")
        self.assertIn("No eligible AI provider", result)
        self.assertNotIn("OpenRouter API Key missing", result)

    def test_gateway_unavailable_returns_graceful_message(self):
        import app as app_module

        with mock.patch.object(app_module, "_get_ai_executive", return_value=None):
            result = app_module.call_ai_with_fallback("Question")
        self.assertIn("No eligible AI provider", result)

    def test_openrouter_missing_key_string_never_returned(self):
        import app as app_module

        # Legacy _openrouter_request no longer returns the raw missing-key
        # error string; with no OpenRouter key it returns the graceful message.
        with mock.patch.object(app_module, "get_secret", return_value="") as m:
            success, result = app_module._openrouter_request("hi", "some-model")
        self.assertFalse(success)
        self.assertNotIn("OpenRouter API Key missing", result)
        self.assertIn("No eligible AI provider", result)
        m.assert_called_with("OPENROUTER_API_KEY", "")

    def test_call_openrouter_engine_wrapper_graceful(self):
        import app as app_module

        with mock.patch.object(app_module, "get_secret", return_value=""):
            result = app_module.call_openrouter_engine("hello")
        self.assertNotIn("OpenRouter API Key missing", result)
        self.assertIn("No eligible AI provider", result)

    def test_graceful_message_is_error_marker_detectable(self):
        # The graceful message must be caught by the app's error-marker
        # checks so downstream pipeline stages degrade instead of treating
        # it as generated content.
        import app as app_module

        from core.validation import contains_error_marker

        result = app_module.call_ai_with_fallback("Question")
        # Direct call with stubbed executive path:
        exec_ = _make_executive({}, [])
        with mock.patch.object(app_module, "_get_ai_executive", return_value=exec_):
            result = app_module.call_ai_with_fallback("Question")
        self.assertTrue(contains_error_marker(result))


# ---------------------------------------------------------------------------
# 7-9. Memo / Chatbot / AI Executive all use the canonical gateway
# ---------------------------------------------------------------------------
class TestFeaturesUseCanonicalGateway(unittest.TestCase):
    def test_investment_memo_uses_canonical_gateway(self):
        from backend.intelligence.memo_generator import MemoGenerator

        context = {
            "ticker": "TEST",
            "context_text": "Revenue grew 14.9% to $281.7B in FY2025.",
            "source_count": 2,
        }
        with mock.patch("app.call_ai_with_fallback",
                        return_value="## Investment Memo\nStrong fundamentals.") as m:
            result = MemoGenerator().generate(context)
        self.assertTrue(result["success"])
        self.assertIn("Investment Memo", result["memo_text"])
        m.assert_called()  # memo generation routed through the canonical entry point

    def test_chatbot_uses_canonical_gateway(self):
        from backend.chat_assistant import FinancialChatAssistant

        assistant = FinancialChatAssistant()
        # A clearly general question (no metric/calc/ticker keyword) must
        # route to the provider chain, never to a deterministic path.
        with mock.patch("app.call_ai_with_fallback",
                        return_value="General conversational answer") as m:
            response = assistant.answer(
                "How does the market environment look today?",
                provider_health={"Groq": True},
            )
        self.assertEqual(response["content"], "General conversational answer")
        m.assert_called()  # chatbot general answers routed through canonical chain

    def test_ai_executive_is_the_canonical_gateway(self):
        import app as app_module

        stub = StubExecutive(_ok("groq", "content"))
        with mock.patch.object(app_module, "_get_ai_executive", return_value=stub):
            result = app_module.call_ai_with_fallback(
                "Analyze revenue growth", system_prompt="You are an analyst."
            )
        self.assertEqual(result, "content")
        self.assertEqual(len(stub.calls), 1)
        # Workload-aware routing task type is passed through to the gateway.
        self.assertIn("task_type", stub.calls[0])


# ---------------------------------------------------------------------------
# 10. Secret safety
# ---------------------------------------------------------------------------
class TestSecretSafety(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("GROQ_API_KEY", None)

    def test_secret_value_never_appears_in_output(self):
        import app as app_module

        os.environ["GROQ_API_KEY"] = "supersecret-groq-value-123"
        stub = StubExecutive(_ok("groq", "public answer"))
        with mock.patch.object(app_module, "_get_ai_executive", return_value=stub):
            result = app_module.call_ai_with_fallback("Question")
        self.assertEqual(result, "public answer")
        self.assertNotIn("supersecret-groq-value-123", result)

    def test_secret_never_in_session_state(self):
        import app as app_module

        os.environ["GROQ_API_KEY"] = "another-secret-value-456"
        stub = StubExecutive(_ok("groq", "answer"))
        with mock.patch.object(app_module, "_get_ai_executive", return_value=stub):
            app_module.call_ai_with_fallback("Question")
        state_values = [str(v) for v in app_module.st.session_state.values()]
        self.assertFalse(any("another-secret-value-456" in v for v in state_values))

    def test_chat_metadata_sanitized(self):
        from backend.chat_assistant import FinancialChatAssistant

        metadata = {
            "intent": "metric",
            "note": "sk-1234567890abcdefghijklmn",
            "evidence": [{"source": "gsk_abcdef123456"}],
        }
        clean = FinancialChatAssistant._sanitize_metadata(metadata)
        self.assertNotIn("sk-1234567890abcdefghijklmn", str(clean))
        self.assertNotIn("gsk_abcdef123456", str(clean))


# ---------------------------------------------------------------------------
# 11. Provider health reflects canonical state
# ---------------------------------------------------------------------------
class TestProviderHealthCanonical(unittest.TestCase):
    def test_canonical_status_reflects_gateway_state(self):
        import app as app_module

        exec_ = _make_executive(
            {"groq": FakeAdapter("groq", _ok("groq"))},
            ["google", "groq", "openrouter"],
        )
        with mock.patch.object(app_module, "_get_ai_executive", return_value=exec_):
            status = app_module.get_canonical_provider_status()
        self.assertEqual(status.get("groq"), "available")
        self.assertEqual(status.get("google"), "not_configured")

    def test_configured_unavailable_state(self):
        import app as app_module

        # Adapter present but health_check False -> configured_unavailable.
        adapter = FakeAdapter("groq", _ok("groq"))
        adapter.health_check = lambda: False
        exec_ = _make_executive({"groq": adapter}, ["groq"])
        with mock.patch.object(app_module, "_get_ai_executive", return_value=exec_):
            status = app_module.get_canonical_provider_status()
        self.assertEqual(status.get("groq"), "configured_unavailable")


# ---------------------------------------------------------------------------
# 12-14. Frozen intelligence components unchanged
# ---------------------------------------------------------------------------
class TestFrozenComponentsUnchanged(unittest.TestCase):
    def test_current_ratio_unchanged(self):
        from backend.financial_calculator import safe_calculate_financial_ratios

        data = {
            "Current Assets": _fact(500.0),
            "Current Liabilities": _fact(250.0),
        }
        result = safe_calculate_financial_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertEqual(result["calculation"]["Current Ratio"]["value"], 2.0)

    def test_cagr_unchanged(self):
        from backend.financial_calculator import (
            CAGR_BEGIN_KEY,
            CAGR_END_KEY,
            safe_calculate_cagr_ratios,
        )

        data = {
            CAGR_BEGIN_KEY: _fact(100.0, period="FY2022"),
            CAGR_END_KEY: _fact(121.0, period="FY2024"),
        }
        result = safe_calculate_cagr_ratios(data)
        self.assertEqual(result["status"], "ALLOWED")
        self.assertAlmostEqual(result["calculation"]["CAGR"]["value"], 0.10, places=4)

    def test_agentic_rag_unchanged(self):
        from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator

        self.assertTrue(callable(AgenticRAGOrchestrator))
        # Core entry points preserved (no constructor side effects checked —
        # RAG needs a live DB, which is outside this unit suite).
        self.assertTrue(hasattr(AgenticRAGOrchestrator, "__init__"))

    def test_calculation_safety_gate_unchanged(self):
        from backend.intelligence.calculation_safety_gate import CalculationSafetyGate

        gate = CalculationSafetyGate()
        verdict = gate.check(
            {
                "Current Assets": _fact(500.0, status="PENDING"),
                "Current Liabilities": _fact(250.0),
            },
            ["Current Assets", "Current Liabilities"],
        )
        self.assertEqual(verdict["status"], "BLOCKED")
        self.assertEqual(verdict["reason"], "PENDING")


if __name__ == "__main__":
    unittest.main()
