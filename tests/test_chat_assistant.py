"""
Platrixa
AI Financial Assistant (interactive chatbot) — Regression Tests

Verifies the conversational layer built on top of the frozen
intelligence stack (Agentic RAG + verified facts + CalculationSafetyGate
+ provider chain). The chatbot module imports no Streamlit, so it is
fully unit-testable here.

Coverage:
  - Intent detection (metric / calculation / ticker / greeting)
  - _shape_fact carries the canonical metric id (regression for the
    bug where metric lookups silently returned empty)
  - Document-backed metric answers with provenance / evidence
  - Gated calculations: ROE, Current Ratio, CAGR, change — never
    bypassing CalculationSafetyGate
  - Missing-data refusal (BLOCKED / no fabrication)
  - Follow-up resolution ('it', 'that company', 'previous year')
  - Bounded conversation context
  - Company questions via Agentic RAG runner stub
  - Graceful no-provider behavior
  - Secret safety: no API-key patterns in answers/metadata

Run: python3 -m pytest tests/test_chat_assistant.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import unittest

logging.getLogger("fte").setLevel(logging.CRITICAL)

from backend.chat_assistant import (
    ChatContext,
    FinancialChatAssistant,
    detect_metric,
    detect_calculation,
    detect_ticker,
    _shape_fact,
    _collect_facts,
    _facts_for_metric,
)


# ---------------------------------------------------------------------------
# Fixtures — facts shaped like the FinancialExtractorV2 output
# ---------------------------------------------------------------------------

def make_fact(
    metric,
    value,
    period="FY2024",
    source="SEC XBRL",
    tier=3,
    scale="10^6",
    currency="USD",
    source_type="XBRL",
):
    return {
        "metric_name": metric,
        "metric_id": metric,
        "metric_value": value,
        "raw_value": str(value),
        "normalized_value": value,
        "unit": "USD",
        "scale": scale,
        "currency_code": currency,
        "currency_role": "REPORTING",
        "fiscal_period": period,
        "reporting_period": period,
        "source": source,
        "source_type": source_type,
        "source_tier": tier,
    }


def make_doc(facts, name="Apple FY2024 10-K"):
    return {"file_name": name, "financial_facts": facts}


def stub_llm(prompt, system_prompt=None):
    return "This is a stubbed conversational answer."


def no_provider_health():
    return {
        "Google AI Studio": False,
        "Groq": False,
        "OpenRouter": False,
        "NVIDIA": False,
        "RapidAPI": False,
        "SambaNova": False,
        "GitHub Models": False,
        "Cerebras": False,
        "Cohere": False,
    }


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestIntentDetection(unittest.TestCase):

    def test_detect_metric_revenue(self):
        self.assertEqual(detect_metric("What was the revenue?"), "Revenue")
        self.assertEqual(detect_metric("net sales for FY2024"), "Revenue")

    def test_detect_metric_net_income(self):
        self.assertEqual(detect_metric("What was net income?"), "NetIncome")

    def test_detect_metric_eps(self):
        self.assertEqual(detect_metric("earnings per share?"), "EPS")

    def test_detect_calculation_roe(self):
        self.assertEqual(detect_calculation("What was ROE?"), "ROE")

    def test_detect_calculation_current_ratio(self):
        self.assertEqual(detect_calculation("What is the current ratio?"), "Current Ratio")

    def test_detect_calculation_cagr(self):
        self.assertEqual(detect_calculation("CAGR from FY2023 to FY2024"), "CAGR")

    def test_detect_calculation_change(self):
        self.assertEqual(detect_calculation("How much did it grow?"), "change")

    def test_detect_ticker(self):
        self.assertEqual(detect_ticker("Analyze AAPL revenue"), "AAPL")
        self.assertEqual(detect_ticker("apple financials"), "AAPL")
        self.assertEqual(detect_ticker("tell me about the market"), None)


# ---------------------------------------------------------------------------
# Fact shaping (regression: metric key must survive shaping)
# ---------------------------------------------------------------------------

class TestFactShaping(unittest.TestCase):

    def test_shape_fact_carries_metric(self):
        shaped = _shape_fact(make_fact("Revenue", 391035000000.0))
        self.assertEqual(shaped.get("metric"), "Revenue")

    def test_shape_fact_verified_with_provenance(self):
        shaped = _shape_fact(make_fact("Revenue", 391035000000.0))
        self.assertEqual(shaped.get("verification_status"), "VERIFIED")
        self.assertEqual(shaped.get("reporting_period"), "FY2024")
        self.assertEqual(shaped.get("source"), "SEC XBRL")
        self.assertEqual(shaped.get("source_tier"), 3)

    def test_shape_fact_pending_without_provenance(self):
        fact = make_fact("Revenue", 391035000000.0)
        fact["source"] = ""
        fact["fiscal_period"] = ""
        shaped = _shape_fact(fact)
        self.assertEqual(shaped.get("verification_status"), "PENDING")

    def test_collect_facts_tags_documents(self):
        facts = [make_fact("Revenue", 391035000000.0)]
        docs = [make_doc(facts, name="Apple 10-K")]
        collected = _collect_facts(docs)
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["document"], "Apple 10-K")

    def test_facts_for_metric_filters(self):
        facts = [
            _shape_fact(make_fact("Revenue", 391035000000.0)),
            _shape_fact(make_fact("NetIncome", 96995000000.0)),
        ]
        self.assertEqual(len(_facts_for_metric(facts, "Revenue")), 1)
        self.assertEqual(len(_facts_for_metric(facts, "NetIncome")), 1)
        self.assertEqual(len(_facts_for_metric(facts, "EPS")), 0)


# ---------------------------------------------------------------------------
# Document-backed metric Q&A with provenance
# ---------------------------------------------------------------------------

class TestDocumentQnA(unittest.TestCase):

    def test_metric_answer_with_evidence(self):
        facts = [make_fact("Revenue", 391035000000.0, period="FY2024")]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was the revenue?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "metric")
        self.assertIn("Revenue", result["content"])
        self.assertIn("391,035", result["content"])  # millions display
        evidence = result["metadata"]["evidence"]
        self.assertTrue(evidence)
        top = evidence[0]
        self.assertEqual(top["source"], "SEC XBRL")
        self.assertEqual(top["period"], "FY2024")
        self.assertEqual(top["source_tier"], 3)

    def test_metric_answer_picks_latest_period(self):
        facts = [
            make_fact("Revenue", 383285000000.0, period="FY2023"),
            make_fact("Revenue", 391035000000.0, period="FY2024"),
        ]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was revenue?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertIn("FY2024", result["content"])
        # Follow-up topic should carry the metric + periods
        topic = result["metadata"]["topic"]
        self.assertEqual(topic["metric"], "Revenue")
        self.assertIn("FY2024", topic["periods"])


# ---------------------------------------------------------------------------
# Gated calculations (never bypassing CalculationSafetyGate)
# ---------------------------------------------------------------------------

class TestGatedCalculations(unittest.TestCase):

    def test_roe_valid(self):
        facts = [
            make_fact("NetIncome", 96995000000.0, period="FY2024"),
            make_fact("ShareholdersEquity", 62615000000.0, period="FY2024"),
        ]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was ROE?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "calculation")
        self.assertIn("ROE", result["content"])
        calc = result["metadata"]["calculation"]
        self.assertEqual(calc["name"], "ROE")
        self.assertEqual(calc["formula"], "Net Profit / Equity")

    def test_current_ratio_valid(self):
        facts = [
            make_fact("CurrentAssets", 152986000000.0, period="FY2024"),
            make_fact("CurrentLiabilities", 165315000000.0, period="FY2024"),
        ]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was the current ratio?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "calculation")
        self.assertIn("Current Ratio", result["content"])

    def test_current_ratio_missing_input_blocks(self):
        # Only one input present -> must BLOCK (MISSING), never compute.
        facts = [make_fact("CurrentAssets", 152986000000.0, period="FY2024")]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was the current ratio?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "blocked")
        self.assertIn("couldn't verify", result["content"].lower())

    def test_cagr_valid(self):
        facts = [
            make_fact("Revenue", 383285000000.0, period="FY2023"),
            make_fact("Revenue", 391035000000.0, period="FY2024"),
        ]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was the revenue CAGR?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "cagr")
        self.assertIn("CAGR", result["content"])
        calc = result["metadata"]["calculation"]
        self.assertEqual(calc["name"], "CAGR")
        self.assertIsNotNone(calc["value"])
        self.assertEqual(calc["years"], 1)

    def test_cagr_single_period_blocks(self):
        facts = [make_fact("Revenue", 391035000000.0, period="FY2024")]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was the revenue CAGR?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "blocked")
        self.assertIn("couldn't verify", result["content"].lower())

    def test_change_computed_from_two_periods(self):
        facts = [
            make_fact("Revenue", 383285000000.0, period="FY2023"),
            make_fact("Revenue", 391035000000.0, period="FY2024"),
        ]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "How much did revenue change?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "change")
        self.assertIn("Revenue", result["content"])
        calc = result["metadata"]["calculation"]
        self.assertEqual(calc["name"], "Revenue change")


# ---------------------------------------------------------------------------
# No-fabrication behavior
# ---------------------------------------------------------------------------

class TestNoFabrication(unittest.TestCase):

    def test_missing_metric_never_invented(self):
        # Documents exist but contain no EPS evidence -> must refuse.
        facts = [make_fact("Revenue", 391035000000.0, period="FY2024")]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was EPS?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "document_miss")
        self.assertIn("couldn't verify", result["content"].lower())

    def test_calculation_blocked_includes_reason(self):
        facts = [make_fact("CurrentAssets", 152986000000.0, period="FY2024")]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was the current ratio?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["blocked_reason"], "Missing")
        self.assertIn("never estimate", result["content"].lower())

    def test_empty_question_prompt(self):
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer("   ", context=ChatContext(), documents=[])
        self.assertEqual(result["metadata"]["intent"], "empty")


# ---------------------------------------------------------------------------
# Follow-up questions + bounded context
# ---------------------------------------------------------------------------

class TestFollowUps(unittest.TestCase):

    def test_resolve_follow_up_it(self):
        ctx = ChatContext()
        ctx.add_assistant(
            "Revenue for FY2024 was 391,035 million.",
            {"topic": {"metric": "Revenue", "ticker": "AAPL",
                       "periods": ["FY2024"], "currency": "USD"},
             "content": "Revenue for FY2024 was 391,035 million."},
        )
        resolved = ctx.resolve_follow_up("How much did it grow?")
        self.assertIn("AAPL", resolved)
        self.assertIn("Revenue", resolved)

    def test_resolve_follow_up_previous_year(self):
        ctx = ChatContext()
        ctx.add_assistant(
            "Net income for FY2024 was 96,995 million.",
            {"topic": {"metric": "NetIncome", "ticker": "AAPL",
                       "periods": ["FY2024"], "currency": "USD"},
             "content": "Net income for FY2024 was 96,995 million."},
        )
        resolved = ctx.resolve_follow_up("How did it change vs the previous year?")
        self.assertIn("NetIncome", resolved)

    def test_context_bounded(self):
        ctx = ChatContext(max_messages=4)
        for i in range(10):
            ctx.add_user(f"question {i}")
            ctx.add_assistant(f"answer {i}", {})
        self.assertLessEqual(len(ctx.messages), 4)

    def test_context_round_trip(self):
        ctx = ChatContext()
        ctx.add_user("What was revenue?")
        ctx.add_assistant("Revenue was 391,035 million.", {})
        restored = ChatContext.from_state(ctx.to_state())
        self.assertEqual(len(restored.messages), 2)
        self.assertEqual(restored.messages[0]["role"], "user")

    def test_change_uses_topic_metric(self):
        # "How much did it grow?" without naming the metric resolves via topic.
        facts = [
            make_fact("Revenue", 383285000000.0, period="FY2023"),
            make_fact("Revenue", 391035000000.0, period="FY2024"),
        ]
        ctx = ChatContext()
        ctx.add_assistant(
            "Revenue for FY2024 was 391,035 million.",
            {"topic": {"metric": "Revenue", "periods": ["FY2024"], "currency": "USD"},
             "content": "Revenue for FY2024 was 391,035 million."},
        )
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "How much did it grow?",
            context=ctx,
            documents=[make_doc(facts)],
        )
        self.assertEqual(result["metadata"]["intent"], "change")
        self.assertIn("Revenue", result["content"])


# ---------------------------------------------------------------------------
# Company questions via Agentic RAG runner
# ---------------------------------------------------------------------------

class TestCompanyRag(unittest.TestCase):

    @staticmethod
    def _complete_rag(ticker, goal, max_iterations=3):
        return {
            "ticker": ticker,
            "terminal_state": "COMPLETE",
            "terminal_reason": "",
            "summary_text": f"{ticker} FY2024 revenue was 391,035 million.",
            "resolved_facts": [
                {"metric_name": "Revenue", "value": 391035000000.0,
                 "fiscal_period": "FY2024", "currency_code": "USD",
                 "scale": "10^6", "source": "postgresql",
                 "source_tier": 2, "document_id": "doc-1"},
            ],
        }

    def test_company_answer_uses_rag(self):
        assistant = FinancialChatAssistant(
            llm_call=stub_llm, rag_runner=self._complete_rag
        )
        result = assistant.answer(
            "What was AAPL revenue?",
            context=ChatContext(),
            documents=[],
            provider_health=no_provider_health(),
        )
        self.assertEqual(result["metadata"]["intent"], "company")
        self.assertIn("AAPL", result["content"])
        self.assertTrue(result["metadata"]["evidence"])

    def test_rag_insufficient_evidence_blocks(self):
        def insufficient_rag(ticker, goal, max_iterations=3):
            return {
                "ticker": ticker,
                "terminal_state": "INSUFFICIENT_EVIDENCE",
                "terminal_reason": "retrieval exhausted",
                "summary_text": "",
                "resolved_facts": [],
            }
        assistant = FinancialChatAssistant(
            llm_call=stub_llm, rag_runner=insufficient_rag
        )
        result = assistant.answer(
            "Analyze AAPL",
            context=ChatContext(),
            documents=[],
            provider_health=no_provider_health(),
        )
        self.assertEqual(result["metadata"]["intent"], "company_blocked")
        self.assertIn("couldn't verify", result["content"].lower())


# ---------------------------------------------------------------------------
# Provider chain / graceful degradation
# ---------------------------------------------------------------------------

class TestProviders(unittest.TestCase):

    def test_no_provider_graceful(self):
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "Tell me about market trends",
            context=ChatContext(),
            documents=[],
            provider_health=no_provider_health(),
        )
        self.assertEqual(result["metadata"]["intent"], "no_provider")
        self.assertIn("unavailable", result["content"].lower())

    def test_general_question_uses_llm_chain(self):
        called = {}

        def recording_llm(prompt, system_prompt=None):
            called["prompt"] = prompt
            return "Here is a general market overview."

        assistant = FinancialChatAssistant(llm_call=recording_llm)
        result = assistant.answer(
            "Tell me about market trends",
            context=ChatContext(),
            documents=[],
            provider_health={"Google AI Studio": True},
        )
        self.assertEqual(result["metadata"]["intent"], "general")
        self.assertEqual(result["content"], "Here is a general market overview.")
        self.assertIn("market trends", called.get("prompt", "").lower())


# ---------------------------------------------------------------------------
# Secret safety
# ---------------------------------------------------------------------------

class TestSecretSafety(unittest.TestCase):

    def test_metadata_never_contains_key_patterns(self):
        facts = [make_fact("Revenue", 391035000000.0, period="FY2024")]
        assistant = FinancialChatAssistant(llm_call=stub_llm)
        result = assistant.answer(
            "What was the revenue?",
            context=ChatContext(),
            documents=[make_doc(facts)],
        )
        blob = str(result)
        for pattern in ("sk-", "gsk_", "AIza", "Bearer ", "nvapi-"):
            self.assertNotIn(pattern, blob)

    def test_sanitize_metadata_redacts_credentials(self):
        meta = {
            "intent": "general",
            "content": "ok",
            "evidence": [{"source": "sk-test1234567890"}],
        }
        safe = FinancialChatAssistant._sanitize_metadata(meta)
        blob = str(safe)
        self.assertNotIn("sk-test1234567890", blob)
        self.assertIn("REDACTED", blob)


if __name__ == "__main__":
    unittest.main()
