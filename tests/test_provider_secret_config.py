"""
Platrixa
AI / Provider Configuration — Secret-Loading Regression Tests

Verifies the production-blocker fix: provider keys are loaded through
core.config.get_secret with priority:

    1. Environment variable (if available)
    2. Streamlit Secrets (if available)

Coverage:
  - get_secret: env var wins over Streamlit secrets
  - get_secret: Streamlit secrets are the fallback when env is absent
  - CompositeSecretsProvider chain order (env first, then streamlit)
  - ProviderManager registers Groq / Google adapters when keys exist
    via get_secret (not st.secrets directly)
  - get_provider_health reflects configured keys
  - app.py provider functions load keys via get_secret (no st.secrets)
  - No secret VALUES are ever printed or exposed in responses

Run: python3 -m pytest tests/test_provider_secret_config.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import unittest
from unittest import mock

logging.getLogger("fte").setLevel(logging.CRITICAL)


class _FakeStreamlitSecrets:
    """Simulates streamlit's st.secrets for tests that exercise the
    StreamlitSecretsProvider path without a real streamlit runtime."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=""):
        return self._values.get(key, default)


class TestGetSecretPriority(unittest.TestCase):
    """Priority: env var first, Streamlit secrets second."""

    def tearDown(self):
        for k in ("GROQ_API_KEY", "GOOGLE_API_KEY", "FMP_API_KEY"):
            os.environ.pop(k, None)

    def test_env_wins_over_streamlit_secrets(self):
        from core.config import CompositeSecretsProvider, EnvSecretsProvider

        os.environ["GROQ_API_KEY"] = "env-groq-value"
        chain = CompositeSecretsProvider([
            EnvSecretsProvider(),
            _FakeStreamlitSecrets({"GROQ_API_KEY": "st-groq-value"}),
        ])
        value = chain.get("GROQ_API_KEY")
        self.assertEqual(value, "env-groq-value")

    def test_streamlit_secrets_fallback_when_env_absent(self):
        from core.config import CompositeSecretsProvider, EnvSecretsProvider

        os.environ.pop("GROQ_API_KEY", None)
        chain = CompositeSecretsProvider([
            EnvSecretsProvider(),
            _FakeStreamlitSecrets({"GROQ_API_KEY": "st-groq-value"}),
        ])
        value = chain.get("GROQ_API_KEY")
        self.assertEqual(value, "st-groq-value")

    def test_default_when_neither_present(self):
        from core.config import CompositeSecretsProvider, EnvSecretsProvider

        os.environ.pop("FMP_API_KEY", None)
        chain = CompositeSecretsProvider([
            EnvSecretsProvider(),
            _FakeStreamlitSecrets({}),
        ])
        self.assertEqual(chain.get("FMP_API_KEY"), "")

    def test_get_secret_uses_env_when_available(self):
        from core.config import get_secret

        os.environ["GOOGLE_API_KEY"] = "env-google-value"
        self.assertEqual(get_secret("GOOGLE_API_KEY"), "env-google-value")

    def test_get_secret_empty_when_unset(self):
        from core.config import get_secret

        os.environ.pop("GOOGLE_API_KEY", None)
        os.environ.pop("GROQ_API_KEY", None)
        self.assertEqual(get_secret("GOOGLE_API_KEY", ""), "")
        self.assertEqual(get_secret("GROQ_API_KEY", ""), "")

    def test_default_provider_order_is_env_then_streamlit(self):
        # The default chain must prefer environment variables (local dev,
        # tests, backend) and fall back to Streamlit secrets (deployed
        # Streamlit Cloud) — the exact fix requested.
        from core.config import get_default_secrets_provider

        provider = get_default_secrets_provider()
        self.assertIsNotNone(provider)
        chain = provider._providers
        self.assertEqual(chain[0].__class__.__name__, "EnvSecretsProvider")
        self.assertEqual(chain[1].__class__.__name__, "StreamlitSecretsProvider")


class TestProviderManagerDetection(unittest.TestCase):
    """ProviderManager must register adapters when keys exist via get_secret."""

    def tearDown(self):
        for k in ("GROQ_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
            os.environ.pop(k, None)

    def test_groq_registered_when_key_configured(self):
        os.environ["GROQ_API_KEY"] = "test-groq-key-not-real"
        from backend.gateway.provider_manager import ProviderManager

        pm = ProviderManager()
        self.assertTrue(pm.key_status().get("groq"))
        self.assertIsNotNone(pm.get("groq"))

    def test_no_adapter_when_no_key(self):
        os.environ.pop("GROQ_API_KEY", None)
        from backend.gateway.provider_manager import ProviderManager

        pm = ProviderManager()
        self.assertFalse(pm.key_status().get("groq"))
        self.assertIsNone(pm.get("groq"))

    def test_google_registered_when_key_configured(self):
        os.environ["GOOGLE_API_KEY"] = "test-google-key-not-real"
        from backend.gateway.provider_manager import ProviderManager

        pm = ProviderManager()
        self.assertTrue(pm.key_status().get("google"))
        self.assertIsNotNone(pm.get("google"))

    def test_key_status_never_contains_values(self):
        os.environ["GROQ_API_KEY"] = "super-secret-value"
        from backend.gateway.provider_manager import ProviderManager

        pm = ProviderManager()
        status = pm.key_status()
        # Only booleans — never the secret value itself.
        self.assertTrue(all(isinstance(v, bool) for v in status.values()))
        summary = pm.summary()
        for entry in summary:
            self.assertNotIn("super-secret-value", str(entry))


class TestProviderHealth(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("GROQ_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)

    def test_health_reflects_configured_keys(self):
        from core.logging import get_provider_health

        os.environ["GROQ_API_KEY"] = "test-groq-key-not-real"
        os.environ.pop("GOOGLE_API_KEY", None)
        health = get_provider_health()
        self.assertTrue(health.get("Groq"))
        self.assertFalse(health.get("Google AI Studio"))
        # No key values in the health output.
        self.assertNotIn("test-groq-key-not-real", str(health))


class TestAppProviderFunctions(unittest.TestCase):
    """app.py provider functions must load keys through get_secret."""

    def tearDown(self):
        os.environ.pop("GROQ_API_KEY", None)

    def test_app_imports_and_functions_exist(self):
        import app as app_module

        for fn_name in ("call_google_ai_studio", "call_groq_engine",
                        "_openrouter_request"):
            self.assertTrue(callable(getattr(app_module, fn_name)))

    def test_groq_uses_get_secret_and_raises_when_missing(self):
        import app as app_module

        os.environ.pop("GROQ_API_KEY", None)
        with mock.patch.object(app_module, "get_secret", return_value="") as m:
            with self.assertRaises(ValueError) as ctx:
                app_module.call_groq_engine("hello")
            m.assert_called_once_with("GROQ_API_KEY", "")
            self.assertIn("Missing Groq Key", str(ctx.exception))

    def test_groq_delegates_to_canonical_gateway_without_exposing(self):
        # With a key present via get_secret, the thin compatibility wrapper
        # delegates to the canonical AI gateway (call_ai_with_fallback). The
        # key value must never leak into the returned response.
        import app as app_module

        os.environ["GROQ_API_KEY"] = "runtime-groq-secret"
        with mock.patch.object(app_module, "get_secret",
                               return_value="runtime-groq-secret") as m:
            with mock.patch.object(app_module, "call_ai_with_fallback",
                                   return_value="ok") as fallback:
                result = app_module.call_groq_engine("hello")
        self.assertEqual(result, "ok")
        m.assert_called_once_with("GROQ_API_KEY", "")
        fallback.assert_called_once()
        self.assertNotIn("runtime-groq-secret", str(result))
        self.assertNotIn("runtime-groq-secret", str(fallback.call_args))

    def test_no_st_secrets_reads_in_provider_functions(self):
        # Regression: provider functions must not bypass the chain with
        # a direct st.secrets read.
        import inspect
        import app as app_module

        for fn_name in ("call_google_ai_studio", "call_groq_engine"):
            src = inspect.getsource(getattr(app_module, fn_name))
            self.assertNotIn("st.secrets", src)
            self.assertIn("get_secret", src)


if __name__ == "__main__":
    unittest.main()
