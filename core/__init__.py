# core/__init__.py
"""
Core package: configuration, constants, logging, utilities, validation,
and exceptions shared by every other package in the Financial Timeline
Engine (ingestion, gateway, timeline, intelligence, memo, exports,
backend, frontend).

This package has no dependency on any other engine package, and its only
optional dependency on `streamlit` is isolated inside
`core.config.StreamlitSecretsProvider` and
`core.logging.StreamlitSessionLogSink` -- everything else here is plain
Python and importable/testable outside of a Streamlit session.
"""
from core.config import SETTINGS, FUTURE_MODULES, get_secret
from core.constants import GROUNDING_RULE, DEFAULT_SESSION_STATE, ERROR_RESPONSE_MARKERS
from core.exceptions import (
    FinancialTimelineEngineError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderAuthenticationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderRequestError,
    CircuitOpenError,
    AllProvidersFailedError,
    DocumentParsingError,
    UnsupportedFileTypeError,
    ResponseValidationError,
    ConfigurationError,
    ExportGenerationError,
)
from core.logging import get_logger, provider_logger, get_provider_health
from core.utilities import hash_text, CacheManager, retry
from core.validation import is_error_response, contains_error_marker, extract_json

__all__ = [
    "SETTINGS",
    "FUTURE_MODULES",
    "get_secret",
    "GROUNDING_RULE",
    "DEFAULT_SESSION_STATE",
    "ERROR_RESPONSE_MARKERS",
    "FinancialTimelineEngineError",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderAuthenticationError",
    "ProviderRateLimitedError",
    "ProviderTimeoutError",
    "ProviderRequestError",
    "CircuitOpenError",
    "AllProvidersFailedError",
    "DocumentParsingError",
    "UnsupportedFileTypeError",
    "ResponseValidationError",
    "ConfigurationError",
    "ExportGenerationError",
    "get_logger",
    "provider_logger",
    "get_provider_health",
    "hash_text",
    "CacheManager",
    "retry",
    "is_error_response",
    "contains_error_marker",
    "extract_json",
]
