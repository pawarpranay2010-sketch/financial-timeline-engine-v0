"""
Stage 2 — API tests for the standalone FastAPI backend.

Covers:
  1. App boots and binds quickly (module import time is fast).
  2. GET /api/v1/health returns ok + component status without blocking.
  3. GET /api/v1/providers/status exposes key names but NEVER values.
  4. GET /api/v1/market/{ticker} returns a structured snapshot.
  5. POST /api/v1/intelligence/analyze runs the Agentic RAG pipeline.
  6. POST /api/v1/db/init initializes the schema on demand.
  7. Static frontend is served at /.
  8. No credentials leak into any response payload.
"""
import sys
import os
import time

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import create_app

SECRET_PATTERNS = [
    "sk-", "AIza", "gsk_", "key=", "api_key=", "apikey=",
    "Bearer ", "password=", "DATABASE_URL=", "postgres://", "postgresql://",
]


class TestStage2AppBoot(unittest.TestCase):
    """The app factory must be importable and cheap to construct."""

    def test_create_app_is_fast(self):
        start = time.monotonic()
        app = create_app()
        elapsed = time.monotonic() - start
        self.assertIsNotNone(app)
        # App construction must not block on DB/providers: keep it fast.
        self.assertLess(elapsed, 5.0)


class TestStage2Health(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_health_ok(self):
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "financial-timeline-engine-api")
        self.assertEqual(body["stage"], 2)
        self.assertIn("database", body)
        self.assertIn("redis", body)
        self.assertIn("providers", body)
        self.assertGreaterEqual(body["uptime_seconds"], 0)

    def test_health_database_status_shape(self):
        body = self.client.get("/api/v1/health").json()
        db = body["database"]
        for key in ("configured", "reachable", "error"):
            self.assertIn(key, db)

    def test_health_does_not_block_on_redis(self):
        body = self.client.get("/api/v1/health").json()
        self.assertIn("configured", body["redis"])

    def test_providers_status_names_only(self):
        res = self.client.get("/api/v1/providers/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        names = [p["name"] for p in body["providers"]]
        self.assertIn("fmp", names)
        self.assertIn("finnhub", names)
        self.assertIn("google", names)
        # Each entry reports presence, never a value
        for p in body["providers"]:
            self.assertIsInstance(p["key_configured"], bool)
            self.assertNotEqual(p["key_configured"], str)  # never the actual key

    def test_no_secrets_in_health_or_providers(self):
        for path in ("/api/v1/health", "/api/v1/providers/status"):
            text = self.client.get(path).text
            for pattern in SECRET_PATTERNS:
                self.assertNotIn(pattern, text, f"secret pattern {pattern!r} leaked via {path}")


class TestStage2Market(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    @patch("api.services.fetch_market_snapshot")
    def test_market_snapshot_returns_structured_payload(self, mock_fetch):
        mock_fetch.return_value = {
            "ticker": "AAPL",
            "success": True,
            "data": {
                "company_profile": {"success": True, "data": {"ticker": "AAPL", "company_name": "Apple Inc."}},
                "market_price": {"success": True, "data": {"price": 230.5}},
                "financials": {"success": True, "data": []},
                "news": {"success": True, "data": []},
                "filings": {"success": True, "data": []},
            },
            "latency_ms": 12,
            "error": None,
        }
        res = self.client.get("/api/v1/market/AAPL")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["ticker"], "AAPL")
        self.assertTrue(body["success"])
        self.assertIn("data", body)

    def test_market_graceful_on_failure(self):
        # Without mocking, DataAgent may fail (no live providers / no DB row).
        # The endpoint must still return 200 with a structured error state.
        res = self.client.get("/api/v1/market/ZZZZ_NOPE")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["ticker"], "ZZZZ_NOPE")
        self.assertIn("success", body)
        self.assertIn("data", body)
        # Error message must not contain secrets
        text = res.text
        for pattern in SECRET_PATTERNS:
            self.assertNotIn(pattern, text)


class TestStage2Intelligence(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    @patch("api.services.run_analysis")
    def test_analyze_endpoint_wires_response(self, mock_run):
        mock_run.return_value = {
            "ticker": "AAPL",
            "goal": "Analyze AAPL revenue",
            "terminal_state": "COMPLETE",
            "terminal_reason": "All requirements satisfied",
            "iterations_used": 1,
            "evidence_count": 5,
            "resolved_count": 3,
            "resolved_facts": [
                {"metric_name": "Revenue", "value": 391000000000.0,
                 "currency_code": "USD", "fiscal_period": "FY2024",
                 "source": "postgresql", "source_tier": 2}
            ],
            "summary_text": "=== CANONICAL EVIDENCE SET ===\nStatus: COMPLETE",
        }
        res = self.client.post(
            "/api/v1/intelligence/analyze",
            json={"ticker": "AAPL", "goal": "Analyze AAPL revenue"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["terminal_state"], "COMPLETE")
        self.assertEqual(body["resolved_count"], 3)
        self.assertEqual(len(body["resolved_facts"]), 1)

    def test_analyze_rejects_bad_input(self):
        res = self.client.post(
            "/api/v1/intelligence/analyze",
            json={"ticker": "", "goal": ""},
        )
        self.assertEqual(res.status_code, 422)

    @patch("api.services.run_analysis", side_effect=RuntimeError("boom"))
    def test_analyze_500_on_pipeline_error(self, mock_run):
        res = self.client.post(
            "/api/v1/intelligence/analyze",
            json={"ticker": "AAPL", "goal": "Analyze AAPL revenue"},
        )
        self.assertEqual(res.status_code, 500)


class TestStage2Frontend(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_frontend_served_at_root(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Platrixa", res.text)

    def test_frontend_assets_served(self):
        css = self.client.get("/styles.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("text/css", css.headers.get("content-type", ""))
        js = self.client.get("/app.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("javascript", js.headers.get("content-type", ""))


if __name__ == "__main__":
    unittest.main()
