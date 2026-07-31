"""
Currency Validator

Tracks currency per fact (not merely per company) and prevents calculations
that combine incompatible currencies.

Currency Roles:
    REPORTING:      The currency in which financial statements are presented
    FUNCTIONAL:     The primary currency of the business environment
    PRESENTATION:   The currency used for presenting financial results
    TRANSACTION:    The currency of a specific transaction
    TAX:            The currency used for tax reporting

Rules:
    - Facts with different currency_code values and different currency_roles
      cannot be combined without explicit FX conversion metadata.
    - Facts with the same currency_code are always compatible.
    - Facts with different currency_codes but the same role can be compared
      only if FX metadata is present.
    - EUR revenue ÷ USD income must return CURRENCY_MISMATCH.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("fte.rag.currency_validator")

# ---------------------------------------------------------------------------
# Currency role constants
# ---------------------------------------------------------------------------

REPORTING = "REPORTING"
FUNCTIONAL = "FUNCTIONAL"
PRESENTATION = "PRESENTATION"
TRANSACTION = "TRANSACTION"
TAX = "TAX"

ALL_ROLES = {REPORTING, FUNCTIONAL, PRESENTATION, TRANSACTION, TAX}

# ---------------------------------------------------------------------------
# FX metadata validation states (Fix #5)
# ---------------------------------------------------------------------------

INVALID_FX_METADATA = "INVALID_FX_METADATA"
FX_METADATA_VALID = "FX_METADATA_VALID"
FX_FRESHNESS_UNCONFIGURED = "FRESHNESS_UNCONFIGURED"
FX_STALE = "FX_STALE"
FX_FRESH = "FX_FRESH"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CurrencyInfo:
    """Currency metadata for a financial fact."""

    currency_code: str = ""
    currency_role: str = REPORTING
    fx_rate: Optional[float] = None
    fx_source: str = ""
    fx_timestamp: Optional[datetime] = None

    def is_compatible_with(self, other: CurrencyInfo) -> bool:
        """Check if this currency is compatible with another for calculations.

        Compatible means:
        - Same currency_code, OR
        - Different currency_code but same role AND explicit FX metadata exists
        """
        if not self.currency_code and not other.currency_code:
            return True  # Both unspecified — assume compatible
        if not self.currency_code or not other.currency_code:
            return False  # One unspecified, one specified — incompatible
        if self.currency_code.upper() == other.currency_code.upper():
            return True  # Same currency (role differences are NOT a conflict)
        # Different currencies — same role AND complete valid FX metadata
        # (rate + source + timestamp) on BOTH facts (Fix #5 Cases C/D/E/F)
        if self.currency_role != other.currency_role:
            return False  # Different roles — never compatible
        ok_self, _, _ = CurrencyValidator.validate_fx_metadata(self)
        ok_other, _, _ = CurrencyValidator.validate_fx_metadata(other)
        return ok_self and ok_other

    def __str__(self) -> str:
        parts = [self.currency_code]
        if self.currency_role and self.currency_role != REPORTING:
            parts.append(f"[{self.currency_role}]")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Currency Validator
# ---------------------------------------------------------------------------


class CurrencyValidator:
    """
    Validates currency compatibility across financial facts.

    Prevents calculations that silently mix incompatible currencies.
    """

    @staticmethod
    def validate_fact_currency(fact: Dict[str, Any]) -> CurrencyInfo:
        """Extract and validate currency info from a fact dict.

        Args:
            fact: A fact dict with optional keys:
                - currency_code, currency_role, fx_rate, fx_source, fx_timestamp

        Returns:
            CurrencyInfo with the parsed information
        """
        role = fact.get("currency_role", REPORTING)
        if role not in ALL_ROLES:
            role = REPORTING

        fx_ts = fact.get("fx_timestamp")
        if isinstance(fx_ts, str):
            try:
                fx_ts = datetime.fromisoformat(fx_ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                fx_ts = None

        return CurrencyInfo(
            currency_code=fact.get("currency_code", ""),
            currency_role=role,
            fx_rate=fact.get("fx_rate"),
            fx_source=fact.get("fx_source", ""),
            fx_timestamp=fx_ts,
        )

    # ------------------------------------------------------------------
    # Fix #5 — deterministic FX metadata validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_fx_rate(rate: Any) -> Tuple[bool, Optional[str]]:
        """Validate an FX rate value deterministically (Case F).

        Rejects: None, zero, negative, NaN, infinity, non-numeric values.
        """
        if rate is None:
            return (False, f"{INVALID_FX_METADATA}: missing fx_rate")
        try:
            parsed = float(rate)
        except (ValueError, TypeError):
            return (False, f"{INVALID_FX_METADATA}: fx_rate is not numeric")
        if parsed <= 0:
            return (False, f"{INVALID_FX_METADATA}: fx_rate must be positive")
        if math.isnan(parsed) or math.isinf(parsed):
            return (False, f"{INVALID_FX_METADATA}: fx_rate must be finite")
        return (True, None)

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def validate_fx_metadata(info: CurrencyInfo) -> Tuple[bool, str, str]:
        """Validate complete FX conversion metadata (Cases C/D/E/F).

        Conversion is only permitted when the rate is a valid positive
        finite number AND the source is present AND the timestamp is
        present and parseable. Returns (valid, state, reason).
        """
        if info.fx_rate is None:
            return (False, INVALID_FX_METADATA,
                    f"{INVALID_FX_METADATA}: missing fx_rate")
        ok, reason = CurrencyValidator.validate_fx_rate(info.fx_rate)
        if not ok:
            return (False, INVALID_FX_METADATA, reason)
        if not info.fx_source or not str(info.fx_source).strip():
            return (False, INVALID_FX_METADATA,
                    f"{INVALID_FX_METADATA}: missing fx_source")
        if CurrencyValidator._parse_timestamp(info.fx_timestamp) is None:
            return (False, INVALID_FX_METADATA,
                    f"{INVALID_FX_METADATA}: missing fx_timestamp")
        return (True, FX_METADATA_VALID, "")

    @staticmethod
    def _has_any_fx_metadata(info: CurrencyInfo) -> bool:
        """True if any FX metadata field is present (rate/source/timestamp).

        Facts with NO FX metadata at all are a plain CURRENCY_MISMATCH
        (Case B — different currencies, no FX conversion). Facts with
        broken/incomplete FX metadata are INVALID_FX_METADATA
        (Cases D/E/F).
        """
        return (
            info.fx_rate is not None
            or bool(info.fx_source and str(info.fx_source).strip())
            or info.fx_timestamp is not None
        )

    @staticmethod
    def check_fx_freshness(
        info: CurrencyInfo,
        max_age_seconds: Optional[float] = None,
    ) -> str:
        """Deterministic FX freshness hook (Case G).

        If no freshness policy is configured (max_age_seconds is None) the
        hook returns FRESHNESS_UNCONFIGURED — a deterministic state that
        signals freshness MUST be checked before any conversion is
        performed. No arbitrary production threshold is invented here;
        callers may supply a policy when one exists.
        """
        ts = CurrencyValidator._parse_timestamp(info.fx_timestamp)
        if max_age_seconds is None:
            return FX_FRESHNESS_UNCONFIGURED
        if ts is None:
            return FX_STALE
        age = (datetime.now(ts.tzinfo) - ts) if ts.tzinfo else (datetime.now() - ts)
        return FX_FRESH if age.total_seconds() <= max_age_seconds else FX_STALE

    @staticmethod
    def fx_compatibility_state(
        facts: List[Dict[str, Any]],
    ) -> Tuple[str, Optional[str]]:
        """Return a deterministic currency state for a set of facts.

        One of:
            "COMPATIBLE"           — same currency, or different currencies
                                     with complete valid FX metadata
            "CURRENCY_MISMATCH"    — different currencies without valid FX
                                     conversion metadata, or different roles
            "INVALID_FX_METADATA"  — FX metadata present but broken
                                     (rate/source/timestamp)
        """
        if not facts or len(facts) < 2:
            return ("COMPATIBLE", None)

        currencies = [CurrencyValidator.validate_fact_currency(f) for f in facts]
        main = currencies[0]

        for i, c in enumerate(currencies[1:], 1):
            if not main.currency_code and not c.currency_code:
                continue
            if main.currency_code.upper() == c.currency_code.upper():
                continue  # same currency — role differences are not a conflict
            if main.currency_role != c.currency_role:
                return (
                    "CURRENCY_MISMATCH",
                    f"fact 0 ({main}) vs fact {i} ({c}): different currency roles",
                )
            for label, info in (("fact 0", main), (f"fact {i}", c)):
                if not CurrencyValidator._has_any_fx_metadata(info):
                    return (
                        "CURRENCY_MISMATCH",
                        f"{label} ({info}): different currencies with "
                        f"no FX conversion metadata",
                    )
                ok, _state, reason = CurrencyValidator.validate_fx_metadata(info)
                if not ok:
                    return (INVALID_FX_METADATA, f"{label} ({info}): {reason}")
        return ("COMPATIBLE", None)

    @staticmethod
    def convert_fact(
        fact: Dict[str, Any],
        target_currency: str,
        rate: Optional[float] = None,
        source: str = "",
        timestamp: Any = None,
    ) -> Dict[str, Any]:
        """Explicit FX conversion preserving the original fact (Case 5).

        NEVER called automatically — conversion is always explicit. Returns
        a converted copy that retains the original fact untouched plus full
        conversion metadata (rate, source, timestamp, target currency,
        freshness state) so the converted value is traceable back to the
        original evidence. Raises ValueError on invalid metadata.
        """
        info = CurrencyValidator.validate_fact_currency(fact)
        fx_rate = rate if rate is not None else info.fx_rate
        fx_source = source or info.fx_source
        fx_ts = timestamp if timestamp is not None else info.fx_timestamp

        temp = CurrencyInfo(
            currency_code=info.currency_code,
            currency_role=info.currency_role,
            fx_rate=fx_rate,
            fx_source=fx_source,
            fx_timestamp=CurrencyValidator._parse_timestamp(fx_ts),
        )
        valid, _state, reason = CurrencyValidator.validate_fx_metadata(temp)
        if not valid:
            raise ValueError(reason)

        value = fact.get("value")
        if value is None:
            raise ValueError(f"{INVALID_FX_METADATA}: fact has no numeric value")

        converted_value = value * float(fx_rate)
        freshness = CurrencyValidator.check_fx_freshness(temp)

        converted = dict(fact)
        converted["value"] = converted_value
        converted["normalized_value"] = converted_value
        converted["currency_code"] = target_currency
        converted["fx_rate"] = float(fx_rate)
        converted["fx_source"] = fx_source
        converted["fx_timestamp"] = fx_ts
        converted["fx_conversion"] = {
            "original_value": value,
            "original_currency": info.currency_code,
            "original_role": info.currency_role,
            "rate": float(fx_rate),
            "source": fx_source,
            "timestamp": str(fx_ts) if fx_ts is not None else None,
            "target_currency": target_currency,
            "converted_value": converted_value,
            "freshness_state": freshness,
        }
        converted["original_fact"] = dict(fact)  # audit trail — never destroyed
        return converted

    @staticmethod
    def check_currency_compatibility(
        facts: List[Dict[str, Any]],
    ) -> Tuple[bool, Optional[str]]:
        """Check if a list of facts have compatible currencies.

        Args:
            facts: List of fact dicts with currency information

        Returns:
            Tuple of (compatible, error_message)
            If compatible is True, error_message is None.
            If compatible is False, error_message describes the mismatch.
        """
        if not facts or len(facts) < 2:
            return (True, None)

        currencies = [CurrencyValidator.validate_fact_currency(f) for f in facts]
        main = currencies[0]

        incompatible_pairs = []
        for i, c in enumerate(currencies[1:], 1):
            if not main.is_compatible_with(c):
                incompatible_pairs.append((0, i))
                logger.warning(
                    f"[CurrencyValidator] Incompatible currencies: "
                    f"{main} vs {c} (facts 0 and {i})"
                )

        if incompatible_pairs:
            details = []
            for i, j in incompatible_pairs:
                details.append(
                    f"fact {i} ({currencies[i]}) incompatible with "
                    f"fact {j} ({currencies[j]})"
                )
            return (False, "CURRENCY_MISMATCH: " + "; ".join(details))

        return (True, None)

    @staticmethod
    def check_operation_currency(
        left: Dict[str, Any],
        right: Dict[str, Any],
        operation: str = "divide",
    ) -> Tuple[bool, Optional[str]]:
        """Check currency compatibility for a specific operation.

        Division (e.g., revenue / income) requires both currencies to be
        compatible. Addition/subtraction also requires the same currency.

        Args:
            left: Left operand fact dict
            right: Right operand fact dict
            operation: "add", "subtract", "multiply", "divide"

        Returns:
            Tuple of (compatible, error_message)
        """
        left_ccy = CurrencyValidator.validate_fact_currency(left)
        right_ccy = CurrencyValidator.validate_fact_currency(right)

        if not left_ccy.currency_code and not right_ccy.currency_code:
            return (True, None)  # Both unspecified

        if left_ccy.is_compatible_with(right_ccy):
            return (True, None)

        return (
            False,
            f"CURRENCY_MISMATCH: Cannot {operation} "
            f"{left_ccy} and {right_ccy}",
        )
