"""
Financial Timeline Engine
Sprint 7 - Deterministic Formula Doer / Formula Engine

A centralized, deterministic financial calculation layer.

The LLM/AI provider NEVER performs the arithmetic. Every metric flows:

    user asks for metric
      -> Formula Registry
      -> required-input resolver
      -> existing verified fact graph
      -> Sprint 6.5 evidence recovery (only when an input is missing)
      -> deterministic calculation (Decimal arithmetic)
      -> validation
      -> auditable calculation lineage

Rules
-----
- Only formulas whose required inputs/definitions are already supported by
  the existing pipeline are registered. No invented formulas.
- The engine NEVER guesses a value, NEVER substitutes a similar metric,
  NEVER silently converts currency/scale, NEVER uses another reporting
  period, and NEVER calls an LLM. If a required input cannot be verified
  through the permitted hierarchy, the metric stays BLOCKED.
- If a metric is already a verified reported fact, the existing fact is
  returned without recalculation.
- Inputs missing from the verified fact graph are recovered ONLY through
  backend.evidence_resolver.resolve_metric() (Document -> Appendix ->
  Approved Regulatory API -> BLOCKED). No second source-resolution system.
- Existing pipeline facts remain the source of truth; the engine result is
  a structured, auditable calculation record (status, formula, inputs,
  steps, provenance, reason).
- Arithmetic uses Decimal to avoid binary floating-point surprises. The
  underlying result is kept at full deterministic precision; display
  rounding follows the per-formula precision policy.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from backend.financial_calculator import FinancialCalculator
from backend.evidence_resolver import (
    DEFAULT_PROVIDERS,
    PROVENANCE_TIER,
    resolve_metric,
)
from backend.formula_engine_cpp import cpp_calculate

# Sprint 12A - optional delegation to the deterministic maths engine
# (backend/maths). Additive only: the legacy formulas and every existing
# behavior below are untouched. The import is guarded so a maths-engine
# problem can never break the existing Formula Engine.
try:  # pragma: no cover - defensive import guard
    from backend.maths.adapter import (
        can_solve_with_graph,
        calculate_with_graph,
    )
    _MATHS_DELEGATION_AVAILABLE = True
except Exception:  # pragma: no cover
    _MATHS_DELEGATION_AVAILABLE = False


# ---------------------------------------------------------------
# Status model (adapted from the existing FT-E classification)
# ---------------------------------------------------------------

STATUS_REPORTED = "reported"
STATUS_DERIVED = "derived"
STATUS_EXTERNAL_DERIVED = "external_derived"
STATUS_BLOCKED = "blocked"
STATUS_UNANALYZED = "unanalyzed"

STATUS_LABELS = {
    STATUS_REPORTED: "🟢 Reported & Verified",
    STATUS_DERIVED: "🔵 Derived from Verified Inputs",
    STATUS_EXTERNAL_DERIVED: "🟣 External + Derived",
    STATUS_BLOCKED: "🔴 Blocked",
    STATUS_UNANALYZED: "⚪ Unanalyzed",
}


# ---------------------------------------------------------------
# Deterministic formula registry
# ---------------------------------------------------------------

def _d(value: Any) -> Optional[Decimal]:
    """Strict numeric conversion to Decimal. Never coerces labels,
    ranges, booleans or None. Commas are the only tolerated decoration."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        s = str(value).strip().replace(",", "")
        if not s or not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)", s):
            return None
        return Decimal(s)
    except (InvalidOperation, ValueError, TypeError):
        return None


class _FormulaDef:
    """One immutable, deterministic formula definition."""

    def __init__(self, key, display_name, required_inputs, formula, kind,
                 precision, fn, period_mode, denominator_inputs=None,
                 aliases=None):
        self.key = key
        self.display_name = display_name
        self.required_inputs = list(required_inputs)
        self.formula = formula
        self.kind = kind                      # "percent" | "ratio"
        self.precision = precision            # display rounding digits
        self.fn = fn                          # dict[key] -> Decimal value
        self.period_mode = period_mode        # "same" | "different" | "span"
        self.denominator_inputs = list(denominator_inputs or [])
        self.aliases = list(aliases or [])

    def to_metadata(self) -> dict:
        return {
            "metric_key": self.key,
            "display_name": self.display_name,
            "required_inputs": list(self.required_inputs),
            "formula": self.formula,
            "unit": self.kind,
            "precision": self.precision,
            "period_requirement": self.period_mode,
            "denominator_inputs": list(self.denominator_inputs),
            "aliases": list(self.aliases),
        }


def _fmt_percent(v: Decimal) -> Decimal:
    """Percent-kind formulas produce the percentage NUMBER (e.g. 36.61)."""
    return v * Decimal(100)


def _div(num_key, den_key):
    def fn(values: Dict[str, Decimal]) -> Decimal:
        return values[num_key] / values[den_key]
    return fn


FORMULA_REGISTRY: Dict[str, _FormulaDef] = {
    "ROE": _FormulaDef(
        "ROE", "ROE", ["Net Profit", "Equity"],
        "Net Profit ÷ Equity × 100", "percent", 2, _div("Net Profit", "Equity"),
        "same", denominator_inputs=["Equity"], aliases=["roe"],
    ),
    "ROA": _FormulaDef(
        "ROA", "ROA", ["Net Profit", "Assets"],
        "Net Profit ÷ Assets × 100", "percent", 2, _div("Net Profit", "Assets"),
        "same", denominator_inputs=["Assets"], aliases=["roa"],
    ),
    "Profit Margin": _FormulaDef(
        "Profit Margin", "Profit Margin", ["Net Profit", "Revenue"],
        "Net Profit ÷ Revenue × 100", "percent", 2,
        _div("Net Profit", "Revenue"), "same",
        denominator_inputs=["Revenue"], aliases=["profit margin", "net margin"],
    ),
    "Operating Margin": _FormulaDef(
        "Operating Margin", "Operating Margin", ["Operating Profit", "Revenue"],
        "Operating Profit ÷ Revenue × 100", "percent", 2,
        _div("Operating Profit", "Revenue"), "same",
        denominator_inputs=["Revenue"], aliases=["operating margin"],
    ),
    "Current Ratio": _FormulaDef(
        "Current Ratio", "Current Ratio",
        ["Current Assets", "Current Liabilities"],
        "Current Assets ÷ Current Liabilities", "ratio", 2,
        _div("Current Assets", "Current Liabilities"), "same",
        denominator_inputs=["Current Liabilities"],
        aliases=["current ratio"],
    ),
    "Debt to Equity": _FormulaDef(
        "Debt to Equity", "Debt to Equity", ["Debt", "Equity"],
        "Debt ÷ Equity", "ratio", 2, _div("Debt", "Equity"), "same",
        denominator_inputs=["Equity"], aliases=["debt to equity", "debt/equity"],
    ),
    "Revenue Growth": _FormulaDef(
        "Revenue Growth", "Revenue Growth", ["Revenue", "Previous Revenue"],
        "(Revenue − Previous Revenue) ÷ Previous Revenue × 100", "percent", 2,
        lambda v: _div("Revenue", "Previous Revenue")(v) - Decimal(1),
        "different", denominator_inputs=["Previous Revenue"],
        aliases=["revenue growth", "sales growth"],
    ),
    "EPS Growth": _FormulaDef(
        "EPS Growth", "EPS Growth", ["EPS", "Previous EPS"],
        "(EPS − Previous EPS) ÷ Previous EPS × 100", "percent", 2,
        lambda v: _div("EPS", "Previous EPS")(v) - Decimal(1),
        "different", denominator_inputs=["Previous EPS"],
        aliases=["eps growth", "earnings per share growth"],
    ),
    "CAGR": _FormulaDef(
        "CAGR", "CAGR", ["CAGR Beginning Value", "CAGR Ending Value"],
        "(Ending ÷ Beginning) ^ (1 ÷ n) − 1", "percent", 2,
        None,  # computed in _calculate with the period span n
        "span", denominator_inputs=["CAGR Beginning Value"],
        aliases=["cagr"],
    ),
}

SUPPORTED_FORMULAS: List[str] = [
    k for k, v in FORMULA_REGISTRY.items()
]


def registry_lookup(metric: Optional[str]) -> Optional[_FormulaDef]:
    if not metric:
        return None
    return FORMULA_REGISTRY.get(metric)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _fact_tier(fact: Optional[Dict[str, Any]]) -> str:
    if not isinstance(fact, dict):
        return PROVENANCE_TIER.UNANALYZED
    tier = fact.get("provenance_tier")
    if tier:
        return str(tier)
    if str(fact.get("source")) == "Calculated":
        return PROVENANCE_TIER.DERIVED
    if fact.get("value") is not None:
        return PROVENANCE_TIER.DOCUMENT
    return PROVENANCE_TIER.UNANALYZED


def _currency_of(fact: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(fact, dict):
        return None
    for k in ("currency_code", "currency", "unit"):
        v = fact.get(k)
        if v not in (None, ""):
            return str(v).strip().upper()
    return None


_SCALE_SYNONYMS = {
    "unit": "unit", "units": "unit",
    "thousand": "thousands", "thousands": "thousands", "k": "thousands",
    "million": "millions", "millions": "millions", "m": "millions",
    "billion": "billions", "billions": "billions", "b": "billions",
    "crore": "crores", "crores": "crores",
}


def _scale_of(fact: Optional[Dict[str, Any]]) -> Optional[str]:
    """Canonical scale label ('B' == 'billions' == 'billion'). Unknown
    labels are compared verbatim - nothing is converted, only synonyms of
    the same scale are normalized so a real mismatch still blocks."""
    if not isinstance(fact, dict):
        return None
    v = fact.get("scale")
    if v in (None, ""):
        return None
    return _SCALE_SYNONYMS.get(str(v).strip().lower(), str(v).strip().lower())


def _period_of(fact: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(fact, dict):
        return None
    for k in ("reporting_period", "period"):
        v = fact.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def _blocked_result(metric, reason, reg=None):
    """Structured BLOCKED result — never a guessed value."""
    return {
        "metric_key": metric,
        "display_name": (reg.display_name if reg else metric),
        "value": None,
        "display_value": "—",
        "unit": (reg.kind if reg else "—"),
        "status": STATUS_BLOCKED,
        "status_label": STATUS_LABELS[STATUS_BLOCKED],
        "formula": (reg.formula if reg else "—"),
        "inputs": [],
        "input_keys": (reg.required_inputs if reg else []),
        "calculation_steps": [],
        "provenance": PROVENANCE_TIER.BLOCKED,
        "reason": reason,
        "error": reason,
    }


def _display_value(value: Decimal, kind: str, precision: int) -> str:
    """Display rounding policy — the underlying value keeps full precision."""
    q = Decimal(1).scaleb(-precision)
    try:
        rounded = value.quantize(q)
    except InvalidOperation:
        rounded = value
    num = format(rounded, "f")
    if kind == "percent":
        return f"{num}%"
    return num


def _validate_inputs(reg, resolved):
    """Deterministic validation. Returns (ok, reason, error_key).

    Checks (in order): every input present/numeric (guaranteed before
    this call), denominator != 0, currency compatibility, scale
    compatibility, and period requirements. Never silently converts.
    """
    dec = {k: _d(v.get("value")) for k, v in resolved.items()}
    for k in reg.required_inputs:
        if dec.get(k) is None:
            return False, f"{k} is unavailable from permitted evidence sources.", "MISSING_INPUT"
    # Denominator guard
    for dk in reg.denominator_inputs:
        if dec.get(dk) == 0:
            return False, f"{dk} is zero — {reg.display_name} cannot be calculated.", "ZERO_DENOMINATOR"
    # Currency compatibility (reject incompatible; no silent conversion)
    curs = {c for k in reg.required_inputs if (c := _currency_of(resolved.get(k)))}
    if len(curs) > 1:
        return False, f"Currency mismatch between inputs ({', '.join(sorted(curs))}).", "CURRENCY_MISMATCH"
    # Scale compatibility (reject incompatible scales)
    scales = {s for k in reg.required_inputs if (s := _scale_of(resolved.get(k)))}
    if len(scales) > 1:
        return False, f"Scale mismatch between inputs ({', '.join(sorted(scales))}).", "SCALE_MISMATCH"
    # Period requirements
    if reg.period_mode == "same":
        periods = {p for k in reg.required_inputs if (p := _period_of(resolved.get(k)))}
        if len(periods) > 1:
            return False, f"Incompatible reporting periods for {reg.display_name} ({', '.join(sorted(periods))}).", "PERIOD_MISMATCH"
    elif reg.period_mode == "different":
        periods = [_period_of(resolved.get(k)) for k in reg.required_inputs]
        if periods[0] and periods[1] and periods[0] == periods[1]:
            return False, f"{reg.display_name} needs two different reporting periods (both are {periods[0]}).", "PERIOD_MISMATCH"
    return True, "", ""


def _cagr_span(resolved) -> Optional[int]:
    """n = end_year - begin_year from period metadata. None when unusable."""
    begin = resolved.get("CAGR Beginning Value") or {}
    end = resolved.get("CAGR Ending Value") or {}
    by = FinancialCalculator._period_year(begin.get("reporting_period"))
    ey = FinancialCalculator._period_year(end.get("reporting_period"))
    if by is None or ey is None:
        return None
    n = ey - by
    return n if n >= 1 else None


def _compute(reg, resolved, n=None):
    """Deterministic Decimal arithmetic. Returns (value_decimal, steps)."""
    dec = {k: _d(v.get("value")) for k, v in resolved.items()}
    if reg.key == "CAGR":
        if n is None:
            raise ValueError("CAGR period span (n) could not be determined.")
        bv, ev = dec["CAGR Beginning Value"], dec["CAGR Ending Value"]
        if bv <= 0 or ev <= 0:
            raise ValueError("CAGR requires positive beginning and ending values.")
        raw = (ev / bv) ** (Decimal(1) / Decimal(n)) - Decimal(1)
        value = _fmt_percent(raw)
        steps = [
            f"CAGR = (Ending ÷ Beginning) ^ (1 ÷ n) − 1",
            f"n = {n} year(s)",
            f"CAGR = ({ev} ÷ {bv}) ^ (1 ÷ {n}) − 1 = {_display_value(value, reg.kind, reg.precision)}",
        ]
        return value, steps
    value = reg.fn(dec)
    if reg.kind == "percent":
        value = _fmt_percent(value)
        steps = [
            f"{reg.display_name} = {reg.formula}",
            f"{reg.display_name} = {_display_value(value, reg.kind, reg.precision)}",
        ]
    else:
        steps = [
            f"{reg.display_name} = {reg.formula}",
            f"{reg.display_name} = {_display_value(value, reg.kind, reg.precision)}",
        ]
    return value, steps


def _input_detail(fact, key):
    """One input row for the lineage — real fields only, '—' otherwise."""
    tier = _fact_tier(fact)
    value = _d(fact.get("value")) if isinstance(fact, dict) else None
    return {
        "metric": key,
        "value": float(value) if value is not None else None,
        "display_value": _display_value(value, "ratio", 2) if value is not None else "—",
        "provenance_tier": tier,
        "tier_label": PROVENANCE_TIER_LABELS.get(tier, tier),
        "page": fact.get("page") if isinstance(fact, dict) else None,
        "source": fact.get("source") if isinstance(fact, dict) else None,
        "evidence": fact.get("evidence") if isinstance(fact, dict) else None,
        "unit": _currency_of(fact) or None,
        "scale": _scale_of(fact) or None,
        "status": "Verified",
    }


PROVENANCE_TIER_LABELS = {
    PROVENANCE_TIER.DOCUMENT: "Document",
    PROVENANCE_TIER.APPENDIX: "Appendix",
    PROVENANCE_TIER.REGULATORY_API: "Regulatory API",
    PROVENANCE_TIER.DERIVED: "Derived",
    PROVENANCE_TIER.EXTERNAL_DERIVED: "External + Derived",
    PROVENANCE_TIER.BLOCKED: "Blocked",
    PROVENANCE_TIER.UNANALYZED: "Unanalyzed",
}


def _lineage_text(reg, result):
    """Human-readable calculation lineage tree (auditable)."""
    lines = [f"{reg.display_name}"]
    lines.append(f"├── Formula: {reg.formula}")
    for it in result.get("inputs") or []:
        lines.append(f"├── {it['metric']}")
        lines.append(f"│   ├── Value: {it.get('display_value')}")
        lines.append(f"│   ├── Provenance: {it.get('provenance_tier')}")
        if it.get("page"):
            lines.append(f"│   ├── Page: {it.get('page')}")
        if it.get("evidence"):
            lines.append(f"│   └── Evidence: {it.get('evidence')}")
    lines.append(f"└── Result: {result.get('display_value')}")
    return "\n".join(lines)


def calculate_metric(
    metric_key: str,
    financial_data: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministically resolve + calculate ONE metric.

    `financial_data` is the existing pipeline fact map (source of truth).
    `context` may carry: reporting_period, company_context,
    workspace_documents, providers, primary_facts (extra verified facts,
    e.g. ratios), and recover (default True — allow Sprint 6.5 recovery
    of missing inputs).

    Returns a structured result: metric_key, display_name, value (full
    precision), display_value, unit, status, status_label, formula,
    inputs (per-input provenance), input_keys, calculation_steps,
    provenance, lineage, reason/error. Never an LLM call.
    """
    context = context or {}
    financial_data = dict(financial_data or {})
    extra = dict(context.get("primary_facts") or {})
    primary_facts = {**extra, **financial_data}
    reporting_period = context.get("reporting_period")
    company_context = context.get("company_context") or {}
    workspace_documents = context.get("workspace_documents") or []
    providers = (
        context["providers"]
        if context.get("providers") is not None
        else DEFAULT_PROVIDERS
    )
    recover = context.get("recover", True)

    reg = registry_lookup(metric_key)

    # -----------------------------------------------------------
    # Step 1 — already a verified REPORTED fact? Return it untouched.
    # -----------------------------------------------------------
    reported = financial_data.get(metric_key)
    if (
        isinstance(reported, dict)
        and _d(reported.get("value")) is not None
        and str(reported.get("source")) != "Calculated"
    ):
        tier = _fact_tier(reported)
        return {
            "metric_key": metric_key,
            "display_name": metric_key,
            "value": float(_d(reported.get("value"))),
            "display_value": _display_value(_d(reported.get("value")), "ratio", 4),
            "unit": "—",
            "status": STATUS_REPORTED,
            "status_label": STATUS_LABELS[STATUS_REPORTED],
            "formula": "—",
            "inputs": [_input_detail(reported, metric_key)],
            "input_keys": [],
            "calculation_steps": [
                f"{metric_key} is a verified reported fact — returned without recalculation."
            ],
            "provenance": tier,
            "reason": None,
            "error": None,
            "lineage": f"{metric_key} — Reported & Verified ({tier})",
        }

    # -----------------------------------------------------------
    # Step 2 — formula lookup; unsupported metrics stay UNANALYZED.
    # Sprint 12A: NEW-registry concepts explicitly registered with the
    # maths engine are delegated to the deterministic graph engine
    # (additive - the existing 9 formulas and the UNANALYZED behavior for
    # every unregistered metric are unchanged).
    # -----------------------------------------------------------
    if reg is None:
        if _MATHS_DELEGATION_AVAILABLE and can_solve_with_graph(metric_key):
            delegated = calculate_with_graph(
                metric_key, financial_data, context
            )
            if delegated is not None:
                return delegated
        return {
            "metric_key": metric_key,
            "display_name": metric_key,
            "value": None,
            "display_value": "—",
            "unit": "—",
            "status": STATUS_UNANALYZED,
            "status_label": STATUS_LABELS[STATUS_UNANALYZED],
            "formula": "—",
            "inputs": [],
            "input_keys": [],
            "calculation_steps": [],
            "provenance": PROVENANCE_TIER.UNANALYZED,
            "reason": "No supported formula or analysis exists for this metric.",
            "error": "UNSUPPORTED",
        }

    # -----------------------------------------------------------
    # Step 3 — gather verified inputs from the fact graph.
    # -----------------------------------------------------------
    resolved: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for inp in reg.required_inputs:
        f = financial_data.get(inp) or primary_facts.get(inp)
        if isinstance(f, dict) and _d(f.get("value")) is not None \
                and _fact_tier(f) != PROVENANCE_TIER.BLOCKED:
            resolved[inp] = dict(f)
        else:
            missing.append(inp)

    # -----------------------------------------------------------
    # Step 4 — recover missing inputs ONLY via the Sprint 6.5 resolver
    #          (Document -> Appendix -> Approved Regulatory API -> BLOCKED).
    # -----------------------------------------------------------
    block_reasons: List[str] = []
    if missing and recover:
        for inp in missing:
            recovered = resolve_metric(
                inp,
                company_context=company_context,
                reporting_period=reporting_period,
                workspace_documents=workspace_documents,
                primary_facts=primary_facts,
                providers=providers,
            )
            if isinstance(recovered, dict) and _d(recovered.get("value")) is not None:
                resolved[inp] = recovered
            else:
                block_reasons.append(
                    f"{inp} is unavailable from permitted evidence sources."
                )
    elif missing:
        for inp in missing:
            block_reasons.append(
                f"{inp} is unavailable from permitted evidence sources."
            )

    # -----------------------------------------------------------
    # Step 5 — validation; any failure = BLOCKED with a precise reason.
    # -----------------------------------------------------------
    if block_reasons:
        return _blocked_result(metric_key, " ".join(dict.fromkeys(block_reasons)), reg)
    if any(_d(f.get("value")) is None for f in resolved.values()):
        return _blocked_result(metric_key, "Required input is not numeric.", reg)

    # -----------------------------------------------------------
    # Step 5b - Sprint 7 C++ engine: deterministic arithmetic,
    # validation and lineage. Falls back to the Python Decimal path
    # when the compiled binary is unavailable.
    # -----------------------------------------------------------
    cpp = cpp_calculate(metric_key, resolved)
    if cpp is not None:
        if cpp["status"] == STATUS_BLOCKED:
            return _blocked_result(
                metric_key,
                cpp["block_reason"] or "Required input cannot be validated.",
                reg,
            )
        if cpp["status"] in (STATUS_DERIVED, STATUS_EXTERNAL_DERIVED) \
                and cpp.get("value") is not None:
            inputs_detail = [_input_detail(resolved[k], k) for k in reg.required_inputs]
            input_tiers = {it["provenance_tier"] for it in inputs_detail}
            external = bool(
                input_tiers & {PROVENANCE_TIER.REGULATORY_API, PROVENANCE_TIER.APPENDIX}
            )
            provenance = (
                PROVENANCE_TIER.EXTERNAL_DERIVED if external
                else PROVENANCE_TIER.DERIVED
            )
            status = STATUS_EXTERNAL_DERIVED if external else STATUS_DERIVED
            raw_value = cpp["value"]
            if reg.kind == "percent":
                raw_value = raw_value * 100  # C++ returns the fraction
            result = {
                "metric_key": metric_key,
                "display_name": reg.display_name,
                "value": float(raw_value),
                "display_value": cpp["display_value"],
                "unit": reg.kind,
                "status": status,
                "status_label": STATUS_LABELS[status],
                "formula": reg.formula,
                "inputs": inputs_detail,
                "input_keys": list(reg.required_inputs),
                "calculation_steps": [
                    f"{it['metric']} = {it['display_value']} ({it['provenance_tier']})"
                    for it in inputs_detail
                ] + list(cpp["steps"]),
                "provenance": provenance,
                "reason": None,
                "error": None,
                "lineage": cpp["lineage"],
            }
            if external:
                result["note"] = (
                    "One or more inputs were recovered through the approved external "
                    "evidence hierarchy — not disclosed in the primary uploaded document."
                )
            return result
        # Unexpected C++ status (e.g. unanalyzed) -> fall through to Python.
    ok, reason, _ek = _validate_inputs(reg, resolved)
    if not ok:
        return _blocked_result(metric_key, reason, reg)
    n = None
    if reg.key == "CAGR":
        n = _cagr_span(resolved)
        if n is None:
            return _blocked_result(
                metric_key,
                "CAGR period span cannot be determined from input reporting periods.",
                reg,
            )

    # -----------------------------------------------------------
    # Step 6+7 — deterministic Decimal arithmetic + lineage.
    # -----------------------------------------------------------
    try:
        value, steps = _compute(reg, resolved, n=n)
    except (ZeroDivisionError, InvalidOperation, ValueError, ArithmeticError) as exc:
        return _blocked_result(
            metric_key,
            f"Calculation blocked: {exc}",
            reg,
        )

    inputs_detail = [_input_detail(resolved[k], k) for k in reg.required_inputs]
    input_tiers = {it["provenance_tier"] for it in inputs_detail}
    external = bool(input_tiers & {PROVENANCE_TIER.REGULATORY_API, PROVENANCE_TIER.APPENDIX})
    provenance = (
        PROVENANCE_TIER.EXTERNAL_DERIVED if external else PROVENANCE_TIER.DERIVED
    )
    status = STATUS_EXTERNAL_DERIVED if external else STATUS_DERIVED
    result = {
        "metric_key": metric_key,
        "display_name": reg.display_name,
        "value": float(value),
        "display_value": _display_value(value, reg.kind, reg.precision),
        "unit": reg.kind,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "formula": reg.formula,
        "inputs": inputs_detail,
        "input_keys": list(reg.required_inputs),
        "calculation_steps": [
            f"{it['metric']} = {it['display_value']} ({it['provenance_tier']})"
            for it in inputs_detail
        ] + steps,
        "provenance": provenance,
        "reason": None,
        "error": None,
        "lineage": _lineage_text(reg, {
            "inputs": inputs_detail,
            "display_value": _display_value(value, reg.kind, reg.precision),
        }),
    }
    if external:
        result["note"] = (
            "One or more inputs were recovered through the approved external "
            "evidence hierarchy — not disclosed in the primary uploaded document."
        )
    return result


def calculate_metric_many(
    metric_keys: List[str],
    financial_data: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Convenience: run calculate_metric for several keys. Deterministic."""
    return {
        k: calculate_metric(k, financial_data, context) for k in metric_keys
    }
