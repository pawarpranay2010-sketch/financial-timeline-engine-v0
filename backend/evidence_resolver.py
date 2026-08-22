"""
Platrixa
Sprint 6.5 - Deterministic External Evidence Recovery

A single, centralized resolver abstraction for missing financial metrics.
A missing metric is recovered ONLY through this strict hierarchy:

    Tier 1 - Primary uploaded document
    Tier 2 - User-uploaded workspace appendices / supplementary documents
    Tier 3 - Approved verified regulatory / structured data providers
    BLOCKED - otherwise

CRITICAL RULE
------------
Tier 4 (random web / search / blog / scraped sources) is FORBIDDEN.
No Google/Bing/generic web search, no blogs, no snippets, no arbitrary
scrapers, no LLM-generated numbers. If Tier 3 cannot provide a
defensible match, the metric MUST remain blocked.

Design rules
------------
- The resolver stops at the FIRST defensible result; it never continues
  to a lower tier after a valid higher-tier fact is found.
- No provider credentials, API endpoints or response schemas are
  invented here. Approved providers are structural stubs that report
  UNAVAILABLE until real credentials are configured via environment
  variables (never in source code). When unconfigured or failing, the
  resolver simply moves to the next approved provider, then BLOCKED.
- Only the minimum necessary information (company_identifier, metric,
  reporting_period) is ever passed to an external provider.
- Sprint 5 evidence fragments and Sprint 6 page/document anchors remain
  untouched; nothing here fabricates provenance.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from backend.financial_extractor import extract_financial_data
from backend.financial_calculator import calculate_financial_ratios


# ---------------------------------------------------------------
# Provenance tiers (explicit provenance schema)
# ---------------------------------------------------------------

class PROVENANCE_TIER:
    DOCUMENT = "DOCUMENT"
    APPENDIX = "APPENDIX"
    REGULATORY_API = "REGULATORY_API"
    DERIVED = "DERIVED"
    EXTERNAL_DERIVED = "EXTERNAL_DERIVED"
    BLOCKED = "BLOCKED"
    UNANALYZED = "UNANALYZED"


# ---------------------------------------------------------------
# Strict external matching vocabulary
# ---------------------------------------------------------------

SUPPORTED_SCALES = {
    "unit", "units", "thousands", "millions", "billions", "crores",
}

# Canonical metric names the resolver understands. Aliases map onto a
# canonical definition; anything NOT in this map is not a compatible
# definition (e.g. Operating Revenue is NOT Revenue, Net Income IS
# Net Profit). Nothing is assumed - an unknown payload field is rejected.
METRIC_ALIASES = {
    "revenue": "Revenue",
    "sales": "Revenue",
    "total revenue": "Revenue",
    "net income": "Net Profit",
    "net profit": "Net Profit",
    "profit after tax": "Net Profit",
    "pat": "Net Profit",
    "ebitda": "EBITDA",
    "operating profit": "Operating Profit",
    "operating income": "Operating Profit",
    "eps": "EPS",
    "earnings per share": "EPS",
    "total debt": "Debt",
    "debt": "Debt",
    "total assets": "Assets",
    "assets": "Assets",
    "total liabilities": "Liabilities",
    "liabilities": "Liabilities",
    "shareholders' equity": "Equity",
    "shareholders equity": "Equity",
    "total equity": "Equity",
    "equity": "Equity",
    "cash flow from operations": "Cash Flow",
    "operating cash flow": "Cash Flow",
    "profit margin": "Profit Margin",
    "roe": "ROE",
    "roa": "ROA",
    "debt to equity": "Debt to Equity",
    "debt/equity": "Debt to Equity",
    "current assets": "Current Assets",
    "current liabilities": "Current Liabilities",
    "current ratio": "Current Ratio",
    "cagr": "CAGR",
}

# Ratios the existing calculator can derive. The FORMULAS and VALUES come
# exclusively from backend/financial_calculator.py - this map only names
# the inputs (no calculation logic is duplicated here).
RATIO_INPUT_SETS = {
    "Profit Margin": ["Net Profit", "Revenue"],
    "ROE": ["Net Profit", "Equity"],
    "ROA": ["Net Profit", "Assets"],
    "Debt to Equity": ["Debt", "Equity"],
    "Current Ratio": ["Current Assets", "Current Liabilities"],
}

# Metrics the Financial Grid can display (mirror of the terminal's
# canonical list). Recovery is only attempted for these names so a
# recovered fact always reaches the grid.
GRID_METRICS = [
    "Revenue", "Net Profit", "EBITDA", "Operating Profit", "EPS", "Debt",
    "Assets", "Liabilities", "Equity", "Cash Flow",
    "Profit Margin", "ROE", "ROA", "Debt to Equity", "Current Ratio", "CAGR",
]

RATIO_KEYS = {
    "Profit Margin", "ROE", "ROA", "Debt to Equity", "Current Ratio", "CAGR",
}

BLOCKED_REASON = "Not available in permitted evidence sources."


# ---------------------------------------------------------------
# Approved provider interface
# ---------------------------------------------------------------

class ExternalEvidenceProvider(ABC):
    """Interface for an APPROVED structured/regulatory provider.

    Implementations receive only the minimum necessary information:
    company_identifier, metric, reporting_period. They must never
    receive the uploaded document itself.

    resolve_metric() returns a raw payload dict, or None to signal
    UNAVAILABLE (unconfigured / no matching record / transient error).
    The resolver validates every payload against strict identity,
    period, metric-definition, currency and scale checks before any
    fact may enter the pipeline. A provider that is unconfigured or
    returns invalid data simply causes the resolver to move on.
    """

    name: str = "unconfigured-provider"

    @abstractmethod
    def is_configured(self) -> bool:
        """True when real credentials/access are configured. Credentials
        come from environment variables only - never from source code."""

    @abstractmethod
    def resolve_metric(
        self,
        company_identifier: Optional[str],
        metric: str,
        reporting_period: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Return a raw payload dict for the metric, or None (UNAVAILABLE)."""


class _UnconfiguredProvider(ExternalEvidenceProvider):
    """Base for approved providers that are architecturally supported but
    not yet wired to credentials. Returns UNAVAILABLE (None) so the
    resolver never crashes and simply moves to the next approved provider.

    No API endpoints, credentials or response schemas are invented for
    these stubs. Real HTTP integrations (SEC EDGAR, India MCA, Bloomberg
    Data License, Refinitiv) plug in behind the same interface, reading
    their own environment variables (e.g. FTE_BLOOMBERG_API_KEY), and
    must keep sending only {company_identifier, metric, reporting_period}.
    """

    name = "unconfigured"

    def is_configured(self) -> bool:
        return False

    def resolve_metric(
        self,
        company_identifier: Optional[str],
        metric: str,
        reporting_period: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        return None


class SECEdgarProvider(_UnconfiguredProvider):
    name = "SEC EDGAR"


class IndiaMCAProvider(_UnconfiguredProvider):
    name = "India MCA"


class BloombergDataLicenseProvider(_UnconfiguredProvider):
    name = "Bloomberg Data License"


class RefinitivProvider(_UnconfiguredProvider):
    name = "Refinitiv"


DEFAULT_PROVIDERS: List[ExternalEvidenceProvider] = [
    SECEdgarProvider(),
    IndiaMCAProvider(),
    BloombergDataLicenseProvider(),
    RefinitivProvider(),
]


# ---------------------------------------------------------------
# Strict external matching
# ---------------------------------------------------------------

def _normalize_period(period: Optional[str]) -> Optional[str]:
    """Normalize a reporting-period label so 'FY2025' == '2025' but
    'FY2024' != 'FY2025' and 'Q1 FY2026' != 'FY2025'."""
    if period is None:
        return None
    s = re.sub(r"[^0-9a-z]", "", str(period).strip().lower())
    s = re.sub(r"fy", "", s)
    return s or None


def _canonical_metric(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return METRIC_ALIASES.get(str(name).strip().lower())


def _numeric_value(value: Any) -> Optional[float]:
    """A value is usable only when it is numeric or an unambiguous
    numeric string; anything else (a guess, a label, a range) is None."""
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        s = s.replace(",", "")
        return float(s)
    except Exception:
        return None


def _validate_external_result(
    payload: Optional[Dict[str, Any]],
    company_context: Dict[str, Any],
    metric: str,
    reporting_period: Optional[str],
) -> tuple:
    """Strict validation of a Tier 3 payload. Returns (ok, reason).
    Every relevant identity check must pass; anything unverifiable
    fails closed (the metric stays BLOCKED)."""
    if not isinstance(payload, dict):
        return False, "provider returned no usable record"
    if _numeric_value(payload.get("value")) is None:
        return False, "no usable numeric value"

    # --- Company identity (prefer canonical IDs; never name similarity
    #     alone, and never accept a payload whose identity is unprovable)
    req_id = company_context.get("cik") or company_context.get("company_identifier")
    pay_id = payload.get("cik") or payload.get("company_identifier")
    if req_id and pay_id:
        if str(req_id).strip() != str(pay_id).strip():
            return False, "company identifier mismatch"
    elif req_id and not pay_id:
        return False, "missing company identifier in provider record"
    elif not req_id and pay_id:
        return False, "company identity cannot be verified"
    else:
        req_name = str(company_context.get("company_name") or "").strip().lower()
        pay_name = str(payload.get("company_name") or "").strip().lower()
        if not req_name or not pay_name or req_name != pay_name:
            return False, "company identity cannot be verified"

    # --- Reporting period (no silent substitution of another period)
    req_p = _normalize_period(reporting_period)
    pay_p = _normalize_period(payload.get("reporting_period") or payload.get("period"))
    if req_p and pay_p:
        if req_p != pay_p:
            return False, "reporting period mismatch"
    elif req_p and not pay_p:
        return False, "missing reporting period in provider record"
    else:
        return False, "reporting period cannot be verified"

    # --- Metric definition (Net Income != EBITDA; Operating Revenue !=
    #     Revenue unless the definition is explicitly compatible)
    req_canon = _canonical_metric(metric)
    pay_canon = _canonical_metric(payload.get("metric") or payload.get("field"))
    if not req_canon or req_canon != pay_canon:
        return False, "metric definition mismatch"

    # --- Currency (reject incompatible currencies; no silent conversion;
    #     one-sided or unknown currency cannot be verified -> reject)
    req_cur = str(company_context.get("currency") or "").strip().upper()
    pay_cur = str(payload.get("currency") or "").strip().upper()
    if req_cur and pay_cur:
        if req_cur != pay_cur:
            return False, "currency mismatch"
    elif req_cur or pay_cur:
        return False, "currency cannot be verified"

    # --- Scale (never assume: unknown/unsupported scale -> reject)
    scale = str(payload.get("scale") or "").strip().lower()
    if scale not in SUPPORTED_SCALES:
        return False, "scale unknown or unsupported"

    return True, "ok"


def _normalize_external_fact(
    payload: Dict[str, Any],
    provider: ExternalEvidenceProvider,
    metric: str,
    reporting_period: Optional[str],
) -> Dict[str, Any]:
    """Normalize a validated provider payload into a pipeline fact with
    REGULATORY_API provenance. Evidence is the provider's own citation
    string only - never a fabricated document fragment."""
    return {
        "value": _numeric_value(payload.get("value")),
        "source": "Regulatory API",
        "provenance_tier": PROVENANCE_TIER.REGULATORY_API,
        "provider": provider.name,
        "provider_identifier": str(
            payload.get("provider_identifier")
            or payload.get("id")
            or payload.get("cik")
            or "—"
        ),
        "reporting_period": str(
            payload.get("reporting_period") or reporting_period or "—"
        ),
        "currency": str(payload.get("currency") or "—"),
        "scale": str(payload.get("scale") or "—"),
        "evidence": str(payload.get("evidence") or payload.get("source_ref") or "—"),
        "metric": metric,
    }


# ---------------------------------------------------------------
# Tier 2 - workspace appendices
# ---------------------------------------------------------------

def _resolve_appendix(
    metric: str,
    workspace_documents: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Search ONLY documents explicitly listed in the workspace (the same
    workspace/company context as the primary upload). A fact is recovered
    only when the extractor finds a value AND the evidence fragment is a
    real substring of that document's text. Never fabricates a citation."""
    for document in workspace_documents or []:
        if not isinstance(document, dict):
            continue
        text = document.get("text") or ""
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            facts = extract_financial_data(text)
        except Exception:
            continue
        fact = facts.get(metric)
        if not isinstance(fact, dict) or fact.get("value") is None:
            continue
        evidence = fact.get("evidence")
        if not (isinstance(evidence, str) and evidence.strip() and evidence in text):
            # Evidence must be a real substring of the appendix; otherwise
            # the match cannot be defended.
            continue
        out = dict(fact)
        out["metric"] = metric
        out["provenance_tier"] = PROVENANCE_TIER.APPENDIX
        out.setdefault("document_name", document.get("document_name") or "—")
        out.setdefault("source", "Appendix")
        return out
    return None


# ---------------------------------------------------------------
# Tier 3 - approved providers + external-derived
# ---------------------------------------------------------------

def _resolve_tier3_direct(
    metric: str,
    company_context: Dict[str, Any],
    reporting_period: Optional[str],
    providers: List[ExternalEvidenceProvider],
) -> tuple:
    """Iterate approved providers; the FIRST defensible, validated result
    wins. Returns (fact, None) on success, or (None, reason) where reason
    is the last validation rejection (so BLOCKED stays specific about
    WHY the provider record was rejected). Unconfigured / failing /
    invalid providers are skipped."""
    company_identifier = (
        company_context.get("company_identifier") or company_context.get("cik")
    )
    last_reason: Optional[str] = None
    for provider in providers or []:
        try:
            payload = provider.resolve_metric(
                company_identifier, metric, reporting_period
            )
        except Exception:
            continue  # provider error -> next approved provider (fail closed)
        if payload is None:
            continue  # UNAVAILABLE -> next approved provider
        ok, reason = _validate_external_result(
            payload, company_context, metric, reporting_period
        )
        if not ok:
            last_reason = reason
            continue  # invalid record -> never enters the pipeline
        return (
            _normalize_external_fact(payload, provider, metric, reporting_period),
            None,
        )
    return None, last_reason


def _resolve_tier3_input(
    metric: str,
    company_context: Dict[str, Any],
    reporting_period: Optional[str],
    providers: List[ExternalEvidenceProvider],
) -> Optional[Dict[str, Any]]:
    """Resolve a single input metric through Tier 3 providers only (used
    for EXTERNAL_DERIVED). Returns a validated REGULATORY_API fact or None."""
    fact, _reason = _resolve_tier3_direct(
        metric, company_context, reporting_period, providers
    )
    if fact is None or fact.get("value") is None:
        return None
    return fact


def _resolve_external_derived(
    metric: str,
    company_context: Dict[str, Any],
    reporting_period: Optional[str],
    workspace_documents: List[Dict[str, Any]],
    primary_facts: Dict[str, Any],
    providers: List[ExternalEvidenceProvider],
) -> Optional[Dict[str, Any]]:
    """If a missing RATIO can be calculated from externally verified
    inputs, distinguish it from a directly reported external fact.
    Every input must itself have valid provenance (document, appendix or
    validated regulatory API); the VALUE and FORMULA come exclusively
    from the existing calculator - never from unverified numbers."""
    inputs = RATIO_INPUT_SETS.get(metric)
    if not inputs:
        return None
    resolved: Dict[str, Dict[str, Any]] = {}
    for inp in inputs:
        # Tier 1 (primary facts) -> Tier 2 (appendix) -> Tier 3 (providers)
        primary = primary_facts.get(inp)
        if isinstance(primary, dict) and primary.get("value") is not None:
            resolved[inp] = dict(primary)
            resolved[inp].setdefault("provenance_tier", PROVENANCE_TIER.DOCUMENT)
            continue
        appendix = _resolve_appendix(inp, workspace_documents)
        if appendix is not None and appendix.get("value") is not None:
            resolved[inp] = appendix
            continue
        external = _resolve_tier3_input(
            inp, company_context, reporting_period, providers
        )
        if external is None:
            return None  # every input must be verified or we do NOT calculate
        resolved[inp] = external

    calc_input = {k: {"value": f["value"]} for k, f in resolved.items()}
    try:
        calculated = calculate_financial_ratios(calc_input)
    except Exception:
        return None
    cfact = calculated.get(metric)
    if not isinstance(cfact, dict) or cfact.get("value") is None:
        return None
    out = dict(cfact)
    out["metric"] = metric
    out["provenance_tier"] = PROVENANCE_TIER.EXTERNAL_DERIVED
    out["note"] = (
        "Not disclosed in the primary uploaded document. "
        "Calculated from externally verified inputs."
    )
    out["input_provenance"] = {
        k: (f.get("provenance_tier") or "DOCUMENT") for k, f in resolved.items()
    }
    return out


# ---------------------------------------------------------------
# Centralized resolver
# ---------------------------------------------------------------

def resolve_metric(
    metric: str,
    company_context: Optional[Dict[str, Any]] = None,
    reporting_period: Optional[str] = None,
    workspace_documents: Optional[List[Dict[str, Any]]] = None,
    primary_facts: Optional[Dict[str, Any]] = None,
    providers: Optional[List[ExternalEvidenceProvider]] = None,
) -> Dict[str, Any]:
    """Resolve ONE metric through the strict hierarchy:

        Tier 1: primary uploaded document (already-present verified fact)
        Tier 2: user-uploaded workspace appendices
        Tier 3: approved structured/regulatory providers
                + EXTERNAL_DERIVED (ratio from externally verified inputs)
        else:   BLOCKED

    Stops at the first defensible result; never falls through to lower
    tiers after a valid higher-tier fact. Never consults random web,
    search, blog or scraped sources.
    """
    company_context = company_context or {}
    workspace_documents = workspace_documents or []
    primary_facts = primary_facts or {}
    providers = providers if providers is not None else DEFAULT_PROVIDERS

    # --- Tier 1: primary uploaded document (existing pipeline fact wins)
    fact = primary_facts.get(metric)
    if isinstance(fact, dict) and fact.get("value") is not None:
        out = dict(fact)
        out["metric"] = metric
        out.setdefault(
            "provenance_tier",
            PROVENANCE_TIER.DERIVED
            if str(fact.get("source")) == "Calculated"
            else PROVENANCE_TIER.DOCUMENT,
        )
        return out

    # --- Tier 2: user-uploaded appendices (same workspace/company context)
    appendix = _resolve_appendix(metric, workspace_documents)
    if appendix is not None:
        return appendix

    # --- Tier 3: approved providers (direct fact)
    external, ext_reason = _resolve_tier3_direct(
        metric, company_context, reporting_period, providers
    )
    if external is not None:
        return external

    # --- Tier 3: EXTERNAL_DERIVED (ratio from externally verified inputs)
    derived = _resolve_external_derived(
        metric, company_context, reporting_period,
        workspace_documents, primary_facts, providers,
    )
    if derived is not None:
        return derived

    # --- BLOCKED: no defensible source. Prefer the specific Tier 3
        #     validation rejection (e.g. "company identifier mismatch") over
        #     the generic reason so the evidence card stays honest.
    return {
        "metric": metric,
        "value": None,
        "provenance_tier": PROVENANCE_TIER.BLOCKED,
        "reason": ext_reason or BLOCKED_REASON,
    }


def recover_missing_metrics(
    module3_result: Dict[str, Any],
    company_context: Optional[Dict[str, Any]] = None,
    reporting_period: Optional[str] = None,
    workspace_documents: Optional[List[Dict[str, Any]]] = None,
    providers: Optional[List[ExternalEvidenceProvider]] = None,
) -> Dict[str, Any]:
    """Centralized integration point: for every grid metric the pipeline
    reports as missing, run resolve_metric() and inject recovered facts
    back into the SAME financial_data / ratios records (the existing
    pipeline fact remains the source of truth - no second fact system).

    Records the outcome under module3_result["external_evidence"]:
        {"attempted": [...], "recovered": {metric: tier}, "blocked": {metric: reason}}

    Deterministic and idempotent (already-recovered facts are found by
    Tier 1 on later runs). Never commits or stores raw documents.
    """
    if not isinstance(module3_result, dict):
        return module3_result
    company_context = company_context or {}
    workspace_documents = workspace_documents or []
    providers = providers if providers is not None else DEFAULT_PROVIDERS

    financial_data = dict(module3_result.get("financial_data") or {})
    ratios = dict(module3_result.get("ratios") or {})
    combined_facts = {**financial_data, **ratios}

    attempted: List[str] = []
    recovered: Dict[str, str] = {}
    blocked: Dict[str, str] = {}

    for metric in GRID_METRICS:
        if metric in financial_data or metric in ratios:
            continue  # already established by the pipeline (Tier 1 stands)
        attempted.append(metric)
        resolved = resolve_metric(
            metric,
            company_context=company_context,
            reporting_period=reporting_period,
            workspace_documents=workspace_documents,
            primary_facts=combined_facts,
            providers=providers,
        )
        if resolved.get("value") is not None:
            target = ratios if metric in RATIO_KEYS else financial_data
            target[metric] = resolved
            recovered[metric] = resolved.get("provenance_tier") or "DOCUMENT"
        else:
            blocked[metric] = (
                resolved.get("reason") or BLOCKED_REASON
            )

    module3_result["financial_data"] = financial_data
    module3_result["ratios"] = ratios
    module3_result["external_evidence"] = {
        "attempted": attempted,
        "recovered": recovered,
        "blocked": blocked,
    }
    return module3_result
