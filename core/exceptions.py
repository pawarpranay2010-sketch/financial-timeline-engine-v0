# core/exceptions.py
"""
Custom exception hierarchy for the Financial Timeline Engine.

Every layer of the application (ingestion, gateway, timeline, intelligence,
exports, backend) raises one of these instead of bare built-ins
(ValueError/RuntimeError/Exception). This gives calling code -- notably the
AI Gateway's retry/circuit-breaker logic and the Streamlit UI's error
handling -- a precise, catchable vocabulary instead of having to guess what
a bare `except Exception` might mean.

Nothing here changes behavior for existing call sites: catching
`FinancialTimelineEngineError` catches everything below it, and every
subclass is still a normal `Exception`, so any existing broad
`except Exception:` block continues to work unmodified.
"""
from __future__ import annotations


class FinancialTimelineEngineError(Exception):
    """Base class for every exception raised by this application.

    Catching this single type is sufficient for code that just wants to
    know "something in our engine failed" without caring about the
    specific layer.
    """


# --------------------------------------------------------------------------
# AI Gateway / provider errors
# --------------------------------------------------------------------------
class ProviderError(FinancialTimelineEngineError):
    """Base class for all AI-provider-related failures (gateway/*)."""


class ProviderNotConfiguredError(ProviderError):
    """Raised when a provider is selected but has no API key configured."""


class ProviderAuthenticationError(ProviderError):
    """Raised when a provider rejects the request due to bad/missing credentials."""


class ProviderRateLimitedError(ProviderError):
    """Raised when a provider returns a rate-limit response (e.g. HTTP 429)."""


class ProviderTimeoutError(ProviderError):
    """Raised when a provider request exceeds its configured timeout."""


class ProviderRequestError(ProviderError):
    """Raised for any other non-2xx / malformed-response provider failure."""


class CircuitOpenError(ProviderError):
    """Raised when a provider's circuit breaker is open and the call is
    short-circuited without making a network request."""


class AllProvidersFailedError(ProviderError):
    """Raised by the gateway's router when every provider in the fallback
    chain has failed for a given request."""


# --------------------------------------------------------------------------
# Ingestion errors
# --------------------------------------------------------------------------
class DocumentParsingError(FinancialTimelineEngineError):
    """Raised when a document (PDF/DOCX/XLSX/CSV/TXT) cannot be parsed."""


class UnsupportedFileTypeError(DocumentParsingError):
    """Raised when an uploaded file's extension has no registered parser."""


# --------------------------------------------------------------------------
# Validation errors
# --------------------------------------------------------------------------
class ResponseValidationError(FinancialTimelineEngineError):
    """Raised when an AI response fails structural/JSON validation."""


class ConfigurationError(FinancialTimelineEngineError):
    """Raised when required configuration/secrets are missing or invalid."""


# --------------------------------------------------------------------------
# Export errors
# --------------------------------------------------------------------------
class ExportGenerationError(FinancialTimelineEngineError):
    """Raised when a JSON/CSV/Excel/Markdown/DOCX/PDF export fails to build."""
