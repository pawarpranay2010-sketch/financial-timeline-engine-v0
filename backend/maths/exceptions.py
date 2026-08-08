"""
Financial Timeline Engine
Sprint 12A - Deterministic Maths & Financial Reasoning Engine
backend/maths/exceptions.py

Structured exception hierarchy for the core engine. The engine NEVER
crashes on bad input - every recoverable failure surfaces as a structured
BLOCKED / REVIEW_REQUIRED result. These exceptions exist for the
programmer-facing edges (registration errors, graph construction errors)
and for internal control flow that the solver converts into structured
results with explicit reasons.

Pure module: no Streamlit, no AI, no network. Deterministic.
"""


class MathsEngineError(Exception):
    """Base class for all maths-engine errors."""


class RegistrationError(MathsEngineError):
    """A formula/fact was registered incorrectly (bad expression, missing
    dependency, unknown variable, conflicting duplicate)."""


class UnregisteredFormulaError(MathsEngineError):
    """A formula_id was requested that is not in the registry."""


class UnregisteredConceptError(MathsEngineError):
    """A concept was requested that is neither a known fact nor the target
    (or inverse-solvable variable) of any registered formula."""


class CycleDetectedError(MathsEngineError):
    """The dependency graph contains a cycle; no deterministic evaluation
    order exists."""


class DomainError(MathsEngineError):
    """Mathematical domain violation: division by zero, invalid log/sqrt
    domain, invalid percentage range, etc."""


class UnitMismatchError(MathsEngineError):
    """Two quantities cannot participate in the same arithmetic operation
    because their unit systems are incompatible (e.g. currency vs shares)."""


class CurrencyMismatchError(MathsEngineError):
    """Two facts carry incompatible currencies and no approved conversion
    relationship exists (never silently converted)."""


class ScaleMismatchError(MathsEngineError):
    """Two facts carry incompatible scales that cannot be normalized into
    the same unit system."""


class PeriodMismatchError(MathsEngineError):
    """Two facts carry incompatible reporting periods for the formula's
    period requirement."""


class InsufficientDataError(MathsEngineError):
    """A required dependency is missing / not numeric. Maps to BLOCKED."""


class AmbiguousEquationError(MathsEngineError):
    """The requested quantity has multiple mathematically possible
    solutions (multiple registered derivations that disagree). Maps to
    REVIEW_REQUIRED / AMBIGUOUS."""


class UnderdeterminedEquationError(MathsEngineError):
    """The registered relationship cannot yield a unique solution for the
    requested variable with the available facts."""


class NullValueError(MathsEngineError):
    """A dependency is present but carries a null / non-numeric value."""
